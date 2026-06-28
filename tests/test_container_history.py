"""Tests for the /api/container/{name}/history endpoint."""

import pytest
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig
from buoy.server import create_app


def _make_config(history=True):
    config = BuoyConfig()
    config.node = NodeConfig(name="test-node")
    config.features = FeaturesConfig(history=history, websocket=False, demo_mode=True)
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield
    if srv._metric_store:
        srv._metric_store.close()
        srv._metric_store = None


class TestContainerHistoryEndpoint:
    def test_history_disabled_returns_404(self):
        app = create_app(_make_config(history=False))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/container/myapp/history")
        assert r.status_code == 404
        assert "error" in r.json()

    def test_invalid_name_returns_400(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/container/../etc/history")
        assert r.status_code in (400, 404)

    def test_valid_name_no_data_returns_empty(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/container/myapp/history")
        assert r.status_code == 200
        body = r.json()
        assert body["container"] == "myapp"
        assert body["samples"] == []

    def test_valid_name_with_data_returns_correct_shape(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            srv._metric_store.record_container_states(
                [{"name": "myapp", "status": "running", "restart_count": 0}]
            )
            srv._metric_store.record_container_states(
                [{"name": "myapp", "status": "running", "restart_count": 1}]
            )
            r = client.get("/api/container/myapp/history?hours=24")
        assert r.status_code == 200
        body = r.json()
        assert body["container"] == "myapp"
        assert body["hours"] == 24
        assert len(body["samples"]) == 2
        sample = body["samples"][0]
        assert "ts" in sample
        assert sample["status"] == "running"
        assert isinstance(sample["restart_count"], int)

    def test_hours_param_clamped_to_max_24(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/container/myapp/history?hours=999")
        assert r.status_code == 200
        assert r.json()["hours"] == 24
