"""Tests for fleet latency — NetworkCollector._poll_peer and /api/fleet endpoint."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.collectors.network import NetworkCollector
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, PeerConfig


def _make_config(name="test-node", peers=None):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.network = NetworkConfig()
    if peers is not None:
        config.network.peers = peers
    config.features = FeaturesConfig()
    return config


def _make_peer(name="harbor", url="http://harbor.local:8090", tier="Tier 1A"):
    return PeerConfig(name=name, url=url, tier=tier)


class TestNetworkCollectorLatency:
    """Unit tests for per-peer latency_ms in NetworkCollector."""

    @pytest.mark.asyncio
    async def test_online_peer_has_latency_ms(self):
        """A reachable peer returns a non-negative numeric latency_ms."""
        peer = _make_peer()
        config = _make_config(peers=[peer])
        coll = NetworkCollector(config)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"cpu": 10, "mem_used": 100, "mem_total": 200}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("buoy.collectors.network.httpx.AsyncClient", return_value=mock_client):
            result = await coll.collect()

        assert len(result["peers"]) == 1
        p = result["peers"][0]
        assert p["online"] is True
        assert "latency_ms" in p
        assert isinstance(p["latency_ms"], int)
        assert p["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_offline_peer_has_latency_ms_minus_one(self):
        """A peer that raises an exception returns online=False and latency_ms=-1."""
        peer = _make_peer()
        config = _make_config(peers=[peer])
        coll = NetworkCollector(config)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("buoy.collectors.network.httpx.AsyncClient", return_value=mock_client):
            result = await coll.collect()

        assert len(result["peers"]) == 1
        p = result["peers"][0]
        assert p["online"] is False
        assert p["latency_ms"] == -1

    @pytest.mark.asyncio
    async def test_non_200_peer_has_latency_ms_minus_one(self):
        """A peer returning a non-200 status returns online=False and latency_ms=-1."""
        peer = _make_peer()
        config = _make_config(peers=[peer])
        coll = NetworkCollector(config)

        fake_response = MagicMock()
        fake_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("buoy.collectors.network.httpx.AsyncClient", return_value=mock_client):
            result = await coll.collect()

        assert len(result["peers"]) == 1
        p = result["peers"][0]
        assert p["online"] is False
        assert p["latency_ms"] == -1

    @pytest.mark.asyncio
    async def test_self_node_has_latency_ms_zero(self):
        """The self-node shortcut returns latency_ms=0."""
        peer = _make_peer(name="test-node")  # same as config node name
        config = _make_config(name="test-node", peers=[peer])
        coll = NetworkCollector(config)

        result = await coll.collect()

        assert len(result["peers"]) == 1
        p = result["peers"][0]
        assert p["online"] is True
        assert p["self"] is True
        assert p["latency_ms"] == 0

    @pytest.mark.asyncio
    async def test_peer_response_includes_url(self):
        """Each peer entry includes the url field so the frontend can build hrefs."""
        peer = _make_peer(url="http://harbor.local:8090")
        config = _make_config(peers=[peer])
        coll = NetworkCollector(config)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"cpu": 5}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("buoy.collectors.network.httpx.AsyncClient", return_value=mock_client):
            result = await coll.collect()

        assert result["peers"][0]["url"] == "http://harbor.local:8090"

    @pytest.mark.asyncio
    async def test_no_peers_returns_empty(self):
        """collect() returns empty peers list when no peers are configured."""
        config = _make_config(peers=[])
        coll = NetworkCollector(config)
        result = await coll.collect()
        assert result == {"peers": []}

    @pytest.mark.asyncio
    async def test_multiple_peers_all_have_latency_ms(self):
        """All peers in a multi-peer config have a latency_ms field."""
        peers = [
            _make_peer(name="alpha", url="http://alpha.local:8090"),
            _make_peer(name="beta", url="http://beta.local:8090"),
        ]
        config = _make_config(peers=peers)
        coll = NetworkCollector(config)

        fake_response = MagicMock()
        fake_response.status_code = 200
        fake_response.json.return_value = {"cpu": 20}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=fake_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("buoy.collectors.network.httpx.AsyncClient", return_value=mock_client):
            result = await coll.collect()

        assert len(result["peers"]) == 2
        for p in result["peers"]:
            assert "latency_ms" in p
            assert isinstance(p["latency_ms"], int)
