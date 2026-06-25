"""System collector — CPU, memory, temperature, uptime.

Reads from /proc and /sys on Linux. Returns zeros gracefully on other platforms.
"""

from __future__ import annotations

import asyncio
import os
import platform
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig


class SystemCollector:
    """Collects system metrics from /proc and /sys."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._is_linux = platform.system() == "Linux"

    async def collect(self) -> dict:
        """Collect basic system stats (for /api/stats response)."""
        if not self._is_linux:
            return self._fallback_stats()

        cpu = await self._read_cpu()
        mem_used, mem_total = self._read_memory()
        temp = self._read_temperature()
        uptime_h, uptime_m = self._read_uptime()
        model = self._read_model()

        return {
            "hostname": self.config.node.name,
            "model": model,
            "tailscale": self.config.network.tailnet_domain,
            "cpu": cpu,
            "mem_used": mem_used,
            "mem_total": mem_total,
            "temp": temp,
            "uptime_h": uptime_h,
            "uptime_m": uptime_m,
        }

    async def collect_detail(self) -> dict:
        """Collect extended CPU + memory detail (for /api/stats/detail)."""
        if not self._is_linux:
            return {"cpu": {}, "memory": {}}

        cpu_detail = await self._read_cpu_detail()
        mem_detail = self._read_memory_detail()

        return {"cpu": cpu_detail, "memory": mem_detail}

    # ── CPU ────────────────────────────────────────────────────────────────────

    async def _read_cpu(self) -> int:
        """Read CPU usage percentage from /proc/stat (two-sample delta)."""
        try:
            s1 = self._read_proc_stat()
            await asyncio.sleep(0.1)
            s2 = self._read_proc_stat()

            idle_delta = s2[3] - s1[3]
            total_delta = sum(s2) - sum(s1)
            if total_delta == 0:
                return 0
            return int(100 * (1 - idle_delta / total_delta))
        except Exception:
            return 0

    def _read_proc_stat(self) -> list[int]:
        """Read aggregate CPU times from first line of /proc/stat."""
        with open("/proc/stat") as f:
            line = f.readline()
        parts = line.split()
        # user, nice, system, idle, iowait, irq, softirq, steal
        return [int(x) for x in parts[1:9]]

    async def _read_cpu_detail(self) -> dict:
        """Extended CPU info: model, cores, load averages, top processes."""
        cores = os.cpu_count() or 1
        model = "unknown"
        try:
            with open("/proc/cpuinfo") as f:
                for line in f:
                    if line.startswith("model name"):
                        model = line.split(":", 1)[1].strip()
                        break
                else:
                    # ARM devices use /proc/device-tree/model
                    model = self._read_model() or "unknown"
        except Exception:
            pass

        load_1, load_5, load_15 = 0.0, 0.0, 0.0
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                load_1, load_5, load_15 = float(parts[0]), float(parts[1]), float(parts[2])
        except Exception:
            pass

        top_processes = await self._top_processes_by("cpu")

        return {
            "model": model,
            "cores": cores,
            "load_1": load_1,
            "load_5": load_5,
            "load_15": load_15,
            "top_processes": top_processes,
        }

    # ── Memory ─────────────────────────────────────────────────────────────────

    def _read_memory(self) -> tuple[float, float]:
        """Read used/total memory in GB from /proc/meminfo."""
        try:
            info = self._parse_meminfo()
            total_kb = info.get("MemTotal", 0)
            free_kb = info.get("MemFree", 0)
            buffers_kb = info.get("Buffers", 0)
            cached_kb = info.get("Cached", 0)

            used_kb = total_kb - free_kb - buffers_kb - cached_kb
            return round(used_kb / 1048576, 1), round(total_kb / 1048576, 1)
        except Exception:
            return 0.0, 0.0

    def _read_memory_detail(self) -> dict:
        """Extended memory info with breakdown."""
        try:
            info = self._parse_meminfo()
            total = info.get("MemTotal", 0) // 1024  # MB
            free = info.get("MemFree", 0) // 1024
            available = info.get("MemAvailable", 0) // 1024
            buffers = info.get("Buffers", 0) // 1024
            cached = info.get("Cached", 0) // 1024
            swap_total = info.get("SwapTotal", 0) // 1024
            swap_free = info.get("SwapFree", 0) // 1024

            used = total - free - buffers - cached
            swap_used = swap_total - swap_free

            return {
                "total_mb": total,
                "used_mb": used,
                "free_mb": free,
                "available_mb": available,
                "buffers_mb": buffers,
                "cached_mb": cached,
                "swap_total_mb": swap_total,
                "swap_used_mb": swap_used,
                "top_processes": [],  # populated by _top_processes_by("mem")
            }
        except Exception:
            return {}

    def _parse_meminfo(self) -> dict[str, int]:
        """Parse /proc/meminfo into a dict of key → value in kB."""
        result = {}
        with open("/proc/meminfo") as f:
            for line in f:
                parts = line.split()
                if len(parts) >= 2:
                    key = parts[0].rstrip(":")
                    result[key] = int(parts[1])
        return result

    # ── Temperature ────────────────────────────────────────────────────────────

    def _read_temperature(self) -> int:
        """Read CPU temperature from thermal zone."""
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                return int(f.read().strip()) // 1000
        except Exception:
            return 0

    # ── Uptime ─────────────────────────────────────────────────────────────────

    def _read_uptime(self) -> tuple[int, int]:
        """Read uptime from /proc/uptime, return (hours, minutes)."""
        try:
            with open("/proc/uptime") as f:
                seconds = int(float(f.read().split()[0]))
            return seconds // 3600, (seconds % 3600) // 60
        except Exception:
            return 0, 0

    # ── Device Model ───────────────────────────────────────────────────────────

    def _read_model(self) -> str:
        """Read device model (Pi, etc.) from device-tree."""
        try:
            with open("/proc/device-tree/model") as f:
                return f.read().strip().rstrip("\x00")
        except Exception:
            return ""

    # ── Top Processes ──────────────────────────────────────────────────────────

    async def _top_processes_by(self, sort_key: str, limit: int = 5) -> list[dict]:
        """Get top N processes sorted by cpu or mem usage via ps."""
        sort_flag = "-%cpu" if sort_key == "cpu" else "-%mem"
        try:
            proc = await asyncio.create_subprocess_exec(
                "ps",
                "aux",
                f"--sort={sort_flag}",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            lines = stdout.decode().strip().split("\n")[1 : limit + 1]
            result = []
            for line in lines:
                parts = line.split(None, 10)
                if len(parts) >= 11:
                    result.append(
                        {
                            "pid": int(parts[1]),
                            "cpu": float(parts[2]),
                            "mem": float(parts[3]),
                            "cmd": parts[10][:80],
                        }
                    )
            return result
        except Exception:
            return []

    # ── Fallback (non-Linux) ───────────────────────────────────────────────────

    def _fallback_stats(self) -> dict:
        """Return placeholder stats on non-Linux platforms."""
        return {
            "hostname": self.config.node.name,
            "model": f"{platform.system()} {platform.machine()}",
            "tailscale": self.config.network.tailnet_domain,
            "cpu": 0,
            "mem_used": 0.0,
            "mem_total": 0.0,
            "temp": 0,
            "uptime_h": 0,
            "uptime_m": 0,
        }
