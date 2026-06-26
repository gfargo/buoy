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


class TestDiskCollectorNvme:
    """Tests for real DiskCollector NVMe SMART path."""

    @pytest.mark.asyncio
    async def test_nvme_smart_returns_none_when_unavailable(self):
        """_nvme_smart returns None gracefully when nsenter and smartctl are absent."""
        from unittest.mock import patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        # Both nsenter and direct smartctl calls raise FileNotFoundError
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("smartctl not found"),
        ):
            result = await coll._nvme_smart()

        assert result is None

    @pytest.mark.asyncio
    async def test_demo_disk_nvme_in_summary(self):
        """DemoDiskCollector always returns nvme data in collect_summary."""
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_summary()
        assert "nvme" in data
        nvme = data["nvme"]
        assert "temp" in nvme
        assert "wear_pct" in nvme
        assert "power_hours" in nvme
        assert "read" in nvme
        assert "written" in nvme


class TestNetworkLatency:
    """Tests for NetworkCollector tailscale ping and HTTP fallback."""

    def _make_net_config(self, peers=None):
        from buoy.config import PeerConfig

        config = _make_config("compass")
        if peers:
            config.network.peers = [PeerConfig(name=n, url=u) for n, u in peers]
        return config

    def _mock_proc(self, returncode, stdout):
        from unittest.mock import AsyncMock, MagicMock

        proc = MagicMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        return proc

    @pytest.mark.asyncio
    async def test_tailscale_ping_parses_latency(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config()
        coll = NetworkCollector(config)
        proc = self._mock_proc(0, b"pong from compass (100.64.67.98) via DERP(nyc) in 2.1ms\n")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await coll._tailscale_ping("compass")

        assert result == 2.1

    @pytest.mark.asyncio
    async def test_tailscale_ping_returns_none_on_failure(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config()
        coll = NetworkCollector(config)
        proc = self._mock_proc(1, b"")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await coll._tailscale_ping("compass")

        assert result is None

    @pytest.mark.asyncio
    async def test_measure_latency_falls_back_to_http(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "http://harbor.local")])
        coll = NetworkCollector(config)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client):
                results = await coll.measure_latency()

        assert len(results) == 1
        assert results[0]["online"] is True
        assert results[0]["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_measure_latency_self_node(self):
        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("compass", "http://compass.local")])
        coll = NetworkCollector(config)
        results = await coll.measure_latency()

        assert results == [{"name": "compass", "latency_ms": 0, "online": True}]
