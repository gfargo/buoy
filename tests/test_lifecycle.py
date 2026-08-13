"""Tests for the Starlette lifespan shutdown path (BUG-18).

Verifies that on shutdown the app tears down plugins, closes the SQLite
metric store, and cancels background loops — none of which happened under
the old ``on_startup=[...]`` hook (there was no shutdown hook at all).
"""

import warnings

import pytest
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig
from buoy.plugins.loader import PluginManager
from buoy.server import create_app
from buoy.storage import MetricStore


def _make_config():
    config = BuoyConfig()
    config.node = NodeConfig(name="compass")
    config.features = FeaturesConfig(history=True, websocket=True, demo_mode=True)
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Each test gets its own DB in tmp_path and a clean global state."""
    monkeypatch.chdir(tmp_path)
    yield
    if srv._metric_store:
        srv._metric_store.close()
        srv._metric_store = None


def test_shutdown_resets_globals():
    app = create_app(_make_config())
    with TestClient(app) as client:
        client.get("/api/health")
        assert srv._metric_store is not None
        assert srv._plugin_manager is not None

    assert srv._metric_store is None
    assert srv._plugin_manager is None
    assert srv._alert_engine is None
    assert srv._background_tasks == []


def test_shutdown_calls_plugin_manager_stop_and_store_close(monkeypatch):
    stop_called = []
    close_called = []

    original_stop = PluginManager.stop
    original_close = MetricStore.close

    async def spy_stop(self):
        stop_called.append(True)
        await original_stop(self)

    def spy_close(self):
        close_called.append(True)
        original_close(self)

    monkeypatch.setattr(PluginManager, "stop", spy_stop)
    monkeypatch.setattr(MetricStore, "close", spy_close)

    app = create_app(_make_config())
    with TestClient(app) as client:
        client.get("/api/health")

    assert stop_called == [True]
    assert close_called == [True]


def test_lifespan_emits_no_deprecation_warning():
    app = create_app(_make_config())
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        with TestClient(app) as client:
            client.get("/api/health")

    assert not any(
        "on_startup" in str(w.message) or "on_shutdown" in str(w.message) for w in caught
    )
