"""WireGuard tunnel status plugin — peer handshake freshness and transfer stats."""

from __future__ import annotations

import asyncio
import time

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest
from buoy.subprocess_utils import communicate


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
                "nsenter",
                "-t",
                "1",
                "-m",
                "--",
                "wg",
                "show",
                iface,
                "dump",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, _ = await communicate(proc, timeout=5)
            text = stdout.decode().strip()
            return text if text else None
        except (TimeoutError, FileNotFoundError):
            return None

    def demo_data(self) -> PanelData:
        peers = [
            {
                "public_key": "aBcDeFgHiJkL…",
                "endpoint": "203.0.113.5:51820",
                "allowed_ips": "10.10.0.2/32",
                "handshake_age": 42,
                "rx": 184320,
                "tx": 92160,
                "stale": False,
            },
            {
                "public_key": "mNoPqRsTuVwX…",
                "endpoint": "198.51.100.9:51820",
                "allowed_ips": "10.10.0.3/32",
                "handshake_age": 610,
                "rx": 20480,
                "tx": 10240,
                "stale": True,
            },
        ]
        return PanelData(
            status="warn", summary="1/2 peers up", detail={"interface": "wg0", "peers": peers}
        )

    def render(self, data: PanelData) -> list[dict] | None:
        peers = data.detail.get("peers") or []
        if not peers:
            return [panel.text("No peers configured", status="dim")]

        rows = []
        for p in peers:
            stale = p.get("stale")
            status = "error" if stale else "ok"
            age = p.get("handshake_age", -1)
            if age < 0:
                age_label = "never"
            elif age < 60:
                age_label = f"{age}s"
            else:
                age_label = f"{age // 60}m"
            rows.append(
                [
                    panel.cell(p.get("public_key", ""), status=status, mono=True),
                    panel.cell(p.get("endpoint", ""), status="dim"),
                    panel.cell(age_label, status=status),
                    panel.cell(
                        f"{_fmt_bytes(p.get('rx', 0))} / {_fmt_bytes(p.get('tx', 0))}", status="dim"
                    ),
                ]
            )
        return [panel.table(["Peer", "Endpoint", "Handshake", "RX/TX"], rows)]


def _fmt_bytes(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1048576:
        return f"{n / 1024:.1f}K"
    return f"{n / 1048576:.1f}M"


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
        peers.append(
            {
                "public_key": public_key[:12] + "…",
                "endpoint": endpoint,
                "allowed_ips": allowed_ips,
                "handshake_age": age,
                "rx": int(rx) if rx.isdigit() else 0,
                "tx": int(tx) if tx.isdigit() else 0,
                "stale": stale,
            }
        )
    return peers
