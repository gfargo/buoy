"""Tests for shared smartctl helpers (buoy.smartctl).

Extracted from the smart_disk plugin so drive discovery isn't duplicated
between it and the core NVMe gauge (BUG-27) — these tests cover the
extracted behavior directly.
"""

from unittest.mock import AsyncMock, patch

import pytest

from buoy.smartctl import run_smartctl, scan_devices, scan_nvme_devices


def _make_proc(stdout: bytes, returncode: int = 0):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, b""))
    return proc


SCAN_OUTPUT = (
    b"/dev/sda -d scsi # /dev/sda [SCSI disk], please try 'smartctl -a /dev/sda'\n"
    b"/dev/nvme0 -d nvme # /dev/nvme0 [NVMe device]\n"
    b"/dev/nvme1 -d nvme # /dev/nvme1 [NVMe device]\n"
)


class TestRunSmartctl:
    @pytest.mark.asyncio
    async def test_returns_stdout_on_success(self):
        proc = _make_proc(b"some output")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await run_smartctl("-a", "/dev/sda")
        assert result == "some output"

    @pytest.mark.asyncio
    async def test_falls_back_to_direct_when_nsenter_missing(self):
        """nsenter raising FileNotFoundError (not installed / not permitted)
        must fall through to a direct smartctl call, not propagate."""
        direct_proc = _make_proc(b"direct output")
        call_count = [0]

        def side_effect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise FileNotFoundError("nsenter")
            return direct_proc

        with patch("asyncio.create_subprocess_exec", side_effect=side_effect):
            result = await run_smartctl("-a", "/dev/sda")

        assert result == "direct output"
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_returns_none_when_both_attempts_fail(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("smartctl")):
            result = await run_smartctl("-a", "/dev/sda")
        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_empty_stdout(self):
        proc = _make_proc(b"")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            result = await run_smartctl("-a", "/dev/sda")
        assert result is None


class TestScanDevices:
    @pytest.mark.asyncio
    async def test_parses_device_and_type_pairs(self):
        proc = _make_proc(SCAN_OUTPUT)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            devices = await scan_devices()

        assert devices == [
            ("/dev/sda", "scsi"),
            ("/dev/nvme0", "nvme"),
            ("/dev/nvme1", "nvme"),
        ]

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_scan_unavailable(self):
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("smartctl")):
            devices = await scan_devices()
        assert devices == []


class TestScanNvmeDevices:
    @pytest.mark.asyncio
    async def test_filters_to_nvme_only_preserving_scan_order(self):
        """BUG-27: a host with a SATA boot drive and two NVMe drives must
        surface only the NVMe devices, in the order smartctl reported them —
        not just the first NVMe-looking path found."""
        proc = _make_proc(SCAN_OUTPUT)
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            devices = await scan_nvme_devices()

        assert devices == ["/dev/nvme0", "/dev/nvme1"]

    @pytest.mark.asyncio
    async def test_empty_when_no_nvme_present(self):
        proc = _make_proc(b"/dev/sda -d scsi # /dev/sda [SCSI disk]\n")
        with patch("asyncio.create_subprocess_exec", return_value=proc):
            devices = await scan_nvme_devices()
        assert devices == []
