"""Shared smartctl helpers — subprocess invocation and drive discovery.

Used by both the core NVMe gauge (collectors/disk.py) and the smart_disk
plugin (plugins/builtin/smart_disk.py) so the nsenter-then-direct-access
fallback and drive-discovery logic isn't duplicated (and can't drift) in
two places (BUG-27).
"""

from __future__ import annotations

import asyncio

from buoy.subprocess_utils import communicate


async def run_smartctl(*args: str, timeout: float = 5) -> str | None:
    """Run smartctl with nsenter (container with pid:host) first, then a
    direct call as a fallback for a native (non-container) install.

    Returns stdout, or None if both attempts failed.
    """
    nsenter_cmd = ["nsenter", "-t", "1", "-m", "--", "smartctl", *args]
    direct_cmd = ["smartctl", *args]

    for cmd in (nsenter_cmd, direct_cmd):
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
            )
            stdout, _ = await communicate(proc, timeout=timeout)
            if proc.returncode is not None and stdout:
                return stdout.decode()
        except (TimeoutError, FileNotFoundError):
            continue
    return None


async def scan_devices() -> list[tuple[str, str]]:
    """Auto-detect drives via `smartctl --scan`.

    Returns (device_path, type) pairs in scan order, e.g.
    [("/dev/nvme0", "nvme"), ("/dev/sda", "scsi")] — the type comes from
    smartctl's own `-d <type>` flag in its scan output.
    """
    output = await run_smartctl("--scan")
    if not output:
        return []

    devices = []
    for line in output.strip().split("\n"):
        parts = line.split()
        if not parts:
            continue
        device = parts[0]
        dev_type = ""
        if "-d" in parts:
            idx = parts.index("-d")
            if idx + 1 < len(parts):
                dev_type = parts[idx + 1]
        devices.append((device, dev_type))
    return devices


async def scan_nvme_devices() -> list[str]:
    """Return just the NVMe device paths from a smartctl --scan, in scan order."""
    return [device for device, dev_type in await scan_devices() if dev_type == "nvme"]
