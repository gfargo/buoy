"""Tailscale plugin — tailnet peer status and connection types."""

from __future__ import annotations

import asyncio
import json

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


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
            return PanelData(status="error", summary="Tailscale not running", detail={"error": "unavailable"})

        backend_state = data.get("BackendState", "")
        if backend_state and backend_state != "Running":
            return PanelData(status="error", summary=f"Tailscale: {backend_state}", detail={"backend_state": backend_state})

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
            peers.append({
                "name": name,
                "online": online,
                "conn_type": conn_type,
                "last_seen": last_seen,
                "exit_node": bool(peer.get("ExitNode", False)),
            })

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
                "nsenter", "-t", "1", "-m", "-n", "--",
                "tailscale", "status", "--json",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6)
            if proc.returncode != 0 or not stdout:
                return None
            return json.loads(stdout)
        except (TimeoutError, FileNotFoundError, PermissionError, json.JSONDecodeError):
            return None

    def frontend_js(self) -> str | None:
        return """
function render_tailscale(data) {
  const peers = (data.detail && data.detail.peers) || [];
  if (!peers.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No peers</div>';
  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.5rem">';
  html += '<tr><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Peer</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Conn</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Status</th></tr>';
  peers.slice(0, 20).forEach(p => {
    const dot = p.online ? '<span style="color:var(--green)">●</span>' : '<span style="color:var(--red)">●</span>';
    const conn = p.conn_type === 'direct' ? '<span style="color:var(--green)">direct</span>' : p.conn_type === 'relay' ? '<span style="color:var(--amber)">relay</span>' : '<span style="color:var(--text-dim)">—</span>';
    const seen = p.last_seen ? '<span style="color:var(--text-dim)">' + new Date(p.last_seen).toLocaleTimeString() + '</span>' : '';
    html += '<tr><td style="padding:0.2rem 0.4rem;color:var(--text)">' + p.name + (p.exit_node ? ' <span style="color:var(--amber)">exit</span>' : '') + '</td><td style="padding:0.2rem 0.4rem">' + conn + '</td><td style="padding:0.2rem 0.4rem">' + dot + seen + '</td></tr>';
  });
  html += '</table>';
  return html;
}
"""
