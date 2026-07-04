"""Tests for alerts field in /api/stats endpoint."""

import pytest
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.alerts import AlertEngine
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, PluginsConfig
from buoy.server import create_app


def _make_config():
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.features = FeaturesConfig(websocket=False, demo_mode=True)
    config.plugins = PluginsConfig()
    return config


@pytest.fixture(autouse=True)
def isolate_state(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    yield
    if srv._metric_store:
        srv._metric_store.close()
        srv._metric_store = None
    srv._alert_engine = None


class TestStatsAlertsField:
    """Tests that /api/stats always includes an alerts array."""

    def test_alerts_empty_on_startup(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/api/stats")
        assert r.status_code == 200
        body = r.json()
        assert "alerts" in body
        assert body["alerts"] == []

    def test_active_alert_appears_in_stats(self):
        import asyncio

        config = _make_config()
        app = create_app(config)
        with TestClient(app) as client:
            # Replace engine with a no-broadcast one AFTER startup runs
            engine = AlertEngine(config)
            srv._alert_engine = engine
            # disk has duration=0, fires immediately on first breach
            asyncio.run(
                engine.evaluate(
                    {"cpu": 10, "mem_used": 0, "mem_total": 1, "temp": 40, "disk_pct": 95}
                )
            )
            r = client.get("/api/stats")
        assert r.status_code == 200
        alerts = r.json()["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["metric"] == "disk"
        assert alerts[0]["level"] == "crit"
        assert alerts[0]["active"] is True

    def test_resolved_alert_removed_from_stats(self):
        import asyncio

        config = _make_config()
        app = create_app(config)
        with TestClient(app) as client:
            engine = AlertEngine(config)
            srv._alert_engine = engine
            asyncio.run(
                engine.evaluate(
                    {"cpu": 10, "mem_used": 0, "mem_total": 1, "temp": 40, "disk_pct": 95}
                )
            )
            assert len(engine.active_alerts) == 1

            # Resolve by dropping below threshold
            asyncio.run(
                engine.evaluate(
                    {"cpu": 10, "mem_used": 0, "mem_total": 1, "temp": 40, "disk_pct": 50}
                )
            )
            r = client.get("/api/stats")
        assert r.json()["alerts"] == []

    def test_alerts_field_present_without_engine(self):
        """Guard: when _alert_engine is None, alerts is [] not an error."""
        app = create_app(_make_config())
        with TestClient(app) as client:
            srv._alert_engine = None
            r = client.get("/api/stats")
        assert r.status_code == 200
        assert r.json()["alerts"] == []
