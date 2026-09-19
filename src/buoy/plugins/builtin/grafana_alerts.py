"""Grafana / Alertmanager plugin — firing alerts by severity."""

from __future__ import annotations

import asyncio
import base64
import json
import ssl
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_MAX_LISTED = 10
_SEVERITY_STATUS = {
    "critical": "error",
    "error": "error",
    "page": "error",
    "warning": "warn",
    "info": "info",
}
_SEVERITY_RANK = {"critical": 0, "error": 0, "page": 0, "warning": 1, "info": 2, "unknown": 3}


class GrafanaAlertsPlugin(Plugin):
    """Shows currently-firing alerts, bucketed by severity, from Alertmanager or Grafana."""

    manifest = PluginManifest(
        id="grafana_alerts",
        name="Alerts",
        icon="🚨",
        description="Firing alerts by severity",
        version="1.0.0",
        config_schema={
            "type": {"type": "string", "default": "alertmanager"},  # alertmanager | grafana
            "url": {"type": "string", "required": True},
            "token": {"type": "string"},  # Grafana service-account token (Bearer)
            "username": {"type": "string"},  # optional basic auth (AM behind a proxy)
            "password": {"type": "string"},
            "include_silenced": {"type": "boolean", "default": False},
            "verify_ssl": {"type": "boolean", "default": True},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "").rstrip("/")
        if not url:
            return PanelData(status="disabled", summary="Not configured")

        alert_type = self.config.get("type", "alertmanager")
        include_silenced = self.config.get("include_silenced", False)

        headers = {"Accept": "application/json"}
        token = self.config.get("token", "")
        username = self.config.get("username", "")
        password = self.config.get("password", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        elif username:
            creds = base64.b64encode(f"{username}:{password}".encode()).decode()
            headers["Authorization"] = f"Basic {creds}"

        ctx = None
        if not self.config.get("verify_ssl", True):
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        if alert_type == "alertmanager":
            path = "/api/v2/alerts?active=true&silenced=false&inhibited=false"
        elif alert_type == "grafana":
            path = (
                "/api/alertmanager/grafana/api/v2/alerts?active=true&silenced=false&inhibited=false"
            )
        else:
            return PanelData(status="error", summary=f"Unknown type: {alert_type!r}")

        try:
            alerts = await self._fetch(f"{url}{path}", headers, ctx)
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

        return self._make_panel(alerts, include_silenced)

    async def _fetch(self, url: str, headers: dict[str, str], ctx) -> list[dict]:
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._fetch_sync, url, headers, ctx)

    def _fetch_sync(self, url: str, headers: dict[str, str], ctx) -> list[dict]:
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            data = json.loads(resp.read())
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            return data.get("data", [])
        return []

    def _make_panel(self, alerts: list[dict], include_silenced: bool) -> PanelData:
        firing = []
        for alert in alerts:
            state = (alert.get("status") or {}).get("state", "")
            if state != "active" and not include_silenced:
                continue
            labels = alert.get("labels") or {}
            annotations = alert.get("annotations") or {}
            severity = str(labels.get("severity", "unknown")).lower()
            firing.append(
                {
                    "alertname": labels.get("alertname", "Alert"),
                    "severity": severity,
                    "instance": labels.get("instance") or labels.get("job") or "",
                    "summary": annotations.get("summary") or annotations.get("description") or "",
                    "startsAt": alert.get("startsAt", ""),
                    "generatorURL": alert.get("generatorURL", ""),
                }
            )

        by_severity: dict[str, int] = {}
        for a in firing:
            by_severity[a["severity"]] = by_severity.get(a["severity"], 0) + 1

        firing.sort(
            key=lambda a: (
                _SEVERITY_RANK.get(a["severity"], _SEVERITY_RANK["unknown"]),
                a["startsAt"],
            )
        )
        listed = firing[:_MAX_LISTED]

        total = len(firing)
        if total == 0:
            status = "ok"
            summary = "No firing alerts"
        else:
            worst_rank = min(
                _SEVERITY_RANK.get(sev, _SEVERITY_RANK["unknown"]) for sev in by_severity
            )
            status = "error" if worst_rank == 0 else "warn"
            summary = " · ".join(
                f"{count} {sev}"
                for sev, count in sorted(
                    by_severity.items(),
                    key=lambda kv: _SEVERITY_RANK.get(kv[0], _SEVERITY_RANK["unknown"]),
                )
            )

        return PanelData(
            status=status,
            summary=summary,
            detail={"total": total, "by_severity": by_severity, "alerts": listed},
        )

    def demo_data(self) -> PanelData:
        alerts = [
            {
                "alertname": "HighMemoryUsage",
                "severity": "warning",
                "instance": "node-3",
                "summary": "Memory usage above 90% for 15m",
                "startsAt": "2026-09-18T10:00:00Z",
                "generatorURL": "",
            },
            {
                "alertname": "DiskSpaceLow",
                "severity": "warning",
                "instance": "node-1",
                "summary": "Disk usage above 85%",
                "startsAt": "2026-09-18T09:30:00Z",
                "generatorURL": "",
            },
            {
                "alertname": "CertExpiringSoon",
                "severity": "info",
                "instance": "proxy-1",
                "summary": "TLS certificate expires in 10 days",
                "startsAt": "2026-09-18T08:00:00Z",
                "generatorURL": "",
            },
        ]
        return PanelData(
            status="warn",
            summary="2 warning · 1 info",
            detail={
                "total": 3,
                "by_severity": {"warning": 2, "info": 1},
                "alerts": alerts,
            },
        )

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        by_severity = d.get("by_severity") or {}
        alerts = d.get("alerts") or []

        if not alerts:
            return [panel.text("No firing alerts", status="dim")]

        blocks: list[dict] = [
            panel.badges(
                [
                    panel.badge(f"{sev}: {count}", status=_SEVERITY_STATUS.get(sev, "dim"))
                    for sev, count in sorted(
                        by_severity.items(),
                        key=lambda kv: _SEVERITY_RANK.get(kv[0], _SEVERITY_RANK["unknown"]),
                    )
                ]
            )
        ]
        blocks.append(
            panel.list_(
                [
                    panel.list_item(
                        a.get("alertname", "Alert"),
                        secondary=a.get("instance", "") or a.get("summary", ""),
                        status=_SEVERITY_STATUS.get(a.get("severity", ""), "dim"),
                        href=a.get("generatorURL") or None,
                    )
                    for a in alerts
                ]
            )
        )
        return blocks
