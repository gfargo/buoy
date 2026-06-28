"""Tests for fleet latency history endpoint."""

import pytest
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, PeerConfig
from buoy.server import create_app


def _make_config(history=True, peers=None):
    config = BuoyConfig()
    config.node = NodeConfig(name="compass")
    config.network = NetworkConfig(peers=peers or [PeerConfig(name="harbor", url="http://harbor")])
    config.features = FeaturesConfig(history=history, websocket=False, demo_mode=True)
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Each test gets its own DB in tmp_path and a clean global state."""
    monkeypatch.chdir(tmp_path)
    yield
    # Reset module-level store so tests don't leak data
    if srv._metric_store:
        srv._metric_store.close()
        srv._metric_store = None


class TestFleetLatencyHistoryEndpoint:
    """Tests for GET /api/fleet/{peer}/latency-history."""

    def test_unknown_peer_returns_404(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/fleet/unknown-node/latency-history")
        assert r.status_code == 404
        assert "error" in r.json()

    def test_history_disabled_returns_404(self):
        app = create_app(_make_config(history=False))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/fleet/harbor/latency-history")
        assert r.status_code == 404

    def test_valid_peer_no_data_returns_empty(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/fleet/harbor/latency-history")
        assert r.status_code == 200
        body = r.json()
        assert body["peer"] == "harbor"
        assert body["data"] == []

    def test_valid_peer_with_data_returns_correct_shape(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            # Seed data after startup (store is initialised by on_startup)
            srv._metric_store.record_latency("harbor", 12.5)
            srv._metric_store.record_latency("harbor", 15.0)
            r = client.get("/api/fleet/harbor/latency-history?hours=6")
        assert r.status_code == 200
        body = r.json()
        assert body["peer"] == "harbor"
        assert body["hours"] == 6
        assert len(body["data"]) == 2
        ts, ms = body["data"][0]
        assert isinstance(ts, int)
        assert ms == 12.5

    def test_hours_param_clamped_to_max_6(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/fleet/harbor/latency-history?hours=99")
        assert r.status_code == 200
        assert r.json()["hours"] == 6

    def test_hours_param_clamped_to_min_1(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/fleet/harbor/latency-history?hours=0")
        assert r.status_code == 200
        assert r.json()["hours"] == 1
