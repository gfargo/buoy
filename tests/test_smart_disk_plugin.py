"""Tests for the SMART disk health plugin (SATA + NVMe)."""

from unittest.mock import AsyncMock, patch

import pytest


class TestSmartDiskPlugin:
    def _make_plugin(self, drives=None):
        from buoy.plugins.builtin.smart_disk import SmartDiskPlugin

        plugin = SmartDiskPlugin()
        plugin.configure({"drives": drives or []})
        return plugin

    SATA_PASSED = b"""
smartctl 7.3 2022-02-28 r5338
SMART overall-health self-assessment test result: PASSED

ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       0
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       12000
194 Temperature_Celsius     0x0022   031   044   000    Old_age   Always       -       38
"""

    SATA_WARN = b"""
smartctl 7.3 2022-02-28 r5338
SMART overall-health self-assessment test result: PASSED

ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       7
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       8500
194 Temperature_Celsius     0x0022   031   044   000    Old_age   Always       -       41
"""

    SATA_FAILED = b"""
smartctl 7.3 2022-02-28 r5338
SMART overall-health self-assessment test result: FAILED!

ID# ATTRIBUTE_NAME          FLAG     VALUE WORST THRESH TYPE      UPDATED  WHEN_FAILED RAW_VALUE
  5 Reallocated_Sector_Ct   0x0033   100   100   010    Pre-fail  Always       -       512
  9 Power_On_Hours          0x0032   099   099   000    Old_age   Always       -       43000
194 Temperature_Celsius     0x0022   031   044   000    Old_age   Always       -       55
"""

    NVME_OUTPUT = b"""
smartctl 7.3 2022-02-28 r5338
SMART overall-health self-assessment test result: PASSED

NVMe Log Sense (Log Identifier: 0x02)
Critical Warning:                   0x00
Temperature:                        38 Celsius
Available Spare:                    100%
Available Spare Threshold:          10%
Percentage Used:                    1%
Power On Hours:                     3500
"""

    SCAN_OUTPUT = b"/dev/sda -d scsi # /dev/sda [SCSI disk], please try 'smartctl -a /dev/sda'\n/dev/nvme0 -d nvme # /dev/nvme0 [NVMe device]\n"

    def _make_proc(self, stdout: bytes, returncode: int = 0):
        mock_proc = AsyncMock()
        mock_proc.returncode = returncode
        mock_proc.communicate = AsyncMock(return_value=(stdout, b""))
        return mock_proc

    @pytest.mark.asyncio
    async def test_sata_ok(self):
        plugin = self._make_plugin(drives=["/dev/sda"])
        proc = self._make_proc(self.SATA_PASSED)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await plugin.collect()
        assert result.status == "ok"
        assert "1 drive OK" in result.summary
        drive = result.detail["drives"][0]
        assert drive["health"] == "PASSED"
        assert drive["reallocated"] == 0
        assert drive["temp"] == 38
        assert drive["power_hours"] == 12000

    @pytest.mark.asyncio
    async def test_sata_warn_reallocated(self):
        plugin = self._make_plugin(drives=["/dev/sda"])
        proc = self._make_proc(self.SATA_WARN)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await plugin.collect()
        assert result.status == "warn"
        assert "reallocated sectors" in result.summary
        assert result.detail["drives"][0]["reallocated"] == 7

    @pytest.mark.asyncio
    async def test_sata_error_failed(self):
        plugin = self._make_plugin(drives=["/dev/sda"])
        proc = self._make_proc(self.SATA_FAILED)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await plugin.collect()
        assert result.status == "error"
        assert "SMART FAILED" in result.summary

    @pytest.mark.asyncio
    async def test_nvme_parses_kv(self):
        plugin = self._make_plugin(drives=["/dev/nvme0"])
        proc = self._make_proc(self.NVME_OUTPUT)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await plugin.collect()
        assert result.status == "ok"
        drive = result.detail["drives"][0]
        assert drive["health"] == "PASSED"
        assert drive["temp"] == 38
        assert drive["power_hours"] == 3500

    @pytest.mark.asyncio
    async def test_auto_scan(self):
        plugin = self._make_plugin()  # no drives whitelist

        scan_proc = self._make_proc(self.SCAN_OUTPUT)
        drive_proc = self._make_proc(self.SATA_PASSED)

        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return scan_proc
            return drive_proc

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
            result = await plugin.collect()

        assert result.status in ("ok", "warn", "error", "disabled")
        assert call_count[0] >= 2  # scan + at least one drive read

    @pytest.mark.asyncio
    async def test_no_smartctl_returns_disabled(self):
        plugin = self._make_plugin()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("smartctl")):
            result = await plugin.collect()
        assert result.status == "disabled"
        assert "No drives" in result.summary

    @pytest.mark.asyncio
    async def test_empty_scan_returns_disabled(self):
        plugin = self._make_plugin()
        proc = self._make_proc(b"")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await plugin.collect()
        assert result.status == "disabled"

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_smart_disk" in js
