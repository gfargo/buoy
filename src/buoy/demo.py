"""Demo mode — realistic mock data for all collectors.

When buoy runs with --demo, these collectors replace the real ones.
No Docker socket, no /proc, no privileged mode needed.
"""

from __future__ import annotations

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
                    {"pid": 1842, "cpu": round(cpu_val * 0.4, 1), "mem": 3.2, "cmd": "docker-containerd"},
                    {"pid": 2901, "cpu": round(cpu_val * 0.2, 1), "mem": 8.1, "cmd": "grafana-server"},
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
        return {
            "containers": len(_DEMO_CONTAINERS),
            "containers_list": [{"name": c["name"]} for c in _DEMO_CONTAINERS],
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

    async def restart_container(self, name: str) -> dict:
        return {"success": True, "container": name}


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
                {"fs": "/dev/nvme0n1p2", "size": "953G", "used": "428G", "avail": "477G", "pct": 45, "mount": "/"},
                {"fs": "/dev/nvme0n1p1", "size": "512M", "used": "63M", "avail": "449M", "pct": 12, "mount": "/boot/firmware"},
                {"fs": "/dev/sda1", "size": "32G", "used": "4.8G", "avail": "25G", "pct": 16, "mount": "/mnt/backup"},
            ],
            "io_read_gb": round(_sinusoidal(142, 2), 1),
            "io_write_gb": round(_sinusoidal(86, 1), 1),
        }
