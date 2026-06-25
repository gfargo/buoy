"""Tests for Buoy collectors (using mocked data / demo collectors)."""

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.demo import DemoDiskCollector, DemoDockerCollector, DemoSystemCollector


def _make_config(name="test-node"):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.network = NetworkConfig()
    config.features = FeaturesConfig()
    return config


class TestDemoSystemCollector:
    """Tests for the demo system collector (mock data)."""

    @pytest.mark.asyncio
    async def test_collect_returns_hostname(self):
        config = _make_config("demo-pi")
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert data["hostname"] == "demo-pi"

    @pytest.mark.asyncio
    async def test_collect_has_required_fields(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()

        required = ["hostname", "cpu", "mem_used", "mem_total", "temp", "uptime_h", "uptime_m"]
        for field in required:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_cpu_in_range(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert 0 <= data["cpu"] <= 100

    @pytest.mark.asyncio
    async def test_temp_in_range(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert 0 <= data["temp"] <= 100

    @pytest.mark.asyncio
    async def test_nvme_data_present(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert "nvme" in data
        assert data["nvme"]["wear_pct"] >= 0

    @pytest.mark.asyncio
    async def test_collect_detail_structure(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect_detail()
        assert "cpu" in data
        assert "memory" in data
        assert "cores" in data["cpu"]
        assert "top_processes" in data["cpu"]
        assert len(data["cpu"]["top_processes"]) == 5


class TestDemoDockerCollector:
    """Tests for the demo Docker collector."""

    @pytest.mark.asyncio
    async def test_list_containers(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        containers = await coll.list_containers()
        assert len(containers) > 0
        assert "name" in containers[0]

    @pytest.mark.asyncio
    async def test_collect_summary(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.collect_summary()
        assert data["containers"] > 0
        assert len(data["containers_list"]) > 0

    @pytest.mark.asyncio
    async def test_inspect_container(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.inspect_container("grafana")
        assert data["name"] == "grafana"
        assert data["status"] == "running"
        assert "resources" in data

    @pytest.mark.asyncio
    async def test_get_logs(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.get_logs("grafana")
        assert data["container"] == "grafana"
        assert len(data["lines"]) > 0

    @pytest.mark.asyncio
    async def test_restart_container(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.restart_container("grafana")
        assert data["success"] is True


class TestDemoDiskCollector:
    """Tests for the demo disk collector."""

    @pytest.mark.asyncio
    async def test_collect_summary(self):
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_summary()
        assert "disk_pct" in data
        assert 0 <= data["disk_pct"] <= 100

    @pytest.mark.asyncio
    async def test_collect_detail(self):
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_detail()
        assert "mounts" in data
        assert len(data["mounts"]) > 0
        assert "pct" in data["mounts"][0]


class TestDockerContainerNameValidation:
    """Test container name validation in the real Docker collector."""

    def test_valid_names(self):
        from buoy.collectors.docker import _valid_name
        assert _valid_name("grafana") is True
        assert _valid_name("my-container_1.0") is True
        assert _valid_name("plane-api-1") is True

    def test_invalid_names(self):
        from buoy.collectors.docker import _valid_name
        assert _valid_name("") is False
        assert _valid_name("-starts-with-dash") is False
        assert _valid_name("../../etc/passwd") is False
        assert _valid_name("a" * 200) is False
        assert _valid_name("has spaces") is False
        assert _valid_name("has;semicolon") is False
