"""Tests for per-application Starlette runtime state and lifespan cleanup."""

import asyncio
import json
import warnings

import pytest
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig
from buoy.plugins.loader import PluginManager
from buoy.server import create_app
from buoy.storage import MetricStore


def _make_config(name="compass", *, history=True, websocket=True, image_updates=False):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.features = FeaturesConfig(
        history=history,
        websocket=websocket,
        demo_mode=True,
        image_updates=image_updates,
    )
    return config


def _snapshot_live_state(state):
    return {
        "plugin_manager": state.plugin_manager,
        "metric_store": state.metric_store,
        "alert_engine": state.alert_engine,
        "collectors_container": state.collectors,
        "collectors": dict(state.collectors),
        "clients_container": state.ws_clients,
        "clients": set(state.ws_clients),
        "cache_container": state.image_update_cache,
        "cache": dict(state.image_update_cache),
        "tasks_container": state.background_tasks,
        "tasks": list(state.background_tasks),
    }


def _assert_matches_snapshot(state, snapshot):
    assert state.plugin_manager is snapshot["plugin_manager"]
    assert state.metric_store is snapshot["metric_store"]
    assert state.alert_engine is snapshot["alert_engine"]
    assert state.collectors is snapshot["collectors_container"]
    assert state.collectors == snapshot["collectors"]
    assert state.ws_clients is snapshot["clients_container"]
    assert state.ws_clients == snapshot["clients"]
    assert state.image_update_cache is snapshot["cache_container"]
    assert state.image_update_cache == snapshot["cache"]
    assert state.background_tasks is snapshot["tasks_container"]
    assert state.background_tasks == snapshot["tasks"]


def _assert_reset(state):
    assert state.plugin_manager is None
    assert state.metric_store is None
    assert state.alert_engine is None
    assert state.collectors == {}
    assert state.ws_clients == set()
    assert state.image_update_cache == {}
    assert state.background_tasks == []


class _RecordingClient:
    def __init__(self):
        self.sent = []

    async def send_text(self, message):
        self.sent.append(json.loads(message))


@pytest.fixture(autouse=True)
def isolated_working_directory(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


def test_two_app_construction_has_distinct_state_and_preserves_config_references():
    config_a = _make_config("alpha", history=False)
    config_b = _make_config("bravo", history=False)

    app_a = create_app(config_a)
    app_b = create_app(config_b)

    assert app_a.state.buoy is not app_b.state.buoy
    assert app_a.state.buoy.config is config_a
    assert app_b.state.buoy.config is config_b
    for field in (
        "collectors",
        "ws_clients",
        "image_update_cache",
        "background_tasks",
    ):
        assert getattr(app_a.state.buoy, field) is not getattr(app_b.state.buoy, field)


def test_simultaneous_lifespans_bind_every_runtime_resource_to_own_app():
    app_a = create_app(_make_config("alpha", image_updates=True))
    app_b = create_app(_make_config("bravo", image_updates=True))
    state_a = app_a.state.buoy
    state_b = app_b.state.buoy
    client_a = object()
    client_b = object()

    with TestClient(app_a) as http_a, TestClient(app_b) as http_b:
        state_a.ws_clients.add(client_a)
        state_b.ws_clients.add(client_b)
        state_a.image_update_cache["alpha"] = {"status": "current"}
        state_b.image_update_cache["bravo"] = {"status": "outdated"}

        assert http_a.get("/api/health").json()["hostname"] == "alpha"
        assert http_b.get("/api/health").json()["hostname"] == "bravo"
        assert state_a.collectors is not state_b.collectors
        assert state_a.collectors.keys() == state_b.collectors.keys()
        for name in state_a.collectors:
            assert state_a.collectors[name] is not state_b.collectors[name]
            assert state_a.collectors[name].config is state_a.config
            assert state_b.collectors[name].config is state_b.config
        assert state_a.plugin_manager is not state_b.plugin_manager
        assert state_a.plugin_manager.config is state_a.config
        assert state_b.plugin_manager.config is state_b.config
        assert state_a.metric_store is not state_b.metric_store
        assert state_a.metric_store.config is state_a.config
        assert state_b.metric_store.config is state_b.config
        assert state_a.alert_engine is not state_b.alert_engine
        assert state_a.alert_engine.config is state_a.config
        assert state_b.alert_engine.config is state_b.config
        assert state_a.background_tasks is not state_b.background_tasks
        assert state_a.background_tasks
        assert state_b.background_tasks
        assert not set(state_a.background_tasks) & set(state_b.background_tasks)
        assert all(not task.done() for task in state_a.background_tasks)
        assert all(not task.done() for task in state_b.background_tasks)
        assert state_a.ws_clients == {client_a}
        assert state_b.ws_clients == {client_b}
        assert state_a.image_update_cache is not state_b.image_update_cache
        assert state_a.image_update_cache["alpha"]["status"] == "current"
        assert state_b.image_update_cache["bravo"]["status"] == "outdated"


def test_shutdown_of_one_app_leaves_every_resource_of_other_app_live():
    app_a = create_app(_make_config("alpha", image_updates=True))
    app_b = create_app(_make_config("bravo", image_updates=True))
    state_a = app_a.state.buoy
    state_b = app_b.state.buoy

    with TestClient(app_a) as client_a:
        state_a.ws_clients.add(object())
        state_a.image_update_cache["alpha"] = {"status": "current"}
        snapshot_a = _snapshot_live_state(state_a)
        with TestClient(app_b) as client_b:
            state_b.ws_clients.add(object())
            state_b.image_update_cache["bravo"] = {"status": "outdated"}
            assert client_b.get("/api/health").status_code == 200
            assert state_b.plugin_manager is not None

        _assert_reset(state_b)
        _assert_matches_snapshot(state_a, snapshot_a)
        assert all(not task.done() for task in state_a.background_tasks)
        assert client_a.get("/api/health").json()["hostname"] == "alpha"


def test_failed_app_construction_validates_before_state_creation_or_existing_mutation(monkeypatch):
    app = create_app(_make_config("existing", history=False))
    state = app.state.buoy
    snapshot = _snapshot_live_state(state)
    invalid = _make_config("invalid", history=False)
    invalid.auth.enabled = True
    invalid.auth.type = "token"
    invalid.auth.token = ""

    def unexpected_state_creation(**_kwargs):
        pytest.fail("BuoyAppState must not be constructed for invalid auth")

    monkeypatch.setattr(srv, "BuoyAppState", unexpected_state_creation)
    with pytest.raises(RuntimeError, match="auth.token is not set"):
        create_app(invalid)

    assert state.config.node.name == "existing"
    assert state.config is app.state.buoy.config
    _assert_matches_snapshot(state, snapshot)


def test_failed_startup_resets_failed_app_without_affecting_existing_live_app(monkeypatch):
    app_a = create_app(_make_config("existing", image_updates=True))
    state_a = app_a.state.buoy

    with TestClient(app_a) as client_a:
        state_a.ws_clients.add(object())
        state_a.image_update_cache["existing"] = {"status": "current"}
        snapshot_a = _snapshot_live_state(state_a)

        async def failing_start(self):
            raise RuntimeError("plugin discovery boom")

        monkeypatch.setattr(PluginManager, "start", failing_start)
        app_b = create_app(_make_config("failing", image_updates=True))
        state_b = app_b.state.buoy
        with pytest.raises(RuntimeError, match="plugin discovery boom"):
            with TestClient(app_b):
                pass

        _assert_reset(state_b)
        _assert_matches_snapshot(state_a, snapshot_a)
        assert all(not task.done() for task in state_a.background_tasks)
        assert client_a.get("/api/health").json()["hostname"] == "existing"


@pytest.mark.asyncio
async def test_alert_engine_callback_broadcasts_only_to_own_app_clients():
    app_a = create_app(_make_config("alpha", history=False, websocket=False))
    app_b = create_app(_make_config("bravo", history=False, websocket=False))
    state_a = app_a.state.buoy
    state_b = app_b.state.buoy
    client_a = _RecordingClient()
    client_b = _RecordingClient()

    async with app_a.router.lifespan_context(app_a), app_b.router.lifespan_context(app_b):
        state_a.ws_clients.add(client_a)
        state_b.ws_clients.add(client_b)

        await state_a.alert_engine.evaluate(
            {"cpu": 10, "mem_used": 0, "mem_total": 1, "temp": 40, "disk_pct": 95}
        )
        await state_a.alert_engine.evaluate(
            {"cpu": 10, "mem_used": 0, "mem_total": 1, "temp": 40, "disk_pct": 50}
        )

        assert [message["type"] for message in client_a.sent] == ["alert", "alert_resolved"]
        assert client_b.sent == []
        assert state_b.alert_engine.active_alerts == []


def test_shutdown_resets_app_state():
    app = create_app(_make_config())
    state = app.state.buoy
    with TestClient(app) as client:
        client.get("/api/health")
        assert state.metric_store is not None
        assert state.plugin_manager is not None
        assert state.alert_engine is not None
        assert state.collectors
        assert state.background_tasks

    _assert_reset(state)


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


def test_partial_startup_failure_still_resets_app_state(monkeypatch):
    async def failing_start(self):
        raise RuntimeError("plugin discovery boom")

    monkeypatch.setattr(PluginManager, "start", failing_start)

    app = create_app(_make_config())
    state = app.state.buoy
    with pytest.raises(RuntimeError, match="plugin discovery boom"):
        with TestClient(app):
            pass

    _assert_reset(state)


@pytest.mark.asyncio
async def test_plugin_stop_failure_retains_live_owner_until_cleanup_retry(monkeypatch):
    app = create_app(_make_config())
    state = app.state.buoy
    plugin_task = None
    original_stop = PluginManager.stop

    try:
        with pytest.raises(RuntimeError, match="teardown boom"):
            async with app.router.lifespan_context(app):
                manager = state.plugin_manager
                plugin_task = asyncio.create_task(asyncio.Event().wait())
                manager._tasks.append(plugin_task)

                async def failing_stop():
                    raise RuntimeError("teardown boom")

                monkeypatch.setattr(manager, "stop", failing_stop)

        assert state.plugin_manager is manager
        assert plugin_task is not None
        assert not plugin_task.done()
        assert state.metric_store is None
        assert state.alert_engine is None
        assert state.collectors == {}
        assert state.background_tasks == []

        with pytest.raises(RuntimeError, match="previous shutdown cleanup is incomplete"):
            await srv.on_startup(state)

        monkeypatch.setattr(manager, "stop", original_stop.__get__(manager, PluginManager))
        await srv.on_shutdown(state)

        assert plugin_task.done()
        _assert_reset(state)
    finally:
        if plugin_task is not None and not plugin_task.done():
            plugin_task.cancel()
            await asyncio.gather(plugin_task, return_exceptions=True)


@pytest.mark.asyncio
async def test_store_close_failure_retains_open_owner_until_cleanup_retry(monkeypatch):
    app = create_app(_make_config())
    state = app.state.buoy
    original_close = MetricStore.close

    with pytest.raises(RuntimeError, match="close boom"):
        async with app.router.lifespan_context(app):
            store = state.metric_store
            connection = store._conn

            def failing_close():
                raise RuntimeError("close boom")

            monkeypatch.setattr(store, "close", failing_close)

    assert state.plugin_manager is None
    assert state.metric_store is store
    assert store._conn is connection
    assert connection.execute("SELECT 1").fetchone() == (1,)
    assert state.alert_engine is None
    assert state.collectors == {}
    assert state.background_tasks == []

    with pytest.raises(RuntimeError, match="previous shutdown cleanup is incomplete"):
        await srv.on_startup(state)

    monkeypatch.setattr(store, "close", original_close.__get__(store, MetricStore))
    await srv.on_shutdown(state)

    assert store._conn is None
    _assert_reset(state)


def test_repeated_lifespan_starts_clean_and_reinitializes_every_resource():
    app = create_app(_make_config())
    state = app.state.buoy

    with TestClient(app):
        first_manager = state.plugin_manager
        first_store = state.metric_store
        first_engine = state.alert_engine
        first_collectors = dict(state.collectors)
        first_tasks = list(state.background_tasks)
        state.ws_clients.add(object())
        state.image_update_cache["dirty"] = {"status": "outdated"}

    _assert_reset(state)

    with TestClient(app):
        assert state.plugin_manager is not None
        assert state.plugin_manager is not first_manager
        assert state.metric_store is not None
        assert state.metric_store is not first_store
        assert state.alert_engine is not None
        assert state.alert_engine is not first_engine
        assert state.collectors
        for name, collector in state.collectors.items():
            assert collector is not first_collectors[name]
        assert state.background_tasks
        assert not set(state.background_tasks) & set(first_tasks)
        assert state.ws_clients == set()
        assert state.image_update_cache == {}

    _assert_reset(state)


def test_removed_module_global_attributes_are_absent():
    for name in (
        "_config",
        "_collectors",
        "_ws_clients",
        "_plugin_manager",
        "_metric_store",
        "_alert_engine",
        "_image_update_cache",
        "_background_tasks",
    ):
        assert not hasattr(srv, name)
