"""Network collector — fleet peer polling, latency measurement, and
per-interface throughput sampled from /proc/net/dev."""

from __future__ import annotations

import asyncio
import logging
import platform
import re
import time
from typing import TYPE_CHECKING

import httpx

from buoy.subprocess_utils import communicate

_PING_RE = re.compile(r"in ([\d.]+)ms")

# Prefer /proc/1/net/{dev,route} (PID 1's netns — the host's, when the
# container runs with pid: host) over this process's own /proc/net/{dev,route}
# (the container's veth when netns isn't shared). Both files in a pair must
# come from the same namespace, so paths are kept index-aligned.
_NET_DEV_PATHS = ("/proc/1/net/dev", "/proc/net/dev")
_ROUTE_PATHS = ("/proc/1/net/route", "/proc/net/route")

# Loopback and container/bridge virtual interfaces excluded by default.
_SKIP_IFACE_RE = re.compile(r"^(lo|veth|docker|br-|virbr)")

_MIN_SAMPLE_INTERVAL = 1.0  # seconds; serializes bursts of near-simultaneous callers

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.collectors.network")


class NetworkCollector:
    """Polls peer buoy instances for fleet stats, measures latency, and
    samples per-interface network throughput."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._is_linux = platform.system() == "Linux"
        # (monotonic timestamp, {iface: {rx_bytes, rx_errs, rx_drop, tx_bytes, tx_errs, tx_drop}})
        self._last_net_sample: tuple[float, dict[str, dict[str, int]]] | None = None
        self._last_net_result: dict | None = None
        self._net_lock = asyncio.Lock()

    async def collect(self) -> dict:
        """Poll all peers and return fleet status."""
        peers = self.config.network.peers
        if not peers:
            return {"peers": []}

        results = await asyncio.gather(
            *[self._poll_peer(p.name, p.url, p.tier, self._peer_verify(p)) for p in peers],
            return_exceptions=True,
        )

        peer_data = []
        for r in results:
            if isinstance(r, Exception):
                continue
            peer_data.append(r)

        return {"peers": peer_data}

    def _peer_verify(self, peer) -> bool:
        """Return the effective TLS-verify flag for a peer.

        A peer-level ``verify_ssl`` wins over the network default; absent
        (``None``) means inherit from ``network.verify_ssl``.
        """
        if peer.verify_ssl is not None:
            return peer.verify_ssl
        return self.config.network.verify_ssl

    async def _poll_peer(self, name: str, url: str, tier: str, verify: bool = True) -> dict:
        """Fetch /api/stats from a peer node."""
        if name == self.config.node.name:
            return {"name": name, "tier": tier, "online": True, "self": True}

        try:
            async with httpx.AsyncClient(timeout=4.0, verify=verify) as client:
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
            logger.debug("failed to poll peer '%s'", name, exc_info=True)
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
            stdout, _ = await communicate(proc, timeout=6)
            if proc.returncode != 0:
                return None
            match = _PING_RE.search(stdout.decode())
            return round(float(match.group(1)), 1) if match else None
        except (TimeoutError, FileNotFoundError, PermissionError):
            return None

    async def measure_latency(self) -> list[dict]:
        """Measure latency to each peer using tailscale ping with HTTP fallback.

        Peers are measured concurrently (like ``collect()`` above) rather than
        one at a time — a tailscale ping can take up to 3s and the HTTP
        fallback up to 4s, so measuring serially could overrun this method's
        own polling interval (``refresh.fleet_interval``, default 15s) with
        just a handful of offline peers.
        """
        peers = self.config.network.peers
        return list(await asyncio.gather(*[self._measure_peer_latency(p) for p in peers]))

    async def _measure_peer_latency(self, peer) -> dict:
        """Measure latency to a single peer (tailscale ping, then HTTP fallback)."""
        if peer.name == self.config.node.name:
            return {"name": peer.name, "latency_ms": 0, "online": True}

        ms = await self._tailscale_ping(peer.name)
        if ms is not None:
            return {"name": peer.name, "latency_ms": ms, "online": True}

        # Fallback: HTTP timing to /api/health
        try:
            async with httpx.AsyncClient(timeout=4.0, verify=self._peer_verify(peer)) as client:
                start = time.monotonic()
                r = await client.get(f"{peer.url}/api/health")
                elapsed = (time.monotonic() - start) * 1000

                if r.status_code == 200:
                    return {"name": peer.name, "latency_ms": round(elapsed, 1), "online": True}
                return {"name": peer.name, "latency_ms": -1, "online": False}
        except Exception:
            logger.debug("failed to measure HTTP latency to peer '%s'", peer.name, exc_info=True)
            return {"name": peer.name, "latency_ms": -1, "online": False}

    # ── Interface throughput ──────────────────────────────────────────────────

    async def collect_throughput(self) -> dict:
        """Sample per-interface rx/tx rates from /proc/net/dev.

        Returns ``{"net": {...}}`` (mergeable into the /api/stats response,
        like ``DiskCollector.collect_summary``) or ``{}`` when unavailable
        (non-Linux, or /proc/net/dev unreadable).

        Rates are computed as a delta against the previous sample, the same
        pattern as ``SystemCollector._read_cpu``: the first sample after
        startup has no prior point to diff against, so it reports 0 rather
        than a fabricated spike. Sampling is serialized behind a lock and
        throttled to once per ``_MIN_SAMPLE_INTERVAL`` so two callers
        (``api_stats`` and ``_stats_loop``) landing microseconds apart share
        one snapshot instead of dividing by a near-zero elapsed time.
        """
        if not self._is_linux:
            return {}

        async with self._net_lock:
            now = time.monotonic()
            if (
                self._last_net_sample is not None
                and self._last_net_result is not None
                and now - self._last_net_sample[0] < _MIN_SAMPLE_INTERVAL
            ):
                return self._last_net_result

            try:
                read = self._read_net_dev_and_route()
            except Exception:
                logger.debug("failed to read network throughput", exc_info=True)
                return {}
            if read is None:
                return {}
            dev_text, route_text, source = read

            cur = {
                name: sample
                for name, sample in self._parse_net_dev(dev_text).items()
                if self._included(name)
            }

            if self._last_net_sample is not None:
                prev_ts, prev_samples = self._last_net_sample
                elapsed = now - prev_ts
            else:
                prev_samples, elapsed = {}, 0.0

            rates = self._compute_rates(prev_samples, cur, elapsed)

            primary = self._parse_default_route(route_text)
            if primary is None or primary not in cur:
                primary = self._pick_highest_total(cur)

            zero_rate = {"rx_bytes_per_sec": 0.0, "tx_bytes_per_sec": 0.0}
            interfaces = []
            for name, sample in sorted(cur.items()):
                rate = rates.get(name, zero_rate)
                interfaces.append(
                    {
                        "name": name,
                        "rx_bytes": sample["rx_bytes"],
                        "tx_bytes": sample["tx_bytes"],
                        "rx_bytes_per_sec": round(rate["rx_bytes_per_sec"], 1),
                        "tx_bytes_per_sec": round(rate["tx_bytes_per_sec"], 1),
                        "rx_errors": sample["rx_errors"],
                        "tx_errors": sample["tx_errors"],
                        "rx_dropped": sample["rx_dropped"],
                        "tx_dropped": sample["tx_dropped"],
                    }
                )

            primary_rate = rates.get(primary, zero_rate)
            result = {
                "net": {
                    "primary": primary,
                    "rx_bytes_per_sec": round(primary_rate["rx_bytes_per_sec"], 1),
                    "tx_bytes_per_sec": round(primary_rate["tx_bytes_per_sec"], 1),
                    "source": source,
                    "interfaces": interfaces,
                }
            }

            # Rebuild from the current sample each cycle (rather than merging
            # into the old dict) so an interface that disappeared (e.g. a
            # short-lived veth) doesn't linger forever as a stale rate source.
            self._last_net_sample = (now, cur)
            self._last_net_result = result
            return result

    def _read_net_dev_and_route(self) -> tuple[str, str, str] | None:
        """Read the first readable (dev, route) pair from the same netns.

        Returns (dev_text, route_text, dev_path) or None if neither
        candidate path is readable. The route file is best-effort — its
        absence still yields throughput data, just without a default-route
        based primary interface (falls back to highest-total-bytes).
        """
        for dev_path, route_path in zip(_NET_DEV_PATHS, _ROUTE_PATHS, strict=True):
            try:
                with open(dev_path) as f:
                    dev_text = f.read()
            except OSError:
                continue
            try:
                with open(route_path) as f:
                    route_text = f.read()
            except OSError:
                route_text = ""
            return dev_text, route_text, dev_path
        return None

    def _included(self, name: str) -> bool:
        """True when an interface should be reported.

        An explicit ``network.interfaces`` allowlist overrides the default
        loopback/virtual-interface filter in both directions: an interface
        that would normally be skipped (e.g. a bridge) is included if
        listed, and one that would normally pass is excluded if omitted.
        """
        allowlist = self.config.network.interfaces
        if allowlist:
            return name in allowlist
        return not _SKIP_IFACE_RE.match(name)

    @staticmethod
    def _parse_net_dev(text: str) -> dict[str, dict[str, int]]:
        """Parse /proc/net/dev into {iface: {rx_bytes, rx_errors, rx_dropped,
        tx_bytes, tx_errors, tx_dropped}}.

        The two header lines have no ':' before their field list, so
        ``partition(":")`` naturally yields an empty/short field list for
        them and they're skipped by the 16-field requirement below —  no
        special-casing needed.
        """
        result: dict[str, dict[str, int]] = {}
        for line in text.splitlines():
            if ":" not in line:
                continue
            name, _, rest = line.partition(":")
            fields = rest.split()
            if len(fields) < 16:
                continue
            try:
                values = [int(f) for f in fields[:16]]
            except ValueError:
                continue
            result[name.strip()] = {
                "rx_bytes": values[0],
                "rx_errors": values[2],
                "rx_dropped": values[3],
                "tx_bytes": values[8],
                "tx_errors": values[10],
                "tx_dropped": values[11],
            }
        return result

    @staticmethod
    def _parse_default_route(text: str) -> str | None:
        """Parse /proc/net/route, returning the interface with the default
        route (Destination 00000000) and the lowest Metric, or None."""
        best_iface: str | None = None
        best_metric: int | None = None
        for line in text.splitlines()[1:]:  # skip header
            parts = line.split()
            if len(parts) < 7 or parts[1] != "00000000":
                continue
            try:
                metric = int(parts[6])
            except ValueError:
                continue
            if best_metric is None or metric < best_metric:
                best_metric = metric
                best_iface = parts[0]
        return best_iface

    @staticmethod
    def _pick_highest_total(interfaces: dict[str, dict[str, int]]) -> str | None:
        """Fallback primary-interface pick when there's no usable default route."""
        if not interfaces:
            return None
        return max(
            interfaces, key=lambda name: interfaces[name]["rx_bytes"] + interfaces[name]["tx_bytes"]
        )

    @staticmethod
    def _compute_rates(
        prev: dict[str, dict[str, int]], cur: dict[str, dict[str, int]], elapsed: float
    ) -> dict[str, dict[str, float]]:
        """Compute per-interface byte/s rates as a delta against the
        previous sample. No prior sample for an interface, or a non-positive
        elapsed window, yields 0 rather than dividing by zero or garbage. A
        counter that goes backwards (32-bit wrap, NIC reset, re-created
        interface) also clamps to 0 rather than reporting negative."""
        rates: dict[str, dict[str, float]] = {}
        for name, sample in cur.items():
            previous = prev.get(name)
            if previous is None or elapsed <= 0:
                rates[name] = {"rx_bytes_per_sec": 0.0, "tx_bytes_per_sec": 0.0}
                continue
            rx_rate = (sample["rx_bytes"] - previous["rx_bytes"]) / elapsed
            tx_rate = (sample["tx_bytes"] - previous["tx_bytes"]) / elapsed
            rates[name] = {
                "rx_bytes_per_sec": max(0.0, rx_rate),
                "tx_bytes_per_sec": max(0.0, tx_rate),
            }
        return rates
