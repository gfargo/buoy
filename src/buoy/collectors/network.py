"""Network collector — fleet peer polling and latency measurement."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import httpx

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

    async def measure_latency(self) -> list[dict]:
        """Measure HTTP round-trip latency to each peer."""
        peers = self.config.network.peers
        results = []

        for peer in peers:
            if peer.name == self.config.node.name:
                results.append({"name": peer.name, "latency_ms": 0, "online": True})
                continue

            try:
                async with httpx.AsyncClient(timeout=4.0, verify=False) as client:
                    import time

                    start = time.monotonic()
                    r = await client.get(f"{peer.url}/api/health")
                    elapsed = (time.monotonic() - start) * 1000

                    if r.status_code == 200:
                        results.append(
                            {
                                "name": peer.name,
                                "latency_ms": round(elapsed, 1),
                                "online": True,
                            }
                        )
                    else:
                        results.append({"name": peer.name, "latency_ms": -1, "online": False})
            except Exception:
                results.append({"name": peer.name, "latency_ms": -1, "online": False})

        return results
