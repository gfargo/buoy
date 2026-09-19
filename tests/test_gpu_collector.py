"""Tests for the GPU collector (NVIDIA/AMD/Intel detection + parsing)."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig
from buoy.demo import DemoGpuCollector


def _make_config(name="test-node"):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.features = FeaturesConfig()
    return config


# ── NVIDIA parsing ───────────────────────────────────────────────────────────


class TestNvidiaProbe:
    @pytest.mark.asyncio
    async def test_two_gpus_parsed_correctly(self):
        from buoy.collectors.gpu import _probe_nvidia

        csv = (
            "0, NVIDIA GeForce RTX 3060, 42, 4096, 12288, 61, 95.50, 170.00\n"
            "1, NVIDIA GeForce RTX 4090, 5, 1024, 24576, 40, 30.00, 450.00\n"
        )
        with patch("buoy.collectors.gpu._run_gpu_tool", new=AsyncMock(return_value=csv)):
            gpus = await _probe_nvidia()

        assert len(gpus) == 2
        first = gpus[0]
        assert first["vendor"] == "nvidia"
        assert first["index"] == 0
        assert first["name"] == "NVIDIA GeForce RTX 3060"
        assert first["util_pct"] == 42
        assert first["mem_used_mb"] == 4096
        assert first["mem_total_mb"] == 12288
        assert first["temp"] == 61
        assert first["power_w"] == 95.5
        assert first["power_limit_w"] == 170
        assert gpus[1]["index"] == 1
        assert gpus[1]["name"] == "NVIDIA GeForce RTX 4090"

    @pytest.mark.asyncio
    async def test_na_and_not_supported_tokens_become_none_not_zero(self):
        from buoy.collectors.gpu import _probe_nvidia

        csv = "0, Tesla T4, [N/A], [Not Supported], 16384, [N/A], [N/A], [N/A]\n"
        with patch("buoy.collectors.gpu._run_gpu_tool", new=AsyncMock(return_value=csv)):
            gpus = await _probe_nvidia()

        assert len(gpus) == 1
        gpu = gpus[0]
        assert gpu["util_pct"] is None
        assert gpu["mem_used_mb"] is None
        assert gpu["mem_total_mb"] == 16384
        assert gpu["temp"] is None
        assert gpu["power_w"] is None
        assert gpu["power_limit_w"] is None

    @pytest.mark.asyncio
    async def test_nvidia_smi_missing_returns_empty_list(self):
        from buoy.collectors.gpu import _probe_nvidia

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("nvidia-smi not found"),
        ):
            gpus = await _probe_nvidia()

        assert gpus == []

    @pytest.mark.asyncio
    async def test_nvidia_smi_timeout_returns_empty_list_no_exception(self):
        from buoy.collectors.gpu import _probe_nvidia

        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=object())),
            patch("buoy.collectors.gpu.communicate", new=AsyncMock(side_effect=TimeoutError())),
        ):
            gpus = await _probe_nvidia()

        assert gpus == []

    @pytest.mark.asyncio
    async def test_compute_apps_parsed_into_processes(self):
        from buoy.collectors.gpu import _probe_nvidia_processes

        csv = "4821, ffmpeg, 1024\n5290, ollama, 3072\n"
        with patch("buoy.collectors.gpu._run_gpu_tool", new=AsyncMock(return_value=csv)):
            processes = await _probe_nvidia_processes()

        assert processes == [
            {"pid": 4821, "name": "ffmpeg", "mem_mb": 1024},
            {"pid": 5290, "name": "ollama", "mem_mb": 3072},
        ]


# ── AMD sysfs parsing ────────────────────────────────────────────────────────


def _write(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content)


class TestAmdProbe:
    @pytest.mark.asyncio
    async def test_amd_card_parsed_from_sysfs(self, tmp_path):
        import buoy.collectors.gpu as gpu_module

        card = tmp_path / "card0"
        device = card / "device"
        _write(device / "vendor", "0x1002\n")
        _write(device / "gpu_busy_percent", "17\n")
        _write(device / "mem_info_vram_used", str(2 * 1024 * 1024 * 1024) + "\n")
        _write(device / "mem_info_vram_total", str(8 * 1024 * 1024 * 1024) + "\n")
        _write(device / "hwmon" / "hwmon3" / "temp1_input", "54000\n")
        _write(device / "hwmon" / "hwmon3" / "power1_average", "45000000\n")
        _write(device / "hwmon" / "hwmon3" / "power1_cap", "120000000\n")

        with patch.object(gpu_module, "_DRM_BASE", tmp_path):
            gpus = await gpu_module._probe_amd()

        assert len(gpus) == 1
        gpu = gpus[0]
        assert gpu["vendor"] == "amd"
        assert gpu["util_pct"] == 17
        assert gpu["mem_used_mb"] == 2048.0
        assert gpu["mem_total_mb"] == 8192.0
        assert gpu["temp"] == 54.0
        assert gpu["power_w"] == 45.0
        assert gpu["power_limit_w"] == 120.0

    @pytest.mark.asyncio
    async def test_amd_card_without_busy_percent_still_listed_with_none_util(self, tmp_path):
        """Older kernels lack gpu_busy_percent — the GPU is still reported,
        just with util_pct=None rather than being dropped entirely."""
        import buoy.collectors.gpu as gpu_module

        card = tmp_path / "card0"
        device = card / "device"
        _write(device / "vendor", "0x1002\n")
        _write(device / "mem_info_vram_used", str(1024 * 1024) + "\n")
        _write(device / "mem_info_vram_total", str(4 * 1024 * 1024) + "\n")

        with patch.object(gpu_module, "_DRM_BASE", tmp_path):
            gpus = await gpu_module._probe_amd()

        assert len(gpus) == 1
        assert gpus[0]["util_pct"] is None
        assert gpus[0]["mem_total_mb"] == 4.0

    @pytest.mark.asyncio
    async def test_pi_and_connector_dirs_are_not_reported_as_gpus(self, tmp_path):
        """A Raspberry Pi's vc4 card0 (no vendor file) plus a connector
        subdirectory (card0-HDMI-A-1) must not surface as GPUs."""
        import buoy.collectors.gpu as gpu_module
        from buoy.collectors.gpu import GpuCollector

        (tmp_path / "card0" / "device").mkdir(parents=True)
        (tmp_path / "card0-HDMI-A-1").mkdir(parents=True)

        with patch.object(gpu_module, "_DRM_BASE", tmp_path):
            amd_gpus = await gpu_module._probe_amd()
            intel_gpus = await GpuCollector(_make_config())._probe_intel()

        assert amd_gpus == []
        assert intel_gpus == []

    @pytest.mark.asyncio
    async def test_no_drm_directory_returns_empty(self, tmp_path):
        import buoy.collectors.gpu as gpu_module

        with patch.object(gpu_module, "_DRM_BASE", tmp_path / "does-not-exist"):
            gpus = await gpu_module._probe_amd()

        assert gpus == []


# ── Intel sysfs parsing ──────────────────────────────────────────────────────


class TestIntelProbe:
    @pytest.mark.asyncio
    async def test_intel_card_reports_identity_and_freq_with_null_util(self, tmp_path):
        import buoy.collectors.gpu as gpu_module
        from buoy.collectors.gpu import GpuCollector

        card = tmp_path / "card0"
        device = card / "device"
        _write(device / "vendor", "0x8086\n")
        _write(device / "gt_cur_freq_mhz", "300\n")
        _write(device / "gt_max_freq_mhz", "1350\n")

        coll = GpuCollector(_make_config())

        with (
            patch.object(gpu_module, "_DRM_BASE", tmp_path),
            patch("shutil.which", return_value=None),
        ):
            gpus = await coll._probe_intel()

        assert len(gpus) == 1
        gpu = gpus[0]
        assert gpu["vendor"] == "intel"
        assert gpu["util_pct"] is None
        assert gpu["util_note"]
        assert gpu["freq_mhz"] == 300
        assert gpu["freq_max_mhz"] == 1350

    @pytest.mark.asyncio
    async def test_intel_util_populated_when_gpu_top_available(self, tmp_path):
        """When intel_gpu_top is present and returns a busy% reading, the
        card's util_pct is populated and util_note is cleared."""
        import buoy.collectors.gpu as gpu_module
        from buoy.collectors.gpu import GpuCollector

        card = tmp_path / "card0"
        _write(card / "device" / "vendor", "0x8086\n")

        coll = GpuCollector(_make_config())
        gpu_top_json = '[{"engines": {"Render/3D": {"busy": 37.5}}}]'

        with (
            patch.object(gpu_module, "_DRM_BASE", tmp_path),
            patch("shutil.which", return_value="/usr/bin/intel_gpu_top"),
            patch(
                "buoy.collectors.gpu._run_gpu_tool",
                new=AsyncMock(return_value=gpu_top_json),
            ),
        ):
            gpus = await coll._probe_intel()

        assert gpus[0]["util_pct"] == 37.5
        assert gpus[0]["util_note"] is None

    @pytest.mark.asyncio
    async def test_intel_gpu_top_unavailability_is_cached(self, tmp_path):
        """Once intel_gpu_top is found unavailable, later calls don't
        re-invoke shutil.which — mirrors the outer detection cache."""
        import buoy.collectors.gpu as gpu_module
        from buoy.collectors.gpu import GpuCollector

        card = tmp_path / "card0"
        _write(card / "device" / "vendor", "0x8086\n")

        coll = GpuCollector(_make_config())

        with (
            patch.object(gpu_module, "_DRM_BASE", tmp_path),
            patch("shutil.which", return_value=None) as which_mock,
        ):
            await coll._probe_intel()
            await coll._probe_intel()

        assert which_mock.call_count == 1


class TestParseIntelGpuTopBusy:
    def test_parses_well_formed_json_array(self):
        from buoy.collectors.gpu import _parse_intel_gpu_top_busy

        output = '[{"engines": {"Render/3D": {"busy": 12.3}}}]'
        assert _parse_intel_gpu_top_busy(output) == 12.3

    def test_tolerates_unterminated_trailing_comma(self):
        """intel_gpu_top -J is a streaming monitor killed mid-write by our
        timeout, so the array is often missing its closing bracket and has
        a trailing comma."""
        from buoy.collectors.gpu import _parse_intel_gpu_top_busy

        output = '{"engines": {"Render/3D": {"busy": 8.0}}},'
        assert _parse_intel_gpu_top_busy(output) == 8.0

    def test_returns_none_for_garbage_output(self):
        from buoy.collectors.gpu import _parse_intel_gpu_top_busy

        assert _parse_intel_gpu_top_busy("not json at all {{{") is None

    def test_returns_none_when_engines_missing(self):
        from buoy.collectors.gpu import _parse_intel_gpu_top_busy

        assert _parse_intel_gpu_top_busy('[{"engines": {}}]') is None

    def test_returns_none_for_empty_frames(self):
        from buoy.collectors.gpu import _parse_intel_gpu_top_busy

        assert _parse_intel_gpu_top_busy("[]") is None


# ── GpuCollector detection + caching ─────────────────────────────────────────


class TestGpuCollectorDetectionAndCaching:
    @pytest.mark.asyncio
    async def test_config_attribute_set(self):
        from buoy.collectors.gpu import GpuCollector

        config = _make_config()
        coll = GpuCollector(config)
        assert coll.config is config

    @pytest.mark.asyncio
    async def test_no_gpu_returns_empty_dict_not_empty_gpus_key(self):
        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=AsyncMock(return_value=[])),
            patch("buoy.collectors.gpu._probe_amd", new=AsyncMock(return_value=[])),
            patch.object(coll, "_probe_intel", new=AsyncMock(return_value=[])),
        ):
            summary = await coll.collect_summary()

        assert summary == {}
        assert "gpus" not in summary

    @pytest.mark.asyncio
    async def test_gpu_less_host_probes_only_once_across_many_calls(self):
        """Detection is a permanent gate (mirrors DockerCollector.is_available):
        once no GPU is found, later ticks must not spawn probe subprocesses."""
        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        nvidia_mock = AsyncMock(return_value=[])
        amd_mock = AsyncMock(return_value=[])
        intel_mock = AsyncMock(return_value=[])

        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=nvidia_mock),
            patch("buoy.collectors.gpu._probe_amd", new=amd_mock),
            patch.object(coll, "_probe_intel", new=intel_mock),
        ):
            for _ in range(5):
                await coll.collect_summary()

        assert nvidia_mock.await_count == 1
        assert amd_mock.await_count == 1
        assert intel_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_gpu_present_returns_expected_fields(self):
        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        nvidia_gpu = {
            "vendor": "nvidia",
            "index": 0,
            "name": "NVIDIA GeForce RTX 3060",
            "util_pct": 42,
            "mem_used_mb": 4096,
            "mem_total_mb": 12288,
            "temp": 61,
            "power_w": 95.5,
            "power_limit_w": 170,
        }
        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=AsyncMock(return_value=[nvidia_gpu])),
            patch("buoy.collectors.gpu._probe_amd", new=AsyncMock(return_value=[])),
            patch.object(coll, "_probe_intel", new=AsyncMock(return_value=[])),
        ):
            summary = await coll.collect_summary()

        assert summary == {"gpus": [nvidia_gpu]}

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_uses_cache(self):
        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        gpu = {"vendor": "nvidia", "index": 0, "name": "GPU"}
        nvidia_mock = AsyncMock(return_value=[gpu])

        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=nvidia_mock),
            patch("buoy.collectors.gpu._probe_amd", new=AsyncMock(return_value=[])),
            patch.object(coll, "_probe_intel", new=AsyncMock(return_value=[])),
        ):
            await coll.collect_summary()
            await coll.collect_summary()

        assert nvidia_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl_but_only_reprobes_present_vendors(self):
        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        gpu = {"vendor": "nvidia", "index": 0, "name": "GPU"}
        nvidia_mock = AsyncMock(return_value=[gpu])
        amd_mock = AsyncMock(return_value=[])
        intel_mock = AsyncMock(return_value=[])

        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=nvidia_mock),
            patch("buoy.collectors.gpu._probe_amd", new=amd_mock),
            patch.object(coll, "_probe_intel", new=intel_mock),
        ):
            await coll.collect_summary()
            coll._gpus_cache_ts -= 6  # simulate TTL expiry (_GPU_CACHE_TTL = 5.0)
            await coll.collect_summary()

        assert nvidia_mock.await_count == 2
        # amd/intel found nothing on the first (detecting) call, so they're
        # skipped on the refresh — only the vendor that was actually found
        # keeps getting re-probed.
        assert amd_mock.await_count == 1
        assert intel_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_calls_only_probe_once(self):
        import asyncio

        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        nvidia_mock = AsyncMock(return_value=[])

        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=nvidia_mock),
            patch("buoy.collectors.gpu._probe_amd", new=AsyncMock(return_value=[])),
            patch.object(coll, "_probe_intel", new=AsyncMock(return_value=[])),
        ):
            await asyncio.gather(
                coll.collect_summary(), coll.collect_summary(), coll.collect_summary()
            )

        assert nvidia_mock.await_count == 1

    @pytest.mark.asyncio
    async def test_collect_detail_includes_processes_only_for_nvidia(self):
        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        nvidia_gpu = {"vendor": "nvidia", "index": 0, "name": "GPU"}
        processes = [{"pid": 123, "name": "ffmpeg", "mem_mb": 512}]

        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=AsyncMock(return_value=[nvidia_gpu])),
            patch("buoy.collectors.gpu._probe_amd", new=AsyncMock(return_value=[])),
            patch.object(coll, "_probe_intel", new=AsyncMock(return_value=[])),
            patch(
                "buoy.collectors.gpu._probe_nvidia_processes",
                new=AsyncMock(return_value=processes),
            ),
        ):
            detail = await coll.collect_detail()

        assert detail == {"gpus": [nvidia_gpu], "processes": processes}

    @pytest.mark.asyncio
    async def test_collect_detail_processes_empty_for_amd_only(self):
        from buoy.collectors.gpu import GpuCollector

        coll = GpuCollector(_make_config())
        amd_gpu = {"vendor": "amd", "index": 0, "name": "GPU"}

        with (
            patch("buoy.collectors.gpu._probe_nvidia", new=AsyncMock(return_value=[])),
            patch("buoy.collectors.gpu._probe_amd", new=AsyncMock(return_value=[amd_gpu])),
            patch.object(coll, "_probe_intel", new=AsyncMock(return_value=[])),
        ):
            detail = await coll.collect_detail()

        assert detail == {"gpus": [amd_gpu], "processes": []}


# ── Demo collector ────────────────────────────────────────────────────────────


class TestDemoGpuCollector:
    def test_config_attribute_set(self):
        config = _make_config()
        coll = DemoGpuCollector(config)
        assert coll.config is config

    @pytest.mark.asyncio
    async def test_collect_summary_has_one_plausible_gpu(self):
        coll = DemoGpuCollector(_make_config())
        data = await coll.collect_summary()

        assert "gpus" in data
        assert len(data["gpus"]) == 1
        gpu = data["gpus"][0]
        assert gpu["vendor"] == "nvidia"
        assert 0 <= gpu["util_pct"] <= 100

    @pytest.mark.asyncio
    async def test_collect_detail_has_processes(self):
        coll = DemoGpuCollector(_make_config())
        detail = await coll.collect_detail()

        assert len(detail["gpus"]) == 1
        assert len(detail["processes"]) == 2
