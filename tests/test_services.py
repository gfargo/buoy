"""Tests for Buoy service discovery."""

from unittest.mock import AsyncMock, patch

import pytest

from buoy.config import (
    BuoyConfig,
    FeaturesConfig,
    NetworkConfig,
    NodeConfig,
    PeerConfig,
    ServiceOverride,
    ServicesConfig,
)
from buoy.services import discover_services


def _make_config(
    name="compass",
    peers=None,
    hidden=None,
    overrides=None,
    tailnet_domain="tailb82ead.ts.net",
):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.network = NetworkConfig(
        peers=peers or [],
        tailnet_domain=tailnet_domain,
    )
    config.features = FeaturesConfig()
    config.services = ServicesConfig(
        hidden=hidden or [],
        overrides=overrides or {},
    )
    return config


class TestDiscoverServicesLocal:
    """Test local service discovery from Docker containers."""

    @pytest.mark.asyncio
    async def test_basic_discovery(self):
        config = _make_config()
        containers = [
            {"name": "grafana", "host_port": 3000},
            {"name": "plane-api-1", "host_port": 8080},
        ]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await discover_services(config, is_tailscale=False)

        assert len(result["local"]) == 2
        assert result["local"][0]["name"] == "grafana"
        assert result["local"][0]["url"] == "http://localhost:3000"

    @pytest.mark.asyncio
    async def test_hidden_containers_excluded(self):
        config = _make_config(hidden=["redis", "postgres"])
        containers = [
            {"name": "grafana", "host_port": 3000},
            {"name": "redis", "host_port": 6379},
            {"name": "postgres", "host_port": 5432},
        ]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await discover_services(config, is_tailscale=False)

        assert len(result["local"]) == 1
        assert result["local"][0]["name"] == "grafana"

    @pytest.mark.asyncio
    async def test_overrides_applied(self):
        overrides = {
            "grafana": ServiceOverride(name="Grafana", icon="📊", port=3000, path="/d/main"),
        }
        config = _make_config(overrides=overrides)
        containers = [{"name": "grafana", "host_port": 9999}]  # host_port gets overridden

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await discover_services(config, is_tailscale=False)

        svc = result["local"][0]
        assert svc["name"] == "Grafana"
        assert svc["icon"] == "📊"
        assert svc["url"] == "http://localhost:3000/d/main"

    @pytest.mark.asyncio
    async def test_tailscale_url_generation(self):
        config = _make_config(name="compass", tailnet_domain="tailb82ead.ts.net")
        containers = [{"name": "grafana", "host_port": 3000}]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await discover_services(config, is_tailscale=True)

        assert result["local"][0]["url"] == "https://compass.tailb82ead.ts.net:3000"

    @pytest.mark.asyncio
    async def test_no_port_means_no_url(self):
        config = _make_config()
        containers = [{"name": "some-worker"}]  # No host_port

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await discover_services(config, is_tailscale=False)

        assert result["local"][0]["url"] == ""

    @pytest.mark.asyncio
    async def test_empty_containers(self):
        config = _make_config()

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=[])

            result = await discover_services(config, is_tailscale=False)

        assert result["local"] == []


class TestDiscoverServicesNetwork:
    """Test network peer discovery from config."""

    @pytest.mark.asyncio
    async def test_peers_included(self):
        peers = [
            PeerConfig(name="harbor", url="https://harbor.tailb82ead.ts.net", tier="1A"),
            PeerConfig(name="watch", url="https://watch.tailb82ead.ts.net", tier="2"),
        ]
        config = _make_config(name="compass", peers=peers)

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=[])

            result = await discover_services(config, is_tailscale=False)

        assert len(result["network"]) == 2
        assert result["network"][0]["name"] == "harbor"
        assert result["network"][0]["tier"] == "1A"
        assert result["network"][1]["name"] == "watch"

    @pytest.mark.asyncio
    async def test_self_excluded_from_network(self):
        peers = [
            PeerConfig(name="compass", url="https://compass.tailb82ead.ts.net", tier="1B"),
            PeerConfig(name="harbor", url="https://harbor.tailb82ead.ts.net", tier="1A"),
        ]
        config = _make_config(name="compass", peers=peers)

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=[])

            result = await discover_services(config, is_tailscale=False)

        # compass should NOT appear in its own network list
        assert len(result["network"]) == 1
        assert result["network"][0]["name"] == "harbor"


class TestDiscoverServicesMetadata:
    """Test metadata fields in the response."""

    @pytest.mark.asyncio
    async def test_response_shape(self):
        config = _make_config(name="watch", tailnet_domain="tailb82ead.ts.net")

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=[])

            result = await discover_services(config, is_tailscale=True)

        assert result["hostname"] == "watch"
        assert result["tailscale"] is True
        assert result["tailnet_domain"] == "tailb82ead.ts.net"
        assert "local" in result
        assert "network" in result


class TestTopServices:
    """Tests for the top_services helper."""

    @pytest.mark.asyncio
    async def test_returns_url_bearing_services_only(self):
        from buoy.services import top_services

        config = _make_config()
        containers = [
            {"name": "grafana", "host_port": 3000},
            {"name": "worker"},  # no port → no URL
        ]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await top_services(config, is_tailscale=False)

        assert len(result) == 1
        assert result[0]["url"] == "http://localhost:3000"

    @pytest.mark.asyncio
    async def test_respects_limit(self):
        from buoy.services import top_services

        config = _make_config()
        containers = [{"name": f"svc{i}", "host_port": 3000 + i} for i in range(10)]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await top_services(config, is_tailscale=False, limit=3)

        assert len(result) == 3

    @pytest.mark.asyncio
    async def test_tailscale_url(self):
        from buoy.services import top_services

        config = _make_config(name="compass", tailnet_domain="tailb82ead.ts.net")
        containers = [{"name": "grafana", "host_port": 3000}]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await top_services(config, is_tailscale=True)

        assert result[0]["url"] == "https://compass.tailb82ead.ts.net:3000"

    @pytest.mark.asyncio
    async def test_hidden_excluded(self):
        from buoy.services import top_services

        config = _make_config(hidden=["redis"])
        containers = [
            {"name": "grafana", "host_port": 3000},
            {"name": "redis", "host_port": 6379},
        ]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await top_services(config, is_tailscale=False)

        assert all(s["name"] != "redis" for s in result)

    @pytest.mark.asyncio
    async def test_overrides_applied(self):
        from buoy.services import top_services

        overrides = {"grafana": ServiceOverride(name="Grafana", icon="📊", port=3000)}
        config = _make_config(overrides=overrides)
        containers = [{"name": "grafana", "host_port": 9999}]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await top_services(config, is_tailscale=False)

        assert result[0]["name"] == "Grafana"
        assert result[0]["icon"] == "📊"
        assert result[0]["url"] == "http://localhost:3000"

    @pytest.mark.asyncio
    async def test_result_shape(self):
        from buoy.services import top_services

        config = _make_config()
        containers = [{"name": "grafana", "host_port": 3000}]

        with patch("buoy.collectors.docker.DockerCollector") as mock_collector:
            instance = mock_collector.return_value
            instance.list_containers = AsyncMock(return_value=containers)

            result = await top_services(config, is_tailscale=False)

        assert set(result[0].keys()) == {"name", "icon", "url"}
