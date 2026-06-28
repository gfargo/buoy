"""WireGuard tunnel status plugin — peer handshake freshness and transfer stats."""

from __future__ import annotations

import asyncio
import time

from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class WireGuardPlugin(Plugin):
    """Shows WireGuard peer connectivity: handshake age, transfer stats, endpoint info."""

    manifest = PluginManifest(
        id="wireguard",
        name="WireGuard",
        icon="🔒",
        description="WireGuard tunnel peer status",
        version="1.0.0",
        config_schema={
            "interface": {"type": "string", "default": "wg0"},
            "stale_seconds": {"type": "integer", "default": 180},
        },
        refresh_interval=60,
    )

    async def collect(self) -> PanelData:
        iface = self.config.get("interface", "wg0")
        stale_seconds = int(self.config.get("stale_seconds", 180))

        dump = await self._read_wg_dump(iface)
        if dump is None:
            return PanelData(
                status="error",
                summary=f"Interface {iface} not found",
                detail={"interface": iface, "peers": []},
            )

        peers = _parse_peers(dump, stale_seconds)
        if not peers:
            return PanelData(
                status="ok",
                summary="0/0 peers up",
                detail={"interface": iface, "peers": []},
            )

        up = sum(1 for p in peers if not p["stale"])
        total = len(peers)
        status = "ok" if up == total else "warn"
        return PanelData(
            status=status,
            summary=f"{up}/{total} peers up",
            detail={"interface": iface, "peers": peers},
        )

    async def _read_wg_dump(self, iface: str) -> str | None:
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter", "-t", "1", "-m", "--", "wg", "show", iface, "dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            text = stdout.decode().strip()
            return text if text else None
        except (TimeoutError, FileNotFoundError):
            return None

    def frontend_js(self) -> str | None:
        return """
function render_wireguard(data) {
  const peers = data.detail.peers || [];
  if (!peers.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No peers configured</div>';
  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.5rem">';
  html += '<tr><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Peer</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Endpoint</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Handshake</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">RX/TX</th></tr>';
  peers.forEach(p => {
    const color = p.stale ? 'var(--red)' : 'var(--green)';
    const age = p.handshake_age < 0 ? 'never' : (p.handshake_age < 60 ? p.handshake_age + 's' : Math.floor(p.handshake_age/60) + 'm');
    html += '<tr><td style="padding:0.2rem 0.4rem;color:' + color + ';font-family:monospace">' + p.public_key + '</td>';
    html += '<td style="padding:0.2rem 0.4rem;color:var(--text-dim)">' + p.endpoint + '</td>';
    html += '<td style="padding:0.2rem 0.4rem;color:' + color + '">' + age + '</td>';
    html += '<td style="padding:0.2rem 0.4rem;color:var(--text-dim)">' + _fmt_bytes(p.rx) + ' / ' + _fmt_bytes(p.tx) + '</td></tr>';
  });
  html += '</table>';
  return html;
}
function _fmt_bytes(n) {
  if (n < 1024) return n + 'B';
  if (n < 1048576) return (n/1024).toFixed(1) + 'K';
  return (n/1048576).toFixed(1) + 'M';
}
"""


def _parse_peers(dump: str, stale_seconds: int) -> list[dict]:
    """Parse wg show <iface> dump output into a list of peer dicts."""
    now = int(time.time())
    peers = []
    lines = dump.strip().split("\n")
    # First line is interface row (4 fields); skip it
    for line in lines[1:]:
        fields = line.split("\t")
        if len(fields) < 8:
            continue
        public_key, _psk, endpoint, allowed_ips, latest_handshake, rx, tx, _keepalive = fields[:8]
        try:
            ts = int(latest_handshake)
        except ValueError:
            ts = 0
        age = (now - ts) if ts > 0 else -1
        stale = ts == 0 or age > stale_seconds
        peers.append({
            "public_key": public_key[:12] + "…",
            "endpoint": endpoint,
            "allowed_ips": allowed_ips,
            "handshake_age": age,
            "rx": int(rx) if rx.isdigit() else 0,
            "tx": int(tx) if tx.isdigit() else 0,
            "stale": stale,
        })
    return peers
