"""Network collector — fleet peer polling and latency measurement."""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

import httpx

_PING_RE = re.compile(r"in ([\d.]+)ms")

if TYPE_CHECKING:
    from buoy.config import BuoyConfig


class NetworkCollector:
    """Polls peer buoy instances for fleet stats and measures latency."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def collect(self) -> dict:
        """Poll all peers and return fleet status."""
        peers = self.config.network.peers
        if not peers:
            return {"peers": []}

        results = await asyncio.gather(
            *[self._poll_peer(p.name, p.url, p.tier) for p in peers],
            return_exceptions=True,
        )

        peer_data = []
        for r in results:
            if isinstance(r, Exception):
                continue
            peer_data.append(r)

        return {"peers": peer_data}

    async def _poll_peer(self, name: str, url: str, tier: str) -> dict:
        """Fetch /api/stats from a peer node."""
        if name == self.config.node.name:
            return {"name": name, "tier": tier, "online": True, "self": True}

        try:
            async with httpx.AsyncClient(timeout=4.0, verify=False) as client:
                r = await client.get(f"{url}/api/stats")
                if r.status_code == 200:
                    data = r.json()
                    return {
                        "name": name,
                        "tier": tier,
                        "online": True,
                        "data": data,
                    }
                return {"name": name, "tier": tier, "online": False}
        except Exception:
            return {"name": name, "tier": tier, "online": False}

    async def _tailscale_ping(self, peer_name: str) -> float | None:
        """Ping a peer via tailscale (WireGuard-level). Returns ms or None on failure.

        Uses nsenter to access the host's tailscale binary from inside the container.
        peer_name should be the peer's tailnet MagicDNS hostname (matches peer.name).
        """
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "-n",
                "--",
                "tailscale",
                "ping",
                "-c",
                "1",
                "--timeout",
                "3s",
                peer_name,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=6)
            if proc.returncode != 0:
                return None
            match = _PING_RE.search(stdout.decode())
            return round(float(match.group(1)), 1) if match else None
        except (TimeoutError, FileNotFoundError, PermissionError):
            return None

    async def measure_latency(self) -> list[dict]:
        """Measure latency to each peer using tailscale ping with HTTP fallback."""
        peers = self.config.network.peers
        results = []

        for peer in peers:
            if peer.name == self.config.node.name:
                results.append({"name": peer.name, "latency_ms": 0, "online": True})
                continue

            ms = await self._tailscale_ping(peer.name)
            if ms is not None:
                results.append({"name": peer.name, "latency_ms": ms, "online": True})
                continue

            # Fallback: HTTP timing to /api/health
            try:
                async with httpx.AsyncClient(timeout=4.0, verify=False) as client:
                    start = time.monotonic()
                    r = await client.get(f"{peer.url}/api/health")
                    elapsed = (time.monotonic() - start) * 1000

                    if r.status_code == 200:
                        results.append(
                            {"name": peer.name, "latency_ms": round(elapsed, 1), "online": True}
                        )
                    else:
                        results.append({"name": peer.name, "latency_ms": -1, "online": False})
            except Exception:
                results.append({"name": peer.name, "latency_ms": -1, "online": False})

        return results
