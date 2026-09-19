"""Cloudflare Tunnel plugin — connector health and active connections."""

from __future__ import annotations

import asyncio
import json
import urllib.error
import urllib.request

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_API_BASE = "https://api.cloudflare.com/client/v4"
_PER_PAGE = 50


def _scrub_token(text: str, token: str) -> str:
    """Remove a configured API token from error text before it reaches the panel."""
    if not token:
        return text
    return text.replace(token, "***")


class CloudflareTunnelPlugin(Plugin):
    """Shows Cloudflare Tunnel connector health: status, connections, colos."""

    manifest = PluginManifest(
        id="cloudflare_tunnel",
        name="Cloudflare Tunnel",
        icon="☁️",
        description="Connector health, active connections",
        version="1.0.0",
        config_schema={
            "account_id": {"type": "string", "required": True},
            "api_token": {"type": "string", "required": True},
            "tunnels": {"type": "array", "default": []},
            "min_connections": {"type": "integer", "default": 0},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        account_id = self.config.get("account_id", "")
        api_token = self.config.get("api_token", "")

        if not account_id or not api_token:
            return PanelData(status="disabled", summary="Not configured")

        loop = asyncio.get_running_loop()
        try:
            return await loop.run_in_executor(None, self._fetch, account_id, api_token)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return PanelData(
                    status="error", summary="Auth failed", detail={"error": f"HTTP {e.code}"}
                )
            return PanelData(
                status="error",
                summary="Unreachable",
                detail={"error": _scrub_token(str(e), api_token)},
            )
        except Exception as e:
            return PanelData(
                status="error",
                summary="Unreachable",
                detail={"error": _scrub_token(str(e), api_token)},
            )

    def _fetch(self, account_id: str, api_token: str) -> PanelData:
        url = f"{_API_BASE}/accounts/{account_id}/cfd_tunnel?is_deleted=false&per_page={_PER_PAGE}"
        req = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {api_token}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=8) as resp:
            data = json.loads(resp.read())

        raw_tunnels = data.get("result") or []
        result_info = data.get("result_info") or {}

        tunnels = _parse_tunnels(raw_tunnels)

        name_filter = self.config.get("tunnels") or []
        if name_filter:
            wanted = set(name_filter)
            tunnels = [t for t in tunnels if t["name"] in wanted or t["id"] in wanted]

        min_connections = int(self.config.get("min_connections", 0))
        truncated = result_info.get("total_count", len(raw_tunnels)) > len(raw_tunnels)

        return _make_panel(tunnels, min_connections, truncated)

    def demo_data(self) -> PanelData:
        tunnels = [
            {
                "id": "a1b2c3d4",
                "name": "prod-web",
                "status": "healthy",
                "connections": 4,
                "colos": ["DFW", "IAD"],
                "version": "2024.10.1",
            },
            {
                "id": "e5f6a7b8",
                "name": "staging-api",
                "status": "degraded",
                "connections": 1,
                "colos": ["DFW"],
                "version": "2024.10.1",
            },
        ]
        return _make_panel(tunnels, min_connections=0, truncated=False)

    def render(self, data: PanelData) -> list[dict] | None:
        d = data.detail or {}
        tunnels = d.get("tunnels") or []
        if not tunnels:
            return [panel.text("No tunnels", status="dim")]

        row_status_map = {"healthy": "ok", "degraded": "warn", "inactive": "dim", "down": "error"}
        rows = []
        for t in tunnels:
            status = row_status_map.get(t.get("status"), "dim")
            if status == "ok" and t.get("below_min"):
                status = "warn"
            rows.append(
                [
                    panel.cell(t.get("name", ""), status=status),
                    panel.cell(t.get("status", ""), status=status),
                    panel.cell(str(t.get("connections", 0)), status="dim"),
                    panel.cell(", ".join(t.get("colos") or []), status="dim"),
                ]
            )
        blocks = [panel.table(["Tunnel", "Status", "Conns", "Colos"], rows)]
        if d.get("truncated"):
            blocks.append(
                panel.text("More tunnels not shown (increase per-page limit)", status="dim")
            )
        return blocks


def _parse_tunnels(raw_tunnels: list[dict]) -> list[dict]:
    tunnels = []
    for t in raw_tunnels:
        if t.get("deleted_at"):
            continue
        connections = t.get("connections") or []
        active = [c for c in connections if not c.get("is_pending_reconnect")]
        colos = sorted({c.get("colo_name") for c in active if c.get("colo_name")})
        version = next((c.get("client_version") for c in active if c.get("client_version")), None)
        tunnels.append(
            {
                "id": t.get("id", ""),
                "name": t.get("name", ""),
                "status": t.get("status", "inactive"),
                "connections": len(active),
                "colos": colos,
                "version": version,
            }
        )
    return tunnels


def _make_panel(tunnels: list[dict], min_connections: int, truncated: bool) -> PanelData:
    if not tunnels:
        return PanelData(
            status="ok",
            summary="No tunnels",
            detail={"tunnels": [], "healthy": 0, "connections": 0, "truncated": truncated},
        )

    healthy = 0
    has_down = False
    has_warn = False
    total_connections = 0

    for t in tunnels:
        status = t.get("status")
        connections = t.get("connections", 0)
        total_connections += connections
        below_min = connections < min_connections
        t["below_min"] = below_min

        if status == "down":
            has_down = True
        elif status in ("degraded", "inactive") or below_min:
            has_warn = True
        else:
            healthy += 1

    if has_down:
        panel_status = "error"
    elif has_warn:
        panel_status = "warn"
    else:
        panel_status = "ok"

    summary = f"{healthy}/{len(tunnels)} healthy · {total_connections} conns"

    return PanelData(
        status=panel_status,
        summary=summary,
        detail={
            "tunnels": tunnels,
            "healthy": healthy,
            "connections": total_connections,
            "truncated": truncated,
        },
    )
