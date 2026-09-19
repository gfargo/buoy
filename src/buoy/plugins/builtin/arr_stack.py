"""*arr stack plugin — Sonarr/Radarr/Prowlarr/Bazarr queue, wanted/missing, and health."""

from __future__ import annotations

import asyncio
import json
import ssl
import urllib.error
import urllib.request
from typing import Any

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_MAX_HEALTH = 10

# key, display name, API base path, queue-status path (None if the service has
# no queue concept), wanted/missing path(s) to sum across
_SERVICES: tuple[tuple[str, str, str, str | None, tuple[str, ...]], ...] = (
    ("sonarr", "Sonarr", "/api/v3", "/queue/status", ("/wanted/missing?pageSize=1",)),
    ("radarr", "Radarr", "/api/v3", "/queue/status", ("/wanted/missing?pageSize=1",)),
    ("prowlarr", "Prowlarr", "/api/v1", None, ()),
    ("bazarr", "Bazarr", "/api", None, ("/episodes/wanted", "/movies/wanted")),
)


def _count(payload: Any) -> int:
    """Extract a record count from an *arr JSON payload, tolerating shape drift."""
    if isinstance(payload, dict):
        for key in ("totalRecords", "totalCount", "total"):
            if key in payload:
                return int(payload[key])
        for key in ("records", "data"):
            if isinstance(payload.get(key), list):
                return len(payload[key])
        return 0
    if isinstance(payload, list):
        return len(payload)
    return 0


class ArrStackPlugin(Plugin):
    """Shows queue depth, wanted/missing counts, and health warnings across the *arr stack."""

    manifest = PluginManifest(
        id="arr_stack",
        name="*arr Stack",
        icon="🍿",
        description="Sonarr/Radarr/Prowlarr/Bazarr queue depth, wanted/missing, and health",
        version="1.0.0",
        config_schema={
            "sonarr_url": {"type": "string"},
            "sonarr_api_key": {"type": "string"},
            "radarr_url": {"type": "string"},
            "radarr_api_key": {"type": "string"},
            "prowlarr_url": {"type": "string"},
            "prowlarr_api_key": {"type": "string"},
            "bazarr_url": {"type": "string"},
            "bazarr_api_key": {"type": "string"},
            "queue_warn_threshold": {"type": "integer", "default": 20},
            "missing_warn_threshold": {"type": "integer", "default": 50},
            "verify_ssl": {"type": "boolean", "default": True},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        configured = []
        for key, name, api_base, queue_path, missing_paths in _SERVICES:
            url = self.config.get(f"{key}_url", "") or ""
            api_key = self.config.get(f"{key}_api_key", "") or ""
            if not url and not api_key:
                continue
            configured.append((key, name, url, api_key, api_base, queue_path, missing_paths))

        if not configured:
            return PanelData(status="disabled", summary="Not configured")

        verify_ssl = self.config.get("verify_ssl", True)
        ctx = None
        if not verify_ssl:
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE

        loop = asyncio.get_running_loop()
        results = await asyncio.gather(
            *(loop.run_in_executor(None, self._collect_service, spec, ctx) for spec in configured),
            return_exceptions=True,
        )

        services = []
        for spec, result in zip(configured, results):
            key, name = spec[0], spec[1]
            if isinstance(result, BaseException):
                services.append(
                    {
                        "key": key,
                        "name": name,
                        "status": "error",
                        "error": str(result),
                        "queue": None,
                        "missing": None,
                        "health": [],
                        "indexer_warnings": 0,
                    }
                )
            else:
                services.append(result)

        return self._aggregate(services)

    def _collect_service(
        self,
        spec: tuple[str, str, str, str, str, str | None, tuple[str, ...]],
        ctx: ssl.SSLContext | None,
    ) -> dict[str, Any]:
        key, name, url, api_key, api_base, queue_path, missing_paths = spec
        if not url or not api_key:
            return {
                "key": key,
                "name": name,
                "status": "error",
                "error": "missing url or api key",
                "queue": None,
                "missing": None,
                "health": [],
                "indexer_warnings": 0,
            }

        base = f"{url.rstrip('/')}{api_base}"
        headers = {"X-Api-Key": api_key, "Accept": "application/json"}

        def get(path: str) -> Any:
            req = urllib.request.Request(f"{base}{path}", headers=headers)
            with urllib.request.urlopen(req, timeout=5, context=ctx) as resp:
                return json.loads(resp.read())

        queue = None
        if queue_path:
            try:
                queue = _count(get(queue_path))
            except urllib.error.HTTPError as e:
                if e.code != 404:
                    raise
                queue = _count(get("/queue?pageSize=1"))

        missing = None
        if missing_paths:
            missing = sum(_count(get(p)) for p in missing_paths)

        health: list[dict[str, str]] = []
        for entry in get("/health") or []:
            htype = entry.get("type", "")
            if htype in ("warning", "error"):
                health.append(
                    {
                        "name": name,
                        "source": entry.get("source", ""),
                        "message": entry.get("message", ""),
                        "type": htype,
                    }
                )

        indexer_warnings = 0
        if key == "prowlarr":
            statuses = get("/indexerstatus") or []
            indexer_warnings = sum(
                1 for s in statuses if s.get("disabledTill") or s.get("mostRecentFailure")
            )

        return {
            "key": key,
            "name": name,
            "status": "ok",
            "error": None,
            "queue": queue,
            "missing": missing,
            "health": health,
            "indexer_warnings": indexer_warnings,
        }

    def _aggregate(self, services: list[dict[str, Any]]) -> PanelData:
        queue_warn = self.config.get("queue_warn_threshold", 20)
        missing_warn = self.config.get("missing_warn_threshold", 50)

        reachable = [s for s in services if s.get("status") != "error"]
        queue_total = sum(s.get("queue") or 0 for s in reachable)
        missing_total = sum(s.get("missing") or 0 for s in reachable)
        indexer_warnings = sum(s.get("indexer_warnings") or 0 for s in reachable)

        health: list[dict[str, str]] = []
        for s in reachable:
            health.extend(s.get("health") or [])
        health = health[:_MAX_HEALTH]

        unreachable = [s["name"] for s in services if s.get("status") == "error"]
        has_error_health = any(h["type"] == "error" for h in health)
        has_warn_health = any(h["type"] == "warning" for h in health)

        if unreachable or has_error_health:
            status = "error"
        elif (
            has_warn_health
            or indexer_warnings
            or queue_total > queue_warn
            or missing_total > missing_warn
        ):
            status = "warn"
        else:
            status = "ok"

        summary = f"{queue_total} queued · {missing_total} missing"
        if health:
            n = len(health)
            summary += f" · {n} health warning{'s' if n != 1 else ''}"
        for name in unreachable:
            summary += f" · {name} unreachable"

        return PanelData(
            status=status,
            summary=summary,
            detail={
                "services": services,
                "queue_total": queue_total,
                "missing_total": missing_total,
                "health": health,
                "thresholds": {"queue": queue_warn, "missing": missing_warn},
            },
        )

    def demo_data(self) -> PanelData:
        services = [
            {
                "key": "sonarr",
                "name": "Sonarr",
                "status": "ok",
                "error": None,
                "queue": 3,
                "missing": 12,
                "health": [],
                "indexer_warnings": 0,
            },
            {
                "key": "radarr",
                "name": "Radarr",
                "status": "ok",
                "error": None,
                "queue": 0,
                "missing": 5,
                "health": [
                    {
                        "name": "Radarr",
                        "source": "RemotePathMappingCheck",
                        "message": "Remote path mapping does not exist",
                        "type": "warning",
                    }
                ],
                "indexer_warnings": 0,
            },
            {
                "key": "prowlarr",
                "name": "Prowlarr",
                "status": "ok",
                "error": None,
                "queue": None,
                "missing": None,
                "health": [],
                "indexer_warnings": 0,
            },
            {
                "key": "bazarr",
                "name": "Bazarr",
                "status": "ok",
                "error": None,
                "queue": None,
                "missing": 8,
                "health": [],
                "indexer_warnings": 0,
            },
        ]
        return self._aggregate(services)

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        services = d.get("services") or []
        health = d.get("health") or []

        rows = []
        for s in services:
            if s.get("status") == "error":
                rows.append(
                    [
                        panel.cell(s.get("name", "")),
                        panel.cell("—", status="error"),
                        panel.cell("—", status="error"),
                        panel.cell(s.get("error") or "unreachable", status="error", truncate=True),
                    ]
                )
                continue

            queue = s.get("queue")
            missing = s.get("missing")
            service_health = s.get("health") or []
            if any(h.get("type") == "error" for h in service_health):
                health_status = "error"
            elif service_health:
                health_status = "warn"
            else:
                health_status = "ok"
            health_label = f"{len(service_health)} warning(s)" if service_health else "OK"

            rows.append(
                [
                    panel.cell(s.get("name", "")),
                    panel.cell(str(queue) if queue is not None else "—"),
                    panel.cell(str(missing) if missing is not None else "—"),
                    panel.cell(health_label, status=health_status),
                ]
            )

        blocks: list[dict] = [panel.table(["Service", "Queue", "Missing", "Health"], rows)]

        if health:
            blocks.append(
                panel.list_(
                    [
                        panel.list_item(
                            h.get("message", ""),
                            secondary=f"{h.get('name', '')} · {h.get('source', '')}",
                            status="error" if h.get("type") == "error" else "warn",
                        )
                        for h in health
                    ]
                )
            )
        else:
            blocks.append(panel.text("All healthy", status="dim"))

        return blocks
