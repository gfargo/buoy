"""Tests for GET /api/health — legacy keys, degraded summary, and the
liveness/readiness contract CI and k8s probes depend on.
"""

from __future__ import annotations

import pytest
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.server import BuoyAppState, _is_degraded, api_health, create_app


def _make_config(name="test-node", demo_mode=True, base_path=""):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.features = FeaturesConfig(websocket=False, demo_mode=demo_mode)
    if base_path:
        config.network = NetworkConfig(base_path=base_path)
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


async def _noop_capability_loop(state):
    """Stand-in for _capability_loop that never touches state.capabilities,
    so tests can deterministically control the snapshot instead of racing
    the real background probe."""
    return


class TestLegacyKeysAndNewFields:
    def test_demo_mode_returns_ok_status_and_new_fields(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/api/health")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["hostname"] == "test-node"
        assert "version" in body
        assert isinstance(body["degraded"], bool)
        assert isinstance(body["subsystems"], dict)
        assert isinstance(body["plugins"], dict)
        assert body["plugins"]["total"] >= 1  # demo mode auto-enables curated builtins


class TestPreProbeSnapshot:
    def test_empty_subsystems_before_first_probe_completes(self, monkeypatch):
        monkeypatch.setattr(srv, "_capability_loop", _noop_capability_loop)
        app = create_app(_make_config())

        with TestClient(app) as client:
            r = client.get("/api/health")

        assert r.status_code == 200
        body = r.json()
        assert body["subsystems"] == {}
        assert body["degraded"] is False


class TestDegradedNeverBreaksLiveness:
    def test_status_stays_ok_and_200_when_everything_is_unavailable(self, monkeypatch):
        """Guards the CI smoke grep (`"status":\\s*"ok"`) and the k8s
        liveness/readiness probes, both of which key off `status`, not
        `degraded` — a fully degraded host must still report ok/200."""
        monkeypatch.setattr(srv, "_capability_loop", _noop_capability_loop)
        app = create_app(_make_config(demo_mode=False))

        with TestClient(app) as client:
            app.state.buoy.capabilities = {
                "docker": {"status": "unavailable", "impact": "x"},
                "nsenter": {"status": "unavailable", "impact": "x"},
                "smartctl": {"status": "unavailable", "impact": "x"},
                "proc": {"status": "unavailable", "impact": "x"},
                "sys_thermal": {"status": "unavailable", "impact": "x"},
            }
            r = client.get("/api/health")

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["degraded"] is True

    def test_not_degraded_when_every_subsystem_ok(self, monkeypatch):
        monkeypatch.setattr(srv, "_capability_loop", _noop_capability_loop)
        app = create_app(_make_config(demo_mode=False))

        with TestClient(app) as client:
            app.state.buoy.capabilities = {
                "docker": {"status": "ok", "impact": ""},
                "nsenter": {"status": "not_applicable", "impact": ""},
            }
            r = client.get("/api/health")

        assert r.json()["degraded"] is False


class TestNoPluginManager:
    def test_safe_shape_when_plugin_manager_is_none(self):
        """api_health must not assume on_startup already ran — exercised
        directly (bypassing create_app's lifespan) since in the real app a
        PluginManager is always constructed before any request is served."""
        app = Starlette(routes=[Route("/api/health", api_health)])
        app.state.buoy = BuoyAppState(config=BuoyConfig())

        with TestClient(app) as client:
            r = client.get("/api/health")

        assert r.status_code == 200
        assert r.json()["plugins"] == {
            "total": 0,
            "ok": 0,
            "error": 0,
            "disabled": 0,
            "not_loaded": 0,
            "entries": [],
        }


class TestBasePath:
    def test_health_reachable_under_base_path(self):
        app = create_app(_make_config(base_path="/buoy"))
        with TestClient(app) as client:
            r = client.get("/buoy/api/health")

        assert r.status_code == 200
        assert r.json()["status"] == "ok"


class TestIsDegradedHelper:
    def test_false_when_nothing_wrong(self):
        assert _is_degraded({"docker": {"status": "ok"}}, {"error": 0, "not_loaded": 0}) is False

    def test_true_on_unavailable_subsystem(self):
        assert (
            _is_degraded({"docker": {"status": "unavailable"}}, {"error": 0, "not_loaded": 0})
            is True
        )

    def test_true_on_plugin_error(self):
        assert _is_degraded({}, {"error": 1, "not_loaded": 0}) is True

    def test_true_on_plugin_not_loaded(self):
        assert _is_degraded({}, {"error": 0, "not_loaded": 1}) is True


class TestBuoyAppStateDefaults:
    def test_capabilities_defaults_to_empty_dict(self):
        state = BuoyAppState(config=BuoyConfig())
        assert state.capabilities == {}
