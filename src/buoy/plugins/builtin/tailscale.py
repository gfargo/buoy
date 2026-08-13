"""Tailscale plugin — tailnet peer status and connection types."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest
from buoy.subprocess_utils import communicate


class TailscalePlugin(Plugin):
    """Shows tailnet peer online state, connection type, and last-seen time."""

    manifest = PluginManifest(
        id="tailscale",
        name="Tailscale",
        icon="🔗",
        description="Tailnet peer status",
        version="1.0.0",
        config_schema={},
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        data = await self._tailscale_status()
        if data is None:
            return PanelData(
                status="error", summary="Tailscale not running", detail={"error": "unavailable"}
            )

        backend_state = data.get("BackendState", "")
        if backend_state and backend_state != "Running":
            return PanelData(
                status="error",
                summary=f"Tailscale: {backend_state}",
                detail={"backend_state": backend_state},
            )

        peers_raw = data.get("Peer", {}) or {}
        peers = []
        for peer in peers_raw.values():
            online = bool(peer.get("Online", False))
            cur_addr = peer.get("CurAddr", "")
            relay = peer.get("Relay", "")
            conn_type = "direct" if cur_addr else ("relay" if relay else "unknown")
            last_seen = peer.get("LastSeen", "")
            # For online peers LastSeen is often zero/meaningless — omit
            if online:
                last_seen = ""
            name = peer.get("HostName") or peer.get("DNSName", "").split(".")[0] or "unknown"
            peers.append(
                {
                    "name": name,
                    "online": online,
                    "conn_type": conn_type,
                    "last_seen": last_seen,
                    "exit_node": bool(peer.get("ExitNode", False)),
                }
            )

        total = len(peers)
        online_count = sum(1 for p in peers if p["online"])
        exit_node = next((p["name"] for p in peers if p["exit_node"]), None)

        if total == 0:
            status, summary = "ok", "No peers"
        elif online_count == total:
            status = "ok"
            summary = f"{online_count}/{total} peers online"
        else:
            status = "warn"
            summary = f"{online_count}/{total} peers online"

        if exit_node:
            summary += f" · exit: {exit_node}"

        detail: dict = {"peers": peers, "backend_state": backend_state}
        if exit_node:
            detail["exit_node"] = exit_node

        return PanelData(status=status, summary=summary, detail=detail)

    async def _tailscale_status(self) -> dict | None:
        """Run tailscale status --json via nsenter. Returns parsed dict or None."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "-n",
                "--",
                "tailscale",
                "status",
                "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await communicate(proc, timeout=6)
            if proc.returncode != 0 or not stdout:
                return None
            return json.loads(stdout)
        except (TimeoutError, FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None

    def render(self, data: PanelData) -> list[dict] | None:
        peers = data.detail.get("peers") or []
        if not peers:
            return [panel.text("No peers", status="dim")]

        conn_status = {"direct": "ok", "relay": "warn"}
        rows = []
        for p in peers[:20]:
            name = p.get("name", "")
            if p.get("exit_node"):
                name += " (exit)"
            conn_type = p.get("conn_type", "unknown")
            seen_label = _format_relative_age(p.get("last_seen") or "")
            rows.append(
                [
                    panel.cell(name),
                    panel.cell(
                        conn_type if conn_type != "unknown" else "—",
                        status=conn_status.get(conn_type),
                    ),
                    panel.cell(
                        "up" if p.get("online") else seen_label or "down",
                        status="ok" if p.get("online") else "error",
                    ),
                ]
            )
        return [panel.table(["Peer", "Conn", "Status"], rows)]


def _format_relative_age(last_seen: str) -> str:
    """Format an RFC3339 LastSeen timestamp as a short relative age (e.g. "3m ago").

    Falls back to "" on anything unparseable so a malformed/missing timestamp
    degrades gracefully instead of surfacing a raw value.
    """
    if not last_seen:
        return ""
    try:
        seen = datetime.fromisoformat(last_seen.replace("Z", "+00:00"))
        if seen.tzinfo is None:
            seen = seen.replace(tzinfo=UTC)
        seconds = max(0, (datetime.now(UTC) - seen).total_seconds())
    except (ValueError, OverflowError):
        return ""

    if seconds < 60:
        return "just now"
    minutes = int(seconds // 60)
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    return f"{days}d ago"
