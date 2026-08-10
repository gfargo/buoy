"""UptimeKuma plugin — service health badges from status page API."""

from __future__ import annotations

import json
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class UptimeKumaPlugin(Plugin):
    """Shows service health badges from UptimeKuma."""

    manifest = PluginManifest(
        id="uptime_kuma",
        name="Uptime",
        icon="🟢",
        description="Service health monitoring",
        version="1.0.0",
        config_schema={"url": {"type": "string", "required": True}},
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        url = self.config.get("url", "")
        if not url:
            return PanelData(status="disabled", summary="Not configured")

        try:
            api_url = f"{url.rstrip('/')}/api/status-page/heartbeat/default"
            req = urllib.request.Request(api_url, headers={"Accept": "application/json"})
            with urllib.request.urlopen(req, timeout=8) as resp:
                data = json.loads(resp.read())

            heartbeats = data.get("heartbeatList", {})
            monitors = []
            up_count = 0
            down_count = 0

            for monitor_id, beats in heartbeats.items():
                if not beats:
                    continue
                latest = beats[-1]
                is_up = latest.get("status") == 1
                name = latest.get("msg", f"Monitor {monitor_id}")
                monitors.append({"name": name, "up": is_up})
                if is_up:
                    up_count += 1
                else:
                    down_count += 1

            total = up_count + down_count
            status = "ok" if down_count == 0 else "error"
            summary = f"{up_count}/{total} up" if total else "No monitors"

            return PanelData(status=status, summary=summary, detail={"monitors": monitors})
        except Exception as e:
            return PanelData(status="error", summary="Unreachable", detail={"error": str(e)})

    def render(self, data: PanelData) -> list[dict] | None:
        monitors = data.detail.get("monitors") or []
        if not monitors:
            return [panel.text("No monitors", status="dim")]

        return [
            panel.badges(
                [
                    panel.badge(m.get("name", ""), status="ok" if m.get("up") else "error")
                    for m in monitors
                ]
            )
        ]
