"""Tests for buoy.capabilities — the /api/health subsystem probe module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from buoy import capabilities
from buoy.config import BuoyConfig, FeaturesConfig


def _make_config(demo_mode=False):
    config = BuoyConfig()
    config.features = FeaturesConfig(demo_mode=demo_mode)
    return config


class _StubDockerCollector:
    def __init__(self, available: bool):
        self._available = available
        self.calls = []

    async def is_available(self, *, force: bool = False) -> bool:
        self.calls.append(force)
        return self._available


class _RaisingDockerCollector:
    async def is_available(self, *, force: bool = False) -> bool:
        raise RuntimeError("boom")


class _StubDiskCollector:
    def __init__(self, mounts):
        self._mounts = mounts

    def _local_mounts(self):
        return self._mounts


class _StubGpuCollector:
    def __init__(self, available):
        self._available = available


class TestDemoMode:
    @pytest.mark.asyncio
    async def test_demo_mode_returns_all_ok_and_probes_nothing(self):
        config = _make_config(demo_mode=True)

        with (
            patch("asyncio.create_subprocess_exec") as mock_exec,
            patch("buoy.smartctl.run_smartctl") as mock_smartctl,
        ):
            result = await capabilities.probe(config)

        mock_exec.assert_not_called()
        mock_smartctl.assert_not_called()
        assert result["docker"]["status"] == "ok"
        assert result["proc"]["status"] == "ok"


class TestDockerProbe:
    @pytest.mark.asyncio
    async def test_available_docker_reports_ok(self):
        result = await capabilities._probe_docker(_StubDockerCollector(True))
        assert result["status"] == "ok"
        assert result["impact"] == ""

    @pytest.mark.asyncio
    async def test_unavailable_docker_reports_impact(self):
        result = await capabilities._probe_docker(_StubDockerCollector(False))
        assert result["status"] == "unavailable"
        assert "service discovery" in result["impact"]

    @pytest.mark.asyncio
    async def test_forces_a_fresh_probe(self):
        stub = _StubDockerCollector(True)
        await capabilities._probe_docker(stub)
        assert stub.calls == [True]

    @pytest.mark.asyncio
    async def test_none_collector_reports_unavailable(self):
        result = await capabilities._probe_docker(None)
        assert result["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_raising_collector_reports_unavailable_not_exception(self):
        result = await capabilities._probe_docker(_RaisingDockerCollector())
        assert result["status"] == "unavailable"


def _make_proc(returncode: int = 0):
    proc = AsyncMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(b"", b""))
    return proc


class TestRunningInContainer:
    def test_dockerenv_present_reports_true(self, fake_fs):
        fake_fs.set_file("/.dockerenv", "")
        assert capabilities._running_in_container() is True

    def test_cgroup_docker_marker_reports_true(self, fake_fs):
        fake_fs.set_file("/proc/1/cgroup", "0::/docker/abc123\n")
        assert capabilities._running_in_container() is True

    def test_cgroup_kubepods_marker_reports_true(self, fake_fs):
        fake_fs.set_file("/proc/1/cgroup", "0::/kubepods/besteffort/pod123\n")
        assert capabilities._running_in_container() is True

    def test_no_markers_reports_false(self, fake_fs):
        fake_fs.set_file("/proc/1/cgroup", "0::/init.scope\n")
        assert capabilities._running_in_container() is False

    def test_nothing_readable_reports_false(self, fake_fs):
        assert capabilities._running_in_container() is False


class TestNsenterProbe:
    @pytest.mark.asyncio
    async def test_successful_nsenter_reports_ok(self):
        with patch("asyncio.create_subprocess_exec", return_value=_make_proc(0)):
            result = await capabilities._probe_nsenter(None)
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_missing_binary_falls_back_to_local_mounts(self):
        with (
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()),
            patch("platform.system", return_value="Linux"),
            patch("buoy.capabilities._running_in_container", return_value=False),
        ):
            result = await capabilities._probe_nsenter(_StubDiskCollector([{"mount": "/"}]))
        assert result["status"] == "not_applicable"

    @pytest.mark.asyncio
    async def test_missing_binary_and_no_local_mounts_reports_unavailable(self):
        with (
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()),
            patch("buoy.capabilities._running_in_container", return_value=False),
        ):
            result = await capabilities._probe_nsenter(_StubDiskCollector([]))
        assert result["status"] == "unavailable"
        assert "impact" in result

    @pytest.mark.asyncio
    async def test_missing_binary_inside_container_reports_unavailable_despite_local_mounts(self):
        """A Tier 3 container (see docs/deployment/privilege-matrix.md) sees
        its own mounts just fine via the /proc/mounts fallback — but that's
        not the host's real mount list, so it must not read as
        not_applicable the way a native install's local view does."""
        with (
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()),
            patch("platform.system", return_value="Linux"),
            patch("buoy.capabilities._running_in_container", return_value=True),
        ):
            result = await capabilities._probe_nsenter(_StubDiskCollector([{"mount": "/"}]))
        assert result["status"] == "unavailable"
        assert "impact" in result

    @pytest.mark.asyncio
    async def test_timeout_falls_back_like_missing_binary(self):
        with patch("asyncio.create_subprocess_exec", side_effect=TimeoutError()):
            result = await capabilities._probe_nsenter(None)
        assert result["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_nonzero_returncode_reports_unavailable(self):
        with patch("asyncio.create_subprocess_exec", return_value=_make_proc(1)):
            result = await capabilities._probe_nsenter(None)
        assert result["status"] == "unavailable"


class TestSmartctlProbe:
    @pytest.mark.asyncio
    async def test_output_present_reports_ok(self):
        with patch("buoy.capabilities.run_smartctl", AsyncMock(return_value="smartctl 7.3")):
            result = await capabilities._probe_smartctl()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_no_output_reports_unavailable(self):
        with patch("buoy.capabilities.run_smartctl", AsyncMock(return_value=None)):
            result = await capabilities._probe_smartctl()
        assert result["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_raising_run_smartctl_reports_unavailable(self):
        with patch("buoy.capabilities.run_smartctl", AsyncMock(side_effect=RuntimeError("boom"))):
            result = await capabilities._probe_smartctl()
        assert result["status"] == "unavailable"


class TestProcProbe:
    @pytest.mark.asyncio
    async def test_present_reports_ok(self, fake_procfs):
        with patch("platform.system", return_value="Linux"):
            result = await capabilities._probe_proc()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_missing_reports_unavailable(self, fake_fs):
        with patch("platform.system", return_value="Linux"):
            result = await capabilities._probe_proc()
        assert result["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_non_linux_reports_not_applicable(self):
        with patch("platform.system", return_value="Darwin"):
            result = await capabilities._probe_proc()
        assert result["status"] == "not_applicable"


class TestSysThermalProbe:
    @pytest.mark.asyncio
    async def test_hwmon_present_reports_ok(self, fake_fs):
        fake_fs.set_dir("/sys/class/hwmon", ["hwmon0"])
        with patch("platform.system", return_value="Linux"):
            result = await capabilities._probe_sys_thermal()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_thermal_present_reports_ok(self, fake_fs):
        fake_fs.set_dir("/sys/class/thermal", ["thermal_zone0"])
        with patch("platform.system", return_value="Linux"):
            result = await capabilities._probe_sys_thermal()
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_neither_present_reports_unavailable(self, fake_fs):
        with patch("platform.system", return_value="Linux"):
            result = await capabilities._probe_sys_thermal()
        assert result["status"] == "unavailable"

    @pytest.mark.asyncio
    async def test_non_linux_reports_not_applicable(self):
        with patch("platform.system", return_value="Darwin"):
            result = await capabilities._probe_sys_thermal()
        assert result["status"] == "not_applicable"


class TestGpuProbe:
    @pytest.mark.asyncio
    async def test_none_collector_returns_none(self):
        assert await capabilities._probe_gpu(None) is None

    @pytest.mark.asyncio
    async def test_available_gpu_reports_ok(self):
        result = await capabilities._probe_gpu(_StubGpuCollector(True))
        assert result["status"] == "ok"

    @pytest.mark.asyncio
    async def test_no_gpu_present_reports_not_applicable(self):
        """features.gpu defaults on and no-ops without a GPU — absence must
        not read as degraded on an ordinary GPU-less host."""
        result = await capabilities._probe_gpu(_StubGpuCollector(False))
        assert result["status"] == "not_applicable"

    @pytest.mark.asyncio
    async def test_not_yet_probed_reports_not_applicable(self):
        """_available is None until the collector's first stats-endpoint
        probe; this must not read as degraded during the startup window."""
        result = await capabilities._probe_gpu(_StubGpuCollector(None))
        assert result["status"] == "not_applicable"


class TestProbeIntegration:
    @pytest.mark.asyncio
    async def test_probe_never_raises_when_every_check_fails(self, fake_fs):
        config = _make_config(demo_mode=False)
        with (
            patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("no host access")),
            patch("buoy.capabilities.run_smartctl", AsyncMock(side_effect=RuntimeError("boom"))),
            patch("platform.system", return_value="Linux"),
        ):
            result = await capabilities.probe(
                config,
                docker_collector=_RaisingDockerCollector(),
                disk_collector=None,
                gpu_collector=None,
            )

        assert result["docker"]["status"] == "unavailable"
        assert result["nsenter"]["status"] == "unavailable"
        assert result["smartctl"]["status"] == "unavailable"
        assert result["proc"]["status"] == "unavailable"
        assert result["sys_thermal"]["status"] == "unavailable"
        assert "gpu" not in result

    @pytest.mark.asyncio
    async def test_probe_includes_gpu_only_when_collector_given(self):
        config = _make_config(demo_mode=False)
        with (
            patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError()),
            patch("buoy.capabilities.run_smartctl", AsyncMock(return_value=None)),
            patch("platform.system", return_value="Linux"),
        ):
            result = await capabilities.probe(
                config,
                docker_collector=None,
                disk_collector=None,
                gpu_collector=_StubGpuCollector(True),
            )

        assert result["gpu"]["status"] == "ok"
