"""Disk collector — mount info, NVMe SMART, I/O stats.

Uses nsenter when running in a container with pid:host to access host mounts.
Falls back to local filesystem info when nsenter is not available.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import time
from typing import TYPE_CHECKING

from buoy.smartctl import run_smartctl, scan_nvme_devices
from buoy.subprocess_utils import communicate

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.collectors.disk")

# Whole-disk block devices only — no trailing partition number/suffix, so
# "sda1", "nvme0n1p1", and "mmcblk0p1" don't match. Also excludes virtual/
# synthetic devices (loop*, dm-*, md*, zram*) whose I/O is already accounted
# for on the physical device(s) underneath them.
_WHOLE_DISK_RE = re.compile(r"^(?:sd[a-z]+|vd[a-z]+|xvd[a-z]+|hd[a-z]+|nvme\d+n\d+|mmcblk\d+)$")

# Mirrors DockerCollector's list_containers() cache (SPEC §8.2): _all_mounts()
# is now on the hot path (collect_summary() -> every /api/stats request and
# stats-loop tick, not just the detail panel), and nsenter+df is a real
# subprocess spawn rather than a cheap syscall.
_MOUNTS_CACHE_TTL = 5.0


_VIRTUAL_FSTYPES = {
    "tmpfs",
    "devtmpfs",
    "proc",
    "sysfs",
    "cgroup",
    "cgroup2",
    "overlay",
    "squashfs",
    "efivarfs",
    "devpts",
    "mqueue",
    "pstore",
    "bpf",
    "tracefs",
    "debugfs",
    "securityfs",
    "autofs",
    "rpc_pipefs",
    "nsfs",
    "fusectl",
    "configfs",
    "binfmt_misc",
    "hugetlbfs",
    "ramfs",
}


class DiskCollector:
    """Collects disk usage, NVMe health, and I/O stats."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._mounts_cache: list[dict] | None = None
        self._mounts_cache_ts: float = 0.0
        self._mounts_lock = asyncio.Lock()

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
        """Get root filesystem usage percentage.

        Derived from the same host-aware mount list _all_mounts() uses
        (nsenter df when available, /proc/mounts otherwise) instead of this
        process's own shutil.disk_usage("/") — inside a container that's the
        container's rootfs, not the host's, so the headline gauge could
        legitimately disagree with the detail panel's mount table, which
        already read the host's root mount (BUG-25).
        """
        try:
            mounts = await self._all_mounts()
            for mount in mounts:
                if mount.get("mount") == "/":
                    return mount["pct"]
            # No "/" entry in the resolved mount list (unexpected, but
            # possible if every fallback came up empty) — fall back to this
            # process's own view of its root filesystem.
            usage = shutil.disk_usage("/")
            return int((usage.used / usage.total) * 100)
        except Exception:
            logger.debug("failed to read root disk usage", exc_info=True)
            return 0

    # ── All Mounts ─────────────────────────────────────────────────────────────

    async def _all_mounts(self) -> list[dict]:
        """Get all real filesystem mounts (excluding tmpfs, etc.), cached briefly.

        Shared by both collect_summary()'s headline gauge and
        collect_detail()'s mount table so the two always agree. Cached for
        _MOUNTS_CACHE_TTL since collect_summary() is now on the hot path.
        """
        now = time.monotonic()
        if self._mounts_cache is not None and now - self._mounts_cache_ts < _MOUNTS_CACHE_TTL:
            return self._mounts_cache

        async with self._mounts_lock:
            now = time.monotonic()
            if self._mounts_cache is not None and now - self._mounts_cache_ts < _MOUNTS_CACHE_TTL:
                return self._mounts_cache

            # Try nsenter first (container with pid:host), then fall back to
            # reading /proc/mounts locally.
            mounts = await self._nsenter_mounts() or self._local_mounts()

            self._mounts_cache = mounts
            self._mounts_cache_ts = time.monotonic()
            return mounts

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
            stdout, _ = await communicate(proc, timeout=5)
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
        """Fallback when nsenter isn't available or permitted.

        Reads /proc/mounts directly rather than shelling out. On a native
        (non-container) install this process is already sitting in the
        host's real mount namespace, so this gives the full host mount
        list with no privilege beyond reading /proc — nsenter into PID 1's
        mount namespace is redundant there and just fails under an
        unprivileged user anyway. Inside an unprivileged container this
        naturally only sees the container's own mounts.
        """
        # Keyed by st_dev: bind mounts (e.g. Docker's per-container
        # /etc/hosts, /etc/resolv.conf) share the same underlying device as
        # their real mount point, so keep only the shortest (real) path
        # per device rather than listing every bind-mounted file too.
        by_device: dict[int, dict] = {}
        try:
            with open("/proc/mounts") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) < 3:
                        continue
                    device, mount_point, fstype = parts[0], parts[1], parts[2]
                    if fstype in _VIRTUAL_FSTYPES:
                        continue
                    if not os.path.isdir(mount_point):
                        continue
                    try:
                        st_dev = os.stat(mount_point).st_dev
                        usage = shutil.disk_usage(mount_point)
                    except OSError:
                        continue
                    if usage.total == 0:
                        continue
                    existing = by_device.get(st_dev)
                    if existing is not None and len(existing["mount"]) <= len(mount_point):
                        continue
                    pct = int((usage.used / usage.total) * 100)
                    by_device[st_dev] = {
                        "fs": device,
                        "size": f"{usage.total / (1024**3):.1f}G",
                        "used": f"{usage.used / (1024**3):.1f}G",
                        "avail": f"{usage.free / (1024**3):.1f}G",
                        "pct": pct,
                        "mount": mount_point,
                    }
        except OSError:
            logger.debug("failed to read /proc/mounts", exc_info=True)
        return list(by_device.values()) or self._root_only_mount()

    def _root_only_mount(self) -> list[dict]:
        """Ultimate fallback if /proc/mounts is unreadable: just report root."""
        try:
            usage = shutil.disk_usage("/")
            pct = int((usage.used / usage.total) * 100)
            return [
                {
                    "fs": "/",
                    "size": f"{usage.total / (1024**3):.1f}G",
                    "used": f"{usage.used / (1024**3):.1f}G",
                    "avail": f"{usage.free / (1024**3):.1f}G",
                    "pct": pct,
                    "mount": "/",
                }
            ]
        except Exception:
            logger.debug("failed to read root mount usage", exc_info=True)
            return []

    # ── NVMe SMART ─────────────────────────────────────────────────────────────

    async def _nvme_smart(self) -> dict | None:
        """Read NVMe SMART data via smartctl for the first NVMe drive found.

        Discovers the actual NVMe device present via `smartctl --scan`
        (shared with the smart_disk plugin's drive discovery, BUG-27)
        instead of assuming the drive is always named `nvme0n1` — a wrong
        assumption on hosts where it's numbered differently, or where
        `nvme0n1` simply isn't the device that exists. Falls back to the
        historical `/dev/nvme0n1` guess if the scan itself finds nothing, in
        case `--scan` misses a device that a direct probe would still reach.

        Surfacing every drive on a multi-NVMe host is a larger UI change
        (today's headline gauge has one NVMe slot) tracked separately as
        #199 (FEAT-16); this only fixes probing the wrong device path.
        """
        nvme_devices = await scan_nvme_devices()
        device = nvme_devices[0] if nvme_devices else "/dev/nvme0n1"

        output = await run_smartctl("-a", device)
        if not output:
            return None

        temp = self._extract_smart(output, "Temperature:", 1)
        wear = self._extract_smart(output, "Percentage Used:", 2, strip_pct=True)
        hours = self._extract_smart(output, "Power On Hours:", 3, strip_comma=True)

        if temp is None and wear is None and hours is None:
            # smartctl still writes its version/copyright banner to stdout
            # even when it fails to open the device (e.g. "No such
            # device") — non-empty `output` alone doesn't mean smartctl
            # actually found a drive, so bail out if none of the expected
            # SMART fields were present to parse.
            return None

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
        """Sum cumulative read/write I/O across every real (whole-disk) block device.

        The previous implementation only recognized `nvme0n1`, `sda`, and
        `mmcblk0` by exact name, so a VM (`vda`/`xvda`), a second SATA disk
        (`sdb`), or an additional NVMe drive (`nvme1n1`) reported 0 GB
        read/write regardless of actual activity (BUG-26). Match by
        device-name pattern instead and sum across every whole disk found.
        """
        try:
            total_read_sectors = 0
            total_write_sectors = 0
            found_any = False

            with open("/proc/diskstats") as f:
                for line in f:
                    parts = line.split()
                    if len(parts) >= 14 and _WHOLE_DISK_RE.match(parts[2]):
                        found_any = True
                        total_read_sectors += int(parts[5])
                        total_write_sectors += int(parts[9])

            if not found_any:
                return {"read_gb": 0, "write_gb": 0}

            return {
                "read_gb": round(total_read_sectors * 512 / (1024**3), 1),
                "write_gb": round(total_write_sectors * 512 / (1024**3), 1),
            }
        except Exception:
            logger.debug("failed to read /proc/diskstats", exc_info=True)
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
