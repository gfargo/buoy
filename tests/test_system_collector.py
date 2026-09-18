"""Tests for the real SystemCollector's /proc and /sys parsers.

Complements the existing SystemCollector coverage in test_collectors.py
(memory, cpu delta, temperature sensor selection, cgroup quota, fallback
stats) with the paths that had no coverage at all: _read_cpu_detail,
_top_processes_by, _read_model's success path, uptime, the remaining
exception branches, and an end-to-end collect() over a full fixture /proc
tree.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from buoy.collectors.system import SystemCollector
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig


def _make_config(name="test-node"):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.network = NetworkConfig()
    config.features = FeaturesConfig()
    return config


class TestReadCpuDetail:
    """_read_cpu_detail() was previously entirely uncovered."""

    @pytest.mark.asyncio
    async def test_x86_model_name_found_in_cpuinfo(self, fake_fs):
        fake_fs.set_file(
            "/proc/cpuinfo",
            "processor\t: 0\nmodel name\t: Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz\n\n",
        )
        fake_fs.set_file("/proc/loadavg", "0.52 0.58 0.59 2/456 12345\n")

        coll = SystemCollector(_make_config())
        with (
            patch("os.cpu_count", return_value=8),
            patch.object(coll, "_top_processes_by", new=AsyncMock(return_value=[])),
        ):
            detail = await coll._read_cpu_detail()

        assert detail["model"] == "Intel(R) Core(TM) i7-9700K CPU @ 3.60GHz"
        assert detail["cores"] == 8
        assert detail["load_1"] == 0.52
        assert detail["load_5"] == 0.58
        assert detail["load_15"] == 0.59

    @pytest.mark.asyncio
    async def test_arm_no_model_name_falls_back_to_device_tree(self, fake_fs):
        """No "model name" line at all (ARM /proc/cpuinfo) — the for/else
        falls through to _read_model()'s /proc/device-tree/model read."""
        fake_fs.set_file("/proc/cpuinfo", "processor\t: 0\nHardware\t: BCM2835\n")
        fake_fs.set_file("/proc/device-tree/model", "Raspberry Pi 4 Model B Rev 1.2\x00")
        fake_fs.set_file("/proc/loadavg", "0.1 0.1 0.1 1/100 1\n")

        coll = SystemCollector(_make_config())
        with (
            patch("os.cpu_count", return_value=4),
            patch.object(coll, "_top_processes_by", new=AsyncMock(return_value=[])),
        ):
            detail = await coll._read_cpu_detail()

        assert detail["model"] == "Raspberry Pi 4 Model B Rev 1.2"

    @pytest.mark.asyncio
    async def test_cpuinfo_unreadable_reports_unknown_model(self, fake_fs):
        fake_fs.set_file("/proc/loadavg", "0.1 0.1 0.1 1/100 1\n")

        coll = SystemCollector(_make_config())
        with (
            patch("os.cpu_count", return_value=4),
            patch.object(coll, "_top_processes_by", new=AsyncMock(return_value=[])),
        ):
            detail = await coll._read_cpu_detail()

        assert detail["model"] == "unknown"

    @pytest.mark.asyncio
    async def test_loadavg_missing_defaults_to_zero(self, fake_fs):
        fake_fs.set_file("/proc/cpuinfo", "model name\t: Test CPU\n")

        coll = SystemCollector(_make_config())
        with (
            patch("os.cpu_count", return_value=4),
            patch.object(coll, "_top_processes_by", new=AsyncMock(return_value=[])),
        ):
            detail = await coll._read_cpu_detail()

        assert (detail["load_1"], detail["load_5"], detail["load_15"]) == (0.0, 0.0, 0.0)

    @pytest.mark.asyncio
    async def test_cores_comes_from_effective_cpu_cores(self, fake_fs):
        """cores must reflect a cgroup quota (via _effective_cpu_cores),
        not os.cpu_count() directly."""
        fake_fs.set_file("/proc/cpuinfo", "model name\t: Test CPU\n")
        fake_fs.set_file("/sys/fs/cgroup/cpu.max", "200000 100000\n")

        coll = SystemCollector(_make_config())
        with (
            patch("os.cpu_count", return_value=16),
            patch.object(coll, "_top_processes_by", new=AsyncMock(return_value=[])),
        ):
            detail = await coll._read_cpu_detail()

        assert detail["cores"] == 2

    @pytest.mark.asyncio
    async def test_top_processes_requested_sorted_by_cpu(self, fake_fs):
        fake_fs.set_file("/proc/cpuinfo", "model name\t: Test CPU\n")
        top_mock = AsyncMock(return_value=[{"pid": 1, "cpu": 5.0, "mem": 1.0, "cmd": "buoy"}])

        coll = SystemCollector(_make_config())
        with (
            patch("os.cpu_count", return_value=4),
            patch.object(coll, "_top_processes_by", new=top_mock),
        ):
            detail = await coll._read_cpu_detail()

        top_mock.assert_awaited_once_with("cpu")
        assert detail["top_processes"] == [{"pid": 1, "cpu": 5.0, "mem": 1.0, "cmd": "buoy"}]


class TestTopProcessesBy:
    """_top_processes_by() was previously entirely uncovered."""

    PS_HEADER = "USER PID %CPU %MEM VSZ RSS TTY STAT START TIME COMMAND"

    @staticmethod
    def _ps_line(pid, cpu, mem, cmd="python3 -m buoy"):
        return f"root {pid} {cpu} {mem} 100000 20000 ? Ss 09:00 0:01 {cmd}"

    async def _run(self, sort_key, stdout_text, limit=5):
        coll = SystemCollector(_make_config())
        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=object())) as p_exec,
            patch(
                "buoy.collectors.system.communicate",
                new=AsyncMock(return_value=(stdout_text.encode(), b"")),
            ),
        ):
            result = await coll._top_processes_by(sort_key, limit=limit)
        return result, p_exec

    @pytest.mark.asyncio
    async def test_cpu_sort_uses_cpu_flag(self):
        stdout = self.PS_HEADER + "\n" + self._ps_line(100, 50.0, 2.0)
        _, p_exec = await self._run("cpu", stdout)

        assert "--sort=-%cpu" in p_exec.call_args.args

    @pytest.mark.asyncio
    async def test_mem_sort_uses_mem_flag(self):
        stdout = self.PS_HEADER + "\n" + self._ps_line(100, 50.0, 2.0)
        _, p_exec = await self._run("mem", stdout)

        assert "--sort=-%mem" in p_exec.call_args.args

    @pytest.mark.asyncio
    async def test_header_line_dropped_and_limit_respected(self):
        lines = [self.PS_HEADER]
        lines += [self._ps_line(100 + i, 10.0, 1.0) for i in range(7)]
        stdout = "\n".join(lines)

        result, _ = await self._run("cpu", stdout, limit=5)

        assert len(result) == 5
        assert result[0]["pid"] == 100

    @pytest.mark.asyncio
    async def test_cmd_truncated_to_80_chars(self):
        long_cmd = "x" * 200
        stdout = self.PS_HEADER + "\n" + self._ps_line(100, 1.0, 1.0, cmd=long_cmd)

        result, _ = await self._run("cpu", stdout)

        assert len(result[0]["cmd"]) == 80

    @pytest.mark.asyncio
    async def test_lines_with_fewer_than_eleven_fields_skipped(self):
        stdout = self.PS_HEADER + "\n" + "root 100 1.0 1.0\n" + self._ps_line(200, 2.0, 2.0)

        result, _ = await self._run("cpu", stdout)

        assert len(result) == 1
        assert result[0]["pid"] == 200

    @pytest.mark.asyncio
    async def test_ps_not_found_returns_empty_list(self):
        coll = SystemCollector(_make_config())
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("no ps")):
            result = await coll._top_processes_by("cpu")

        assert result == []


class TestReadModel:
    def test_strips_trailing_nul(self, fake_fs):
        fake_fs.set_file("/proc/device-tree/model", "Raspberry Pi 4 Model B Rev 1.2\x00")

        coll = SystemCollector(_make_config())
        assert coll._read_model() == "Raspberry Pi 4 Model B Rev 1.2"

    def test_unreadable_returns_empty_string(self, fake_fs):
        coll = SystemCollector(_make_config())
        assert coll._read_model() == ""


class TestReadUptime:
    def test_success_splits_into_hours_minutes_and_total_seconds(self, fake_fs):
        fake_fs.set_file("/proc/uptime", "361234.56 720000.00\n")

        coll = SystemCollector(_make_config())
        # uptime_s is total elapsed seconds, not a remainder — downstream
        # consumers (prometheus_exporter.py) depend on the total.
        assert coll._read_uptime() == (100, 20, 361234)

    def test_unreadable_returns_zeros(self, fake_fs):
        coll = SystemCollector(_make_config())
        assert coll._read_uptime() == (0, 0, 0)


class TestSystemCollectorExceptionPaths:
    @pytest.mark.asyncio
    async def test_read_cpu_malformed_stat_returns_zero(self, fake_fs):
        fake_fs.set_file("/proc/stat", "not a valid stat line\n")

        coll = SystemCollector(_make_config())
        assert await coll._read_cpu() == 0

    def test_read_memory_unreadable_returns_zeros(self, fake_fs):
        coll = SystemCollector(_make_config())
        assert coll._read_memory() == (0.0, 0.0)

    def test_read_memory_detail_unreadable_returns_empty_dict(self, fake_fs):
        coll = SystemCollector(_make_config())
        assert coll._read_memory_detail() == {}


class TestReadTemperatureErrorBranches:
    """Branches of _read_temperature() not already covered by
    TestSystemCollectorTemperature in test_collectors.py."""

    def test_hwmon_entry_with_unreadable_name_is_skipped(self, fake_fs):
        fake_fs.set_dir("/sys/class/hwmon", ["hwmon0", "hwmon1"])
        # hwmon0 has no "name" file at all -> unreadable, must be skipped
        fake_fs.set_file("/sys/class/hwmon/hwmon1/name", "coretemp")
        fake_fs.set_file("/sys/class/hwmon/hwmon1/temp1_input", "45000")

        coll = SystemCollector(_make_config())
        assert coll._read_temperature() == 45

    def test_hwmon_entry_with_unrelated_name_is_skipped(self, fake_fs):
        """A non-CPU hwmon driver (e.g. an NVMe or wifi sensor) must be
        skipped in favor of a later matching entry, not just the first
        readable one."""
        fake_fs.set_dir("/sys/class/hwmon", ["hwmon0", "hwmon1"])
        fake_fs.set_file("/sys/class/hwmon/hwmon0/name", "nvme")
        fake_fs.set_file("/sys/class/hwmon/hwmon0/temp1_input", "35000")
        fake_fs.set_file("/sys/class/hwmon/hwmon1/name", "coretemp")
        fake_fs.set_file("/sys/class/hwmon/hwmon1/temp1_input", "45000")

        coll = SystemCollector(_make_config())
        assert coll._read_temperature() == 45

    def test_hwmon_match_with_garbage_temp_falls_through_to_thermal(self, fake_fs):
        fake_fs.set_dir("/sys/class/hwmon", ["hwmon0"])
        fake_fs.set_file("/sys/class/hwmon/hwmon0/name", "coretemp")
        fake_fs.set_file("/sys/class/hwmon/hwmon0/temp1_input", "not-a-number")
        fake_fs.set_dir("/sys/class/thermal", ["thermal_zone0"])
        fake_fs.set_file("/sys/class/thermal/thermal_zone0/type", "x86_pkg_temp")
        fake_fs.set_file("/sys/class/thermal/thermal_zone0/temp", "50000")

        coll = SystemCollector(_make_config())
        assert coll._read_temperature() == 50

    def test_missing_thermal_class_returns_none(self, fake_fs):
        coll = SystemCollector(_make_config())
        assert coll._read_temperature() is None

    def test_zone_with_unreadable_type_is_skipped(self, fake_fs):
        fake_fs.set_dir("/sys/class/thermal", ["thermal_zone0", "thermal_zone1"])
        # thermal_zone0 has no "type" file at all -> unreadable, skipped
        fake_fs.set_file("/sys/class/thermal/thermal_zone1/type", "acpitz")
        fake_fs.set_file("/sys/class/thermal/thermal_zone1/temp", "48000")

        coll = SystemCollector(_make_config())
        assert coll._read_temperature() == 48

    def test_preferred_zone_with_unreadable_temp_falls_through_to_next_preferred(self, fake_fs):
        fake_fs.set_dir("/sys/class/thermal", ["thermal_zone0", "thermal_zone1"])
        fake_fs.set_file("/sys/class/thermal/thermal_zone0/type", "x86_pkg_temp")
        # thermal_zone0's temp file is missing -> unreadable, must fall
        # through to the next preferred type (acpitz) rather than give up.
        fake_fs.set_file("/sys/class/thermal/thermal_zone1/type", "acpitz")
        fake_fs.set_file("/sys/class/thermal/thermal_zone1/temp", "39000")

        coll = SystemCollector(_make_config())
        assert coll._read_temperature() == 39


class TestCollectDetailNonLinux:
    @pytest.mark.asyncio
    async def test_returns_empty_cpu_and_memory_dicts(self):
        coll = SystemCollector(_make_config())
        coll._is_linux = False

        assert await coll.collect_detail() == {"cpu": {}, "memory": {}}


class TestSystemCollectorEndToEnd:
    """Pins the whole collect() formula chain together over one realistic
    /proc tree — the BUG-28/29/31 fixes only matter in combination."""

    @pytest.mark.asyncio
    async def test_collect_reads_full_proc_tree(self, fake_procfs):
        fake_procfs.set_file("/proc/device-tree/model", "Generic Test Board\x00")

        coll = SystemCollector(_make_config("pinned-node"))
        coll._is_linux = True

        data = await coll.collect()

        assert data["hostname"] == "pinned-node"
        assert data["model"] == "Generic Test Board"
        assert data["cpu"] == 0  # first sample since collector init, no delta yet
        assert data["mem_used"] == pytest.approx(7.0, abs=0.1)
        assert data["mem_total"] == pytest.approx(15.6, abs=0.1)
        assert data["temp"] is None  # no hwmon/thermal sysfs present in this fixture
        assert data["uptime_h"] == 100
        assert data["uptime_m"] == 20
        assert data["uptime_s"] == 361234
