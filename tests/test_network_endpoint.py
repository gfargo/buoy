"""Wiring tests for network throughput data across /api/stats,
/api/stats/detail, the stats WebSocket broadcast, and demo mode.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from starlette.testclient import TestClient

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, RefreshConfig
from buoy.server import BuoyAppState, _stats_loop, create_app


def _demo_config():
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.features = FeaturesConfig(websocket=False, demo_mode=True)
    return config


class _PlainWsClient:
    def __init__(self):
        self.sent = []

    async def send_text(self, message):
        self.sent.append(message)


class TestApiStatsNetworkField:
    def test_demo_mode_includes_net(self):
        app = create_app(_demo_config())
        with TestClient(app) as client:
            r = client.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        assert "net" in body
        assert body["net"]["primary"] == "eth0"
        assert "rx_bytes_per_sec" in body["net"]
        assert "tx_bytes_per_sec" in body["net"]

    def test_omitted_when_network_collector_missing(self):
        app = create_app(_demo_config())
        with TestClient(app) as client:
            del app.state.buoy.collectors["network"]
            r = client.get("/api/stats")
        assert r.status_code == 200
        assert "net" not in r.json()


class TestApiStatsDetailNetworkField:
    def test_demo_mode_includes_interfaces(self):
        app = create_app(_demo_config())
        with TestClient(app) as client:
            r = client.get("/api/stats/detail")
        assert r.status_code == 200
        body = r.json()
        assert "net" in body
        assert isinstance(body["net"]["interfaces"], list)
        assert body["net"]["interfaces"]
        first = body["net"]["interfaces"][0]
        for key in (
            "name",
            "rx_bytes",
            "tx_bytes",
            "rx_bytes_per_sec",
            "tx_bytes_per_sec",
            "rx_errors",
            "tx_errors",
            "rx_dropped",
            "tx_dropped",
        ):
            assert key in first

    def test_omitted_when_network_collector_missing(self):
        app = create_app(_demo_config())
        with TestClient(app) as client:
            del app.state.buoy.collectors["network"]
            r = client.get("/api/stats/detail")
        assert r.status_code == 200
        assert "net" not in r.json()


class TestStatsLoopBroadcastsNet:
    @pytest.mark.asyncio
    async def test_websocket_broadcast_carries_net(self):
        from buoy.demo import (
            DemoDiskCollector,
            DemoDockerCollector,
            DemoNetworkCollector,
            DemoSystemCollector,
        )

        config = _demo_config()
        config.features.websocket = True
        config.refresh = RefreshConfig(stats_interval=0)
        state = BuoyAppState(config=config)
        state.collectors["system"] = DemoSystemCollector(config)
        state.collectors["docker"] = DemoDockerCollector(config)
        state.collectors["disk"] = DemoDiskCollector(config)
        state.collectors["network"] = DemoNetworkCollector(config)

        client = _PlainWsClient()
        state.ws_clients.add(client)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_stats_loop(state), timeout=0.2)

        assert client.sent
        payload = json.loads(client.sent[0])
        assert "net" in payload["data"]
        assert payload["data"]["net"]["primary"] == "eth0"

    @pytest.mark.asyncio
    async def test_history_record_drops_interfaces_breakdown(self):
        from buoy.demo import (
            DemoDiskCollector,
            DemoDockerCollector,
            DemoNetworkCollector,
            DemoSystemCollector,
        )

        config = _demo_config()
        config.refresh = RefreshConfig(stats_interval=0)
        state = BuoyAppState(config=config)
        state.collectors["system"] = DemoSystemCollector(config)
        state.collectors["docker"] = DemoDockerCollector(config)
        state.collectors["disk"] = DemoDiskCollector(config)
        state.collectors["network"] = DemoNetworkCollector(config)

        recorded = []

        class _RecordingStore:
            def record(self, metric, data):
                recorded.append(data)

            def prune(self):
                pass

        state.metric_store = _RecordingStore()

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_stats_loop(state), timeout=0.2)

        assert recorded
        assert "net" in recorded[0]
        assert "interfaces" not in recorded[0]["net"]
        assert "primary" in recorded[0]["net"]


class TestDemoNetworkCollectorParity:
    """DemoNetworkCollector must keep api_fleet/latency-loop behavior
    unchanged now that demo mode registers a 'network' collector at all."""

    @pytest.mark.asyncio
    async def test_collect_returns_no_peers(self):
        from buoy.demo import DemoNetworkCollector

        coll = DemoNetworkCollector(_demo_config())
        assert await coll.collect() == {"peers": []}

    @pytest.mark.asyncio
    async def test_measure_latency_returns_empty(self):
        from buoy.demo import DemoNetworkCollector

        coll = DemoNetworkCollector(_demo_config())
        assert await coll.measure_latency() == []

    def test_api_fleet_demo_mode_returns_no_peers(self):
        app = create_app(_demo_config())
        with TestClient(app) as client:
            r = client.get("/api/fleet")
        assert r.status_code == 200
        assert r.json() == {"peers": []}
