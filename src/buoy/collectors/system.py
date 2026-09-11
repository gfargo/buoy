"""System collector — CPU, memory, temperature, uptime.

Reads from /proc and /sys on Linux. Returns zeros gracefully on other platforms.
"""

from __future__ import annotations

import asyncio
import logging
import os
import platform
from typing import TYPE_CHECKING

from buoy.subprocess_utils import communicate

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.collectors.system")


class SystemCollector:
    """Collects system metrics from /proc and /sys."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._is_linux = platform.system() == "Linux"
        self._last_proc_stat: list[int] | None = None

    async def collect(self) -> dict:
        """Collect basic system stats (for /api/stats response)."""
        if not self._is_linux:
            return self._fallback_stats()

        cpu = await self._read_cpu()
        mem_used, mem_total = self._read_memory()
        temp = self._read_temperature()
        uptime_h, uptime_m, uptime_s = self._read_uptime()
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
            "uptime_s": uptime_s,
        }

    async def collect_detail(self) -> dict:
        """Collect extended CPU + memory detail (for /api/stats/detail)."""
        if not self._is_linux:
            return {"cpu": {}, "memory": {}}

        cpu_detail = await self._read_cpu_detail()
        mem_detail = self._read_memory_detail()
        mem_detail["top_processes"] = await self._top_processes_by("mem")

        return {"cpu": cpu_detail, "memory": mem_detail}

    # ── CPU ────────────────────────────────────────────────────────────────────

    async def _read_cpu(self) -> int:
        """Read CPU usage percentage from /proc/stat.

        Computes the delta against the sample kept from the *previous* call
        instead of blocking on a fixed 100ms sleep between two samples taken
        back-to-back (BUG-31) — that cost was paid on every /api/stats and
        /metrics request, and again on every stats-loop tick. The elapsed
        time between calls (governed by how often those are hit) already
        gives a real window to measure over.
        """
        try:
            sample = self._read_proc_stat()
            previous = self._last_proc_stat
            self._last_proc_stat = sample

            if previous is None:
                return 0  # no prior sample yet (first read since startup)

            idle_delta = sample[3] - previous[3]
            total_delta = sum(sample) - sum(previous)
            if total_delta <= 0:
                return 0
            return int(100 * (1 - idle_delta / total_delta))
        except Exception:
            logger.debug("failed to read CPU usage from /proc/stat", exc_info=True)
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
            logger.debug("failed to read CPU model from /proc/cpuinfo", exc_info=True)

        load_1, load_5, load_15 = 0.0, 0.0, 0.0
        try:
            with open("/proc/loadavg") as f:
                parts = f.read().split()
                load_1, load_5, load_15 = float(parts[0]), float(parts[1]), float(parts[2])
        except Exception:
            logger.debug("failed to read load averages from /proc/loadavg", exc_info=True)

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
            # MemAvailable already accounts for reclaimable caches/slab (unlike
            # MemFree+Buffers+Cached, which ignores SReclaimable and Shmem and so
            # overstates "used" vs free/htop). Fall back to MemFree if the kernel
            # doesn't expose MemAvailable (very old kernels).
            available_kb = info.get("MemAvailable", info.get("MemFree", 0))

            used_kb = total_kb - available_kb
            return round(used_kb / 1048576, 1), round(total_kb / 1048576, 1)
        except Exception:
            logger.debug("failed to read memory from /proc/meminfo", exc_info=True)
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

            # See _read_memory: MemTotal - MemAvailable matches free/htop's "used",
            # unlike MemTotal - MemFree - Buffers - Cached which ignores
            # SReclaimable and Shmem.
            used = total - available
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
                # top_processes is added by collect_detail(), which awaits
                # _top_processes_by("mem") — this method stays sync.
            }
        except Exception:
            logger.debug("failed to read memory detail from /proc/meminfo", exc_info=True)
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

    def _read_temperature(self) -> int | None:
        """Read CPU temperature, or None if no CPU sensor could be identified.

        thermal_zone0 is the CPU on a Raspberry Pi but is frequently
        `acpitz`, a wifi radio, or a battery sensor on x86 (BUG-28) — always
        reading zone0 by index silently reported the wrong sensor, or 0°C
        when it happened not to exist. Prefer hwmon drivers known to be CPU
        package sensors (coretemp/k10temp/zenpower), then fall back to
        scanning thermal zones by *type* rather than by a fixed index.
        """
        temp = self._read_hwmon_cpu_temp()
        if temp is not None:
            return temp
        return self._read_thermal_zone_cpu_temp()

    _CPU_HWMON_NAMES = frozenset({"coretemp", "k10temp", "zenpower", "cpu_thermal"})

    def _read_hwmon_cpu_temp(self) -> int | None:
        """Scan /sys/class/hwmon for a driver known to expose the CPU package temp."""
        try:
            entries = sorted(os.listdir("/sys/class/hwmon"))
        except OSError:
            return None

        for entry in entries:
            base = f"/sys/class/hwmon/{entry}"
            try:
                with open(f"{base}/name") as f:
                    name = f.read().strip()
            except OSError:
                continue
            if name not in self._CPU_HWMON_NAMES:
                continue
            try:
                with open(f"{base}/temp1_input") as f:
                    return int(f.read().strip()) // 1000
            except (OSError, ValueError):
                logger.debug("hwmon '%s' matched but temp1_input unreadable", name, exc_info=True)
        return None

    # Zone `type` values known to be the CPU/SoC package temperature, most
    # specific first. "acpitz" is a best-effort fallback: on ACPI-based x86
    # hosts without coretemp/k10temp loaded it's often the only sensor at
    # all, but it isn't always specifically the CPU, so it's tried last.
    _CPU_THERMAL_ZONE_TYPES = (
        "x86_pkg_temp",
        "cpu-thermal",
        "cpu_thermal",
        "soc-thermal",
        "soc_thermal",
        "acpitz",
    )

    def _read_thermal_zone_cpu_temp(self) -> int | None:
        """Scan /sys/class/thermal for a zone whose *type* looks CPU-related."""
        try:
            entries = sorted(
                e for e in os.listdir("/sys/class/thermal") if e.startswith("thermal_zone")
            )
        except OSError:
            return None

        zones: dict[str, str] = {}  # type -> temp file path, first match wins per type
        for entry in entries:
            base = f"/sys/class/thermal/{entry}"
            try:
                with open(f"{base}/type") as f:
                    zone_type = f.read().strip()
            except OSError:
                continue
            zones.setdefault(zone_type, f"{base}/temp")

        for preferred_type in self._CPU_THERMAL_ZONE_TYPES:
            temp_path = zones.get(preferred_type)
            if temp_path is None:
                continue
            try:
                with open(temp_path) as f:
                    return int(f.read().strip()) // 1000
            except (OSError, ValueError):
                logger.debug("thermal zone type '%s' matched but unreadable", preferred_type)
        return None

    # ── Uptime ─────────────────────────────────────────────────────────────────

    def _read_uptime(self) -> tuple[int, int, int]:
        """Read uptime from /proc/uptime, return (hours, minutes, seconds)."""
        try:
            with open("/proc/uptime") as f:
                seconds = int(float(f.read().split()[0]))
            return seconds // 3600, (seconds % 3600) // 60, seconds
        except Exception:
            logger.debug("failed to read uptime from /proc/uptime", exc_info=True)
            return 0, 0, 0

    # ── Device Model ───────────────────────────────────────────────────────────

    def _read_model(self) -> str:
        """Read device model (Pi, etc.) from device-tree."""
        try:
            with open("/proc/device-tree/model") as f:
                return f.read().strip().rstrip("\x00")
        except Exception:
            logger.debug("failed to read device model from device-tree", exc_info=True)
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
            stdout, _ = await communicate(proc, timeout=5)
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
            logger.debug("failed to read top processes via ps", exc_info=True)
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
            "uptime_s": 0,
        }
