"""Server integration tests for the GPU collector wiring.

Guards the one thing the implementation plan flagged as easy to get wrong:
adding gpu_coll.collect_summary() to api_stats()'s asyncio.gather() call is
positional — inserting it before top_services would silently shift that
result's index and corrupt (or crash) the "top_services" field.
"""

from unittest.mock import AsyncMock, patch

from starlette.testclient import TestClient

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, PluginsConfig
from buoy.server import create_app


def _make_config(*, demo_mode: bool, gpu: bool = True):
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.features = FeaturesConfig(websocket=False, demo_mode=demo_mode, gpu=gpu)
    config.plugins = PluginsConfig()
    return config


class TestGpuInDemoMode:
    def test_stats_includes_gpus(self):
        app = create_app(_make_config(demo_mode=True))
        with TestClient(app) as client:
            r = client.get("/api/stats")

        assert r.status_code == 200
        body = r.json()
        assert "gpus" in body
        assert len(body["gpus"]) == 1
        assert body["gpus"][0]["vendor"] == "nvidia"
        # top_services must still be present and untouched by the extra
        # gather() entry.
        assert "top_services" in body
        assert isinstance(body["top_services"], list)

    def test_stats_detail_includes_gpu_key(self):
        app = create_app(_make_config(demo_mode=True))
        with TestClient(app) as client:
            r = client.get("/api/stats/detail")

        assert r.status_code == 200
        body = r.json()
        assert "gpu" in body
        assert len(body["gpu"]["gpus"]) == 1
        assert len(body["gpu"]["processes"]) == 2

    def test_gpu_feature_disabled_omits_gpus_even_in_demo(self):
        app = create_app(_make_config(demo_mode=True, gpu=False))
        with TestClient(app) as client:
            r = client.get("/api/stats")

        assert r.status_code == 200
        assert "gpus" not in r.json()


class TestGpuAbsentOnRealHost:
    """A GPU-less host: nvidia-smi is absent and sysfs has no AMD/Intel
    card. GpuCollector degrades to finding nothing. _DRM_BASE is patched to
    a path with no drm devices (rather than relying on the sandbox lacking
    one) since this CI sandbox exposes its own Intel iGPU under
    /sys/class/drm — which is itself a nice sanity check for the collector,
    just not what this particular test wants to exercise."""

    def test_stats_omits_gpus_key_and_top_services_still_correct(self, tmp_path):
        app = create_app(_make_config(demo_mode=False))
        with (
            patch("buoy.collectors.gpu._DRM_BASE", tmp_path / "no-drm-here"),
            patch("buoy.collectors.gpu._probe_nvidia", new=AsyncMock(return_value=[])),
            TestClient(app) as client,
        ):
            r = client.get("/api/stats")

        assert r.status_code == 200
        body = r.json()
        assert "gpus" not in body
        assert "top_services" in body
        assert isinstance(body["top_services"], list)

    def test_stats_detail_gpu_key_present_but_empty(self, tmp_path):
        app = create_app(_make_config(demo_mode=False))
        with (
            patch("buoy.collectors.gpu._DRM_BASE", tmp_path / "no-drm-here"),
            patch("buoy.collectors.gpu._probe_nvidia", new=AsyncMock(return_value=[])),
            TestClient(app) as client,
        ):
            r = client.get("/api/stats/detail")

        assert r.status_code == 200
        body = r.json()
        assert body["gpu"] == {"gpus": [], "processes": []}
