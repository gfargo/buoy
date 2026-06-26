"""Disk collector — mount info, NVMe SMART, I/O stats.

Uses nsenter when running in a container with pid:host to access host mounts.
Falls back to local filesystem info when nsenter is not available.
"""

from __future__ import annotations

import asyncio
import shutil
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig


class DiskCollector:
    """Collects disk usage, NVMe health, and I/O stats."""

    def __init__(self, config: BuoyConfig):
        self.config = config

    async def collect_summary(self) -> dict:
        """Collect root disk percentage + NVMe info for the stats response."""
        disk_pct = await self._root_disk_percent()
        nvme = await self._nvme_smart()
        result = {"disk_pct": disk_pct}
        if nvme:
            result["nvme"] = nvme
        return result

    async def collect_detail(self) -> dict:
        """Collect detailed mount info and I/O for the detail panel."""
        mounts = await self._all_mounts()
        io = await self._disk_io()
        return {
            "mounts": mounts,
            "io_read_gb": io.get("read_gb", 0),
            "io_write_gb": io.get("write_gb", 0),
        }

    # ── Root Disk ──────────────────────────────────────────────────────────────

    async def _root_disk_percent(self) -> int:
        """Get root filesystem usage percentage."""
        try:
            usage = shutil.disk_usage("/")
            return int((usage.used / usage.total) * 100)
        except Exception:
            return 0

    # ── All Mounts ─────────────────────────────────────────────────────────────

    async def _all_mounts(self) -> list[dict]:
        """Get all real filesystem mounts (excluding tmpfs, etc.)."""
        # Try nsenter first (container with pid:host)
        mounts = await self._nsenter_mounts()
        if mounts:
            return mounts

        # Fallback: read /proc/mounts locally
        return self._local_mounts()

    async def _nsenter_mounts(self) -> list[dict]:
        """Use nsenter to get host mount info."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "nsenter",
                "-t",
                "1",
                "-m",
                "--",
                "df",
                "-h",
                "-x",
                "tmpfs",
                "-x",
                "devtmpfs",
                "-x",
                "squashfs",
                "-x",
                "overlay",
                "-x",
                "efivarfs",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
            if proc.returncode != 0:
                return []

            mounts = []
            for line in stdout.decode().strip().split("\n")[1:]:
                parts = line.split()
                if len(parts) >= 6:
                    pct_str = parts[4].rstrip("%")
                    try:
                        pct = int(pct_str)
                    except ValueError:
                        pct = 0
                    mounts.append(
                        {
                            "fs": parts[0],
                            "size": parts[1],
                            "used": parts[2],
                            "avail": parts[3],
                            "pct": pct,
                            "mount": parts[5],
                        }
                    )
            return mounts
        except (TimeoutError, FileNotFoundError):
            return []

    def _local_mounts(self) -> list[dict]:
        """Fallback: get mount info from local filesystem."""
        mounts = []
        try:
            usage = shutil.disk_usage("/")
            total_gb = usage.total / (1024**3)
            used_gb = usage.used / (1024**3)
            avail_gb = usage.free / (1024**3)
            pct = int((usage.used / usage.total) * 100)
            mounts.append(
                {
                    "fs": "/",
                    "size": f"{total_gb:.1f}G",
                    "used": f"{used_gb:.1f}G",
                    "avail": f"{avail_gb:.1f}G",
                    "pct": pct,
                    "mount": "/",
                }
            )
        except Exception:
            pass
        return mounts

    # ── NVMe SMART ─────────────────────────────────────────────────────────────

    async def _nvme_smart(self) -> dict | None:
        """Read NVMe SMART data via smartctl.

        Tries nsenter first (container with pid:host) to access the host's
        /dev/nvme0n1, then falls back to direct access.
        """
        # Build command: prefer nsenter to reach host device from container
        nsenter_cmd = ["nsenter", "-t", "1", "-m", "--", "smartctl", "-a", "/dev/nvme0n1"]
        direct_cmd = ["smartctl", "-a", "/dev/nvme0n1"]

        output: str | None = None
        for cmd in (nsenter_cmd, direct_cmd):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                if proc.returncode is not None and stdout:
                    output = stdout.decode()
                    break
            except (TimeoutError, FileNotFoundError):
                continue

        if not output:
            return None

        temp = self._extract_smart(output, "Temperature:", 1)
        wear = self._extract_smart(output, "Percentage Used:", 2, strip_pct=True)
        hours = self._extract_smart(output, "Power On Hours:", 3, strip_comma=True)

        read_line = self._find_line(output, "Data Units Read:")
        written_line = self._find_line(output, "Data Units Written:")
        read_val = self._extract_bracket(read_line) if read_line else "unknown"
        written_val = self._extract_bracket(written_line) if written_line else "unknown"

        return {
            "temp": int(temp) if temp else 0,
            "wear_pct": int(wear) if wear else 0,
            "read": read_val,
            "written": written_val,
            "power_hours": int(hours) if hours else 0,
        }

    # ── Disk I/O ───────────────────────────────────────────────────────────────

    async def _disk_io(self) -> dict:
        """Read cumulative I/O from /proc/diskstats."""
        try:
            with open("/proc/diskstats") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 14 and parts[2] in ("nvme0n1", "sda", "mmcblk0"):
                        sectors_read = int(parts[5])
                        sectors_written = int(parts[9])
                        return {
                            "read_gb": round(sectors_read * 512 / (1024**3), 1),
                            "write_gb": round(sectors_written * 512 / (1024**3), 1),
                        }
        except Exception:
            pass
        return {"read_gb": 0, "write_gb": 0}

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _extract_smart(
        output: str, prefix: str, word_idx: int, strip_pct: bool = False, strip_comma: bool = False
    ) -> str | None:
        for line in output.split("\n"):
            if prefix in line:
                parts = line.split()
                if len(parts) > word_idx:
                    val = parts[word_idx]
                    if strip_pct:
                        val = val.rstrip("%")
                    if strip_comma:
                        val = val.replace(",", "")
                    return val
        return None

    @staticmethod
    def _find_line(output: str, prefix: str) -> str | None:
        for line in output.split("\n"):
            if prefix in line:
                return line
        return None

    @staticmethod
    def _extract_bracket(line: str) -> str:
        """Extract value in square brackets: 'Data Units Read: 123 [456 GB]' → '456 GB'."""
        if "[" in line and "]" in line:
            return line[line.index("[") + 1 : line.index("]")]
        return "unknown"
