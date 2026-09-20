"""Demo mode — realistic mock data for all collectors.

When buoy runs with --demo, these collectors replace the real ones.
No Docker socket, no /proc, no privileged mode needed.
"""

from __future__ import annotations

import asyncio
import math
import random
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

# Fake containers that look realistic
_DEMO_CONTAINERS = [
    {"name": "grafana", "host_port": 3000},
    {"name": "prometheus", "host_port": 9090},
    {"name": "nginx-proxy", "host_port": 443},
    {"name": "postgres", "host_port": None},
    {"name": "redis", "host_port": None},
    {"name": "plausible", "host_port": 8000},
    {"name": "uptime-kuma", "host_port": 3001},
    {"name": "vaultwarden", "host_port": 8080},
    {"name": "immich-server", "host_port": 2283},
    {"name": "homeassistant", "host_port": 8123},
    {"name": "jellyfin", "host_port": 8096},
    {"name": "actual-budget", "host_port": 5006},
]

_START_TIME = time.time()

# State/health/status for each demo container, keyed by name — kept separate
# from _DEMO_CONTAINERS (which list_containers() returns by reference and
# which feeds demo service discovery) so this dict is free to include
# non-running entries without perturbing that list. Includes one unhealthy
# and one exited container so the containers panel has something to show off.
_DEMO_CONTAINER_STATES = {
    "grafana": {"state": "running", "status": "Up 2 days", "health": None},
    "prometheus": {"state": "running", "status": "Up 2 days", "health": None},
    "nginx-proxy": {"state": "running", "status": "Up 5 days (healthy)", "health": "healthy"},
    "postgres": {"state": "running", "status": "Up 5 days (healthy)", "health": "healthy"},
    "redis": {"state": "running", "status": "Up 3 hours (unhealthy)", "health": "unhealthy"},
    "plausible": {"state": "running", "status": "Up 1 day", "health": None},
    "uptime-kuma": {"state": "running", "status": "Up 5 days", "health": None},
    "vaultwarden": {"state": "running", "status": "Up 5 days", "health": None},
    "immich-server": {
        "state": "running",
        "status": "Up 12 minutes (health: starting)",
        "health": "starting",
    },
    "homeassistant": {"state": "running", "status": "Up 5 days", "health": None},
    "jellyfin": {"state": "running", "status": "Up 5 days", "health": None},
    "actual-budget": {"state": "exited", "status": "Exited (0) 3 hours ago", "health": None},
}

# Curated builtins auto-enabled for `--demo` when the operator hasn't
# configured any plugins, so a bare `docker run ... --demo` shows a populated
# dashboard instead of an empty one. Deliberately excludes prometheus_exporter:
# its /metrics route is registered from config at app-build time (server.py),
# so auto-enabling it here would desync the route from the plugin state.
DEMO_PLUGIN_IDS = (
    "github",
    "uptime_kuma",
    "tailscale",
    "smart_disk",
    "immich",
    "jellyfin",
    "proxmox",
    "dns_filter",
    "arr_stack",
)


def _sinusoidal(base: float, amplitude: float, period: float = 300) -> float:
    """Generate a sinusoidal value with noise for realistic fluctuation."""
    t = time.time() - _START_TIME
    wave = math.sin(2 * math.pi * t / period) * amplitude
    noise = random.uniform(-amplitude * 0.3, amplitude * 0.3)
    return base + wave + noise


class DemoSystemCollector:
    """Mock system collector with realistic fluctuating values."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def collect(self) -> dict:
        uptime_sec = int(time.time() - _START_TIME) + 86400 * 3  # pretend 3 days up
        cpu = max(1, min(95, int(_sinusoidal(25, 15, period=120))))
        mem_used = round(max(0.5, min(7.5, _sinusoidal(4.2, 0.8, period=300))), 1)
        temp = max(35, min(78, int(_sinusoidal(52, 8, period=600))))

        return {
            "hostname": self.config.node.name or "demo-node",
            "model": "Raspberry Pi 5 Model B Rev 1.0",
            "tailscale": "demo.ts.net",
            "cpu": cpu,
            "mem_used": mem_used,
            "mem_total": 8.0,
            "temp": temp,
            "disk_pct": max(20, min(85, int(_sinusoidal(45, 5, period=900)))),
            "uptime_h": uptime_sec // 3600,
            "uptime_m": (uptime_sec % 3600) // 60,
            "uptime_s": uptime_sec,
            "nvme": {
                "temp": max(30, min(55, int(_sinusoidal(38, 4)))),
                "wear_pct": 2,
                "read": "1.42 TB",
                "written": "856 GB",
                "power_hours": 2847,
            },
        }

    async def collect_detail(self) -> dict:
        cpu_val = max(1, min(95, int(_sinusoidal(25, 15, period=120))))
        return {
            "cpu": {
                "model": "Cortex-A76 (ARMv8.2)",
                "cores": 4,
                "load_1": round(max(0, _sinusoidal(1.2, 0.8)), 2),
                "load_5": round(max(0, _sinusoidal(1.0, 0.5)), 2),
                "load_15": round(max(0, _sinusoidal(0.8, 0.3)), 2),
                "top_processes": [
                    {
                        "pid": 1842,
                        "cpu": round(cpu_val * 0.4, 1),
                        "mem": 3.2,
                        "cmd": "docker-containerd",
                    },
                    {
                        "pid": 2901,
                        "cpu": round(cpu_val * 0.2, 1),
                        "mem": 8.1,
                        "cmd": "grafana-server",
                    },
                    {"pid": 3104, "cpu": round(cpu_val * 0.15, 1), "mem": 5.4, "cmd": "postgres"},
                    {"pid": 1203, "cpu": round(cpu_val * 0.1, 1), "mem": 2.8, "cmd": "nginx"},
                    {"pid": 4501, "cpu": round(cpu_val * 0.05, 1), "mem": 1.2, "cmd": "node"},
                ],
            },
            "memory": {
                "total_mb": 8192,
                "used_mb": int(_sinusoidal(4300, 400)),
                "free_mb": int(_sinusoidal(1200, 300)),
                "available_mb": int(_sinusoidal(3800, 500)),
                "buffers_mb": 128,
                "cached_mb": int(_sinusoidal(2400, 200)),
                "swap_total_mb": 4096,
                "swap_used_mb": int(max(0, _sinusoidal(64, 30))),
                "top_processes": [
                    {"pid": 2901, "mem": 8.1, "rss_mb": 664, "cmd": "grafana-server"},
                    {"pid": 3104, "mem": 5.4, "rss_mb": 442, "cmd": "postgres"},
                    {"pid": 1842, "mem": 3.2, "rss_mb": 262, "cmd": "docker-containerd"},
                    {"pid": 5020, "mem": 2.9, "rss_mb": 238, "cmd": "jellyfin"},
                    {"pid": 1203, "mem": 2.8, "rss_mb": 230, "cmd": "nginx"},
                ],
            },
        }


class DemoDockerCollector:
    """Mock Docker collector with fake containers."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def list_containers(self) -> list[dict]:
        return _DEMO_CONTAINERS

    async def collect_summary(self) -> dict:
        containers_list = []
        running_count = 0
        for c in _DEMO_CONTAINERS:
            name = c["name"]
            st = _DEMO_CONTAINER_STATES.get(
                name, {"state": "running", "status": "Up", "health": None}
            )
            entry = {
                "name": name,
                "state": st["state"],
                "status": st["status"],
                "health": st["health"],
                "cpu_pct": None,
                "mem_usage": None,
                "mem_pct": None,
            }
            if st["state"] == "running":
                running_count += 1
                entry["cpu_pct"] = f"{random.uniform(0.1, 15.0):.2f}%"
                entry["mem_usage"] = f"{random.randint(50, 500)}MiB / 8GiB"
                entry["mem_pct"] = f"{random.uniform(0.5, 8.0):.2f}%"
            containers_list.append(entry)

        return {
            "containers": running_count,
            "containers_list": containers_list,
        }

    async def inspect_container(self, name: str) -> dict:
        return {
            "name": name,
            "status": "running",
            "started": "2026-06-22T08:15:30Z",
            "image": f"{name}:latest",
            "restart_count": random.randint(0, 3),
            "pid": random.randint(1000, 9999),
            "image_created": "2026-06-20T12:00:00Z",
            "resources": {
                "cpu_pct": f"{random.uniform(0.1, 15.0):.2f}%",
                "mem_usage": f"{random.randint(50, 500)}MiB / 8GiB",
                "mem_pct": f"{random.uniform(0.5, 8.0):.2f}%",
                "net_io": f"{random.randint(1, 500)}MB / {random.randint(1, 200)}MB",
                "block_io": f"{random.randint(0, 100)}MB / {random.randint(0, 50)}MB",
            },
            "ports": "0.0.0.0:8080->8080/tcp",
        }

    async def get_logs(self, name: str, tail: int = 30) -> dict:
        lines = [
            f"2026-06-24T10:00:{i:02d}Z  INFO  [{name}] Request processed in {random.randint(1, 50)}ms"
            for i in range(min(tail, 10))
        ]
        return {"container": name, "lines": lines}

    async def stream_logs(self, name: str, tail: int = 100, max_line_bytes: int = 8192):
        """Emit a synthetic log line roughly once a second, forever.

        Mirrors ``DockerCollector.stream_logs``'s shape (an async generator
        of ``{"stream", "line"}`` dicts) so the frontend viewer and
        playwright smoke tests can exercise live streaming in demo mode
        without a real Docker socket.
        """
        i = 0
        while True:
            await asyncio.sleep(1)
            i += 1
            ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            stream = "stderr" if i % 7 == 0 else "stdout"
            level = "WARN" if stream == "stderr" else "INFO"
            yield {
                "stream": stream,
                "line": f"{ts} {level} [{name}] demo log line {i} — "
                f"request handled in {random.randint(1, 80)}ms",
            }

    async def restart_container(self, name: str) -> dict:
        return {"success": True, "container": name}

    async def list_container_states(self) -> list[dict]:
        """Return synthetic states for demo containers."""
        states = []
        for c in _DEMO_CONTAINERS:
            n = c["name"]
            # Most containers running; give a couple a restart count for realism
            restart_count = 2 if n in ("redis", "nginx-proxy") else 0
            states.append({"name": n, "status": "running", "restart_count": restart_count})
        return states


_DEMO_IMAGE_STATUSES = {
    "grafana": "update_available",
    "prometheus": "up_to_date",
    "nginx-proxy": "up_to_date",
    "postgres": "up_to_date",
    "redis": "unknown",
    "plausible": "update_available",
    "uptime-kuma": "up_to_date",
    "vaultwarden": "up_to_date",
    "immich-server": "update_available",
    "homeassistant": "up_to_date",
    "jellyfin": "unknown",
    "actual-budget": "up_to_date",
}


class DemoImageUpdateChecker:
    """Mock image update checker returning deterministic demo statuses."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def check_all(self) -> dict[str, dict]:
        now = time.time()
        return {
            c["name"]: {
                "status": _DEMO_IMAGE_STATUSES.get(c["name"], "unknown"),
                "image": f"{c['name']}:latest",
                "checked_at": now,
            }
            for c in _DEMO_CONTAINERS
        }


class DemoStaticHealthChecker:
    """Mock static-service health checker — makes no outbound requests."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def check_all(self) -> dict[str, dict]:
        return {
            entry.name: {"status": "ok", "latency_ms": round(random.uniform(5, 40), 1)}
            for entry in self.config.services.static
            if entry.health_check
        }


class DemoDiskCollector:
    """Mock disk collector with realistic mount data."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def collect_summary(self) -> dict:
        return {
            "disk_pct": max(20, min(85, int(_sinusoidal(45, 5, period=900)))),
            "nvme": {
                "temp": max(30, min(55, int(_sinusoidal(38, 4)))),
                "wear_pct": 2,
                "read": "1.42 TB",
                "written": "856 GB",
                "power_hours": 2847,
            },
        }

    async def collect_detail(self) -> dict:
        return {
            "mounts": [
                {
                    "fs": "/dev/nvme0n1p2",
                    "size": "953G",
                    "used": "428G",
                    "avail": "477G",
                    "pct": 45,
                    "mount": "/",
                },
                {
                    "fs": "/dev/nvme0n1p1",
                    "size": "512M",
                    "used": "63M",
                    "avail": "449M",
                    "pct": 12,
                    "mount": "/boot/firmware",
                },
                {
                    "fs": "/dev/sda1",
                    "size": "32G",
                    "used": "4.8G",
                    "avail": "25G",
                    "pct": 16,
                    "mount": "/mnt/backup",
                },
            ],
            "io_read_gb": round(_sinusoidal(142, 2), 1),
            "io_write_gb": round(_sinusoidal(86, 1), 1),
        }


class DemoNetworkCollector:
    """Mock network collector: synthetic per-interface throughput, no peers.

    ``collect()`` and ``measure_latency()`` mirror the real collector's
    peer-polling/latency shape but return empty results (demo mode has no
    real peers to poll), so ``api_fleet`` and ``_latency_loop`` behave
    exactly as they did before demo mode had a "network" collector at all.
    """

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def collect(self) -> dict:
        return {"peers": []}

    async def measure_latency(self) -> list:
        return []

    async def collect_throughput(self) -> dict:
        rx0 = max(0, _sinusoidal(1_800_000, 900_000, period=180))
        tx0 = max(0, _sinusoidal(320_000, 160_000, period=180))
        rx1 = max(0, _sinusoidal(45_000, 30_000, period=240))
        tx1 = max(0, _sinusoidal(12_000, 8_000, period=240))
        return {
            "net": {
                "primary": "eth0",
                "rx_bytes_per_sec": round(rx0, 1),
                "tx_bytes_per_sec": round(tx0, 1),
                "source": "demo",
                "interfaces": [
                    {
                        "name": "eth0",
                        "rx_bytes": 128_849_018_880,
                        "tx_bytes": 42_949_672_960,
                        "rx_bytes_per_sec": round(rx0, 1),
                        "tx_bytes_per_sec": round(tx0, 1),
                        "rx_errors": 0,
                        "tx_errors": 0,
                        "rx_dropped": 0,
                        "tx_dropped": 0,
                    },
                    {
                        "name": "tailscale0",
                        "rx_bytes": 4_294_967_296,
                        "tx_bytes": 2_147_483_648,
                        "rx_bytes_per_sec": round(rx1, 1),
                        "tx_bytes_per_sec": round(tx1, 1),
                        "rx_errors": 0,
                        "tx_errors": 0,
                        "rx_dropped": 0,
                        "tx_dropped": 0,
                    },
                ],
            }
        }


class DemoGpuCollector:
    """Mock GPU collector — one NVIDIA GPU, so the transcoding/ML audience
    (Jellyfin, Frigate, Ollama) sees a populated GPU panel in `--demo`."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def collect_summary(self) -> dict:
        return {"gpus": [self._gpu()]}

    async def collect_detail(self) -> dict:
        return {
            "gpus": [self._gpu()],
            "processes": [
                {"pid": 4821, "name": "ffmpeg", "mem_mb": 1024},
                {"pid": 5290, "name": "ollama", "mem_mb": 3072},
            ],
        }

    def _gpu(self) -> dict:
        return {
            "vendor": "nvidia",
            "index": 0,
            "name": "NVIDIA GeForce RTX 3060",
            "util_pct": max(0, min(100, int(_sinusoidal(35, 25, period=180)))),
            "mem_used_mb": max(500, min(11500, int(_sinusoidal(4200, 1500, period=240)))),
            "mem_total_mb": 12288,
            "temp": max(35, min(80, int(_sinusoidal(58, 10, period=200)))),
            "power_w": round(max(20, min(170, _sinusoidal(95, 40, period=180))), 1),
            "power_limit_w": 170.0,
        }
