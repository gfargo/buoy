"""SMART disk health plugin — SATA + NVMe drive health via smartctl."""

from __future__ import annotations

import asyncio
import re

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest


class SmartDiskPlugin(Plugin):
    """Shows health, temperature, reallocated sectors, and power-on hours for all drives."""

    manifest = PluginManifest(
        id="smart_disk",
        name="Disks",
        icon="💾",
        description="SMART disk health (SATA + NVMe)",
        version="1.0.0",
        config_schema={"drives": {"type": "array", "default": []}},
        refresh_interval=300,
    )

    async def collect(self) -> PanelData:
        drives_cfg = self.config.get("drives") or []
        if drives_cfg:
            devices = list(drives_cfg)
        else:
            devices = await self._scan_drives()

        if not devices:
            return PanelData(status="disabled", summary="No drives found", detail={"drives": []})

        results = []
        for dev in devices:
            info = await self._read_drive(dev)
            if info:
                results.append(info)

        if not results:
            return PanelData(status="disabled", summary="No drives found", detail={"drives": []})

        # Aggregate status
        status = "ok"
        failed = [d for d in results if d["health"] == "FAILED"]
        reallocated = [d for d in results if d.get("reallocated", 0) > 0]

        if failed:
            status = "error"
            summary = f"{failed[0]['device']}: SMART FAILED"
        elif reallocated:
            status = "warn"
            summary = (
                "1 drive: reallocated sectors"
                if len(reallocated) == 1
                else f"{len(reallocated)} drives: reallocated sectors"
            )
        else:
            n = len(results)
            summary = f"{n} drive{'s' if n != 1 else ''} OK"

        return PanelData(status=status, summary=summary, detail={"drives": results})

    async def _scan_drives(self) -> list[str]:
        """Auto-detect drives via smartctl --scan (nsenter first, then direct)."""
        nsenter_cmd = ["nsenter", "-t", "1", "-m", "--", "smartctl", "--scan"]
        direct_cmd = ["smartctl", "--scan"]

        for cmd in (nsenter_cmd, direct_cmd):
            try:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
                if proc.returncode is not None and stdout:
                    devices = []
                    for line in stdout.decode().strip().split("\n"):
                        if not line:
                            continue
                        # Format: /dev/sda -d scsi # /dev/sda [SCSI disk], ...
                        parts = line.split()
                        if parts:
                            devices.append(parts[0])
                    return devices
            except (TimeoutError, FileNotFoundError):
                continue
        return []

    async def _read_drive(self, device: str) -> dict | None:
        """Run smartctl -A -H on a device and parse SMART data."""
        nsenter_cmd = ["nsenter", "-t", "1", "-m", "--", "smartctl", "-A", "-H", device]
        direct_cmd = ["smartctl", "-A", "-H", device]

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

        dev_name = device.split("/")[-1]

        # Parse health from -H output
        health = "UNKNOWN"
        for line in output.split("\n"):
            if "SMART overall-health self-assessment test result:" in line:
                health = "PASSED" if "PASSED" in line else "FAILED"
                break
            # NVMe: "SMART/Health Information" section — health implied if no critical warnings
            if "Critical Warning:" in line:
                health = "PASSED" if line.split()[-1] == "0x00" else "FAILED"
                break

        # Detect drive type: NVMe key:value vs SATA attribute table
        is_nvme = "NVMe" in output or "nvme" in device.lower()

        if is_nvme:
            temp = _kv_int(output, "Temperature:")
            power_hours = _kv_int(output, "Power On Hours:")
            reallocated = 0  # NVMe uses different wear indicators
        else:
            temp = _sata_attr(output, 194)
            if temp is None:
                temp = _sata_attr(output, 190)  # some drives use ID 190
            reallocated = _sata_attr(output, 5) or 0
            power_hours = _sata_attr(output, 9)

        return {
            "device": dev_name,
            "health": health,
            "temp": temp,
            "reallocated": reallocated,
            "power_hours": power_hours,
        }

    def render(self, data: PanelData) -> list[dict] | None:
        drives = data.detail.get("drives") or []
        if not drives:
            return [panel.text("No drives detected", status="dim")]

        rows = []
        for d in drives:
            health = d.get("health")
            health_status = "ok" if health == "PASSED" else "error" if health == "FAILED" else "dim"
            reallocated = d.get("reallocated")
            realloc_status = "warn" if (reallocated or 0) > 0 else None
            temp = d.get("temp")
            power_hours = d.get("power_hours")
            rows.append(
                [
                    panel.cell(d.get("device", "")),
                    panel.cell(health, status=health_status),
                    panel.cell(f"{temp}°C" if temp is not None else "—"),
                    panel.cell(
                        reallocated if reallocated is not None else "—", status=realloc_status
                    ),
                    panel.cell(f"{power_hours}h" if power_hours is not None else "—", status="dim"),
                ]
            )
        return [panel.table(["Device", "Health", "Temp", "Reallocated", "Power Hours"], rows)]


# ── Parsing helpers ────────────────────────────────────────────────────────────


def _kv_int(output: str, key: str) -> int | None:
    """Parse 'Key:  value [unit]' NVMe format → int (first numeric token after key)."""
    for line in output.split("\n"):
        if key in line and ":" in line:
            after = line.split(":", 1)[1].strip()
            for token in after.split():
                digits = token.replace(",", "")
                if digits.isdigit():
                    return int(digits)
    return None


def _sata_attr(output: str, attr_id: int) -> int | None:
    """Parse SATA attribute table line by ID; returns the raw value (last column)."""
    pattern = re.compile(rf"^\s*{attr_id}\s+\S+\s+.*?\s+(\d+)\s*$")
    for line in output.split("\n"):
        m = pattern.match(line)
        if m:
            return int(m.group(1))
    return None
