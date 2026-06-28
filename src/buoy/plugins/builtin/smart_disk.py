"""SMART disk health plugin — SATA + NVMe drive health via smartctl."""

from __future__ import annotations

import asyncio
import re

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

    def frontend_js(self) -> str | None:
        return """
function render_smart_disk(data) {
  const drives = data.detail.drives || [];
  if (!drives.length) return '<div style="font-size:0.6rem;color:var(--text-dim)">No drives detected</div>';
  let html = '<table style="width:100%;border-collapse:collapse;font-size:0.5rem">';
  html += '<tr><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Device</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Health</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Temp</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Reallocated</th><th style="text-align:left;padding:0.2rem 0.4rem;color:var(--text-dim);border-bottom:1px solid var(--border)">Power Hours</th></tr>';
  drives.forEach(d => {
    const healthColor = d.health === 'PASSED' ? 'var(--green)' : d.health === 'FAILED' ? 'var(--red)' : 'var(--text-dim)';
    const reallocColor = (d.reallocated || 0) > 0 ? 'var(--yellow)' : 'var(--text)';
    html += '<tr>';
    html += '<td style="padding:0.2rem 0.4rem;color:var(--text);white-space:nowrap">' + d.device + '</td>';
    html += '<td style="padding:0.2rem 0.4rem;color:' + healthColor + '">' + d.health + '</td>';
    html += '<td style="padding:0.2rem 0.4rem;color:var(--text)">' + (d.temp != null ? d.temp + '°C' : '—') + '</td>';
    html += '<td style="padding:0.2rem 0.4rem;color:' + reallocColor + '">' + (d.reallocated != null ? d.reallocated : '—') + '</td>';
    html += '<td style="padding:0.2rem 0.4rem;color:var(--text-dim)">' + (d.power_hours != null ? d.power_hours + 'h' : '—') + '</td>';
    html += '</tr>';
  });
  html += '</table>';
  return html;
}
"""


# ── Parsing helpers ────────────────────────────────────────────────────────────


def _kv_int(output: str, key: str) -> int | None:
    """Parse 'Key:  value [unit]' NVMe format → int (first numeric token after key)."""
    for line in output.split("\n"):
        if key in line and ":" in line:
            after = line.split(":", 1)[1].strip()
            for token in after.split():
                try:
                    return int(token.replace(",", ""))
                except ValueError:
                    pass
    return None


def _sata_attr(output: str, attr_id: int) -> int | None:
    """Parse SATA attribute table line by ID; returns the raw value (last column)."""
    pattern = re.compile(rf"^\s*{attr_id}\s+\S+\s+.*?\s+(\d+)\s*$")
    for line in output.split("\n"):
        m = pattern.match(line)
        if m:
            try:
                return int(m.group(1))
            except ValueError:
                pass
    return None
