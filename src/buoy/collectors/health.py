"""Static service health checker — polls operator-configured non-Docker URLs."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING

import httpx

if TYPE_CHECKING:
    from buoy.config import BuoyConfig, StaticService

logger = logging.getLogger("buoy.collectors.health")


class StaticHealthChecker:
    """Polls the ``health_check`` URL of each configured static service."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    def _verify(self, entry: StaticService) -> bool:
        """Per-entry ``verify_ssl`` wins; absent (``None``) inherits network.verify_ssl."""
        if entry.verify_ssl is not None:
            return entry.verify_ssl
        return self.config.network.verify_ssl

    async def _check_one(self, entry: StaticService) -> tuple[str, dict]:
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(
                timeout=5.0, verify=self._verify(entry), follow_redirects=True
            ) as client:
                r = await client.get(entry.health_check)
                latency_ms = round((time.monotonic() - start) * 1000, 1)
                status = "ok" if 200 <= r.status_code < 400 else "error"
                return entry.name, {
                    "status": status,
                    "latency_ms": latency_ms,
                    "code": r.status_code,
                }
        except Exception:
            logger.debug("health check failed for static service '%s'", entry.name, exc_info=True)
            return entry.name, {"status": "error"}

    async def check_all(self) -> dict[str, dict]:
        """Check every static service with a configured ``health_check`` URL."""
        entries = [e for e in self.config.services.static if e.health_check]
        if not entries:
            return {}

        results = await asyncio.gather(*[self._check_one(e) for e in entries])
        return dict(results)
