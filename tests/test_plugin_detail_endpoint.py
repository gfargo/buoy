"""Tests for GET /api/plugins/{id} (single plugin detail payload)."""

import pytest
from starlette.testclient import TestClient

from buoy.auth import PROTECTED_PATHS
from buoy.config import (
    BuoyConfig,
    FeaturesConfig,
    NodeConfig,
    PluginEntry,
    PluginsConfig,
    _build_config,
)
from buoy.server import create_app


def _make_config(builtin=None, demo_mode=True):
    config = BuoyConfig()
    config.node = NodeConfig(name="test-node")
    config.features = FeaturesConfig(websocket=False, demo_mode=demo_mode)
    if builtin is not None:
        config.plugins = PluginsConfig(builtin=builtin)
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


class TestPluginDetailEndpoint:
    def test_demo_plugin_returns_200_with_detail_shape(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            plugins = client.get("/api/plugins").json()["plugins"]
            plugin_id = plugins[0]["id"]

            r = client.get(f"/api/plugins/{plugin_id}")

        assert r.status_code == 200
        body = r.json()
        assert body["id"] == plugin_id
        assert "name" in body
        assert "panel" in body
        assert "detail_panel" in body
        assert body["loaded"] is True
        assert body["manifest"]["source"] == "builtin"
        assert isinstance(body["manifest"]["effective_refresh_interval"], int)

    def test_unknown_id_returns_404(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins/does-not-exist")

        assert r.status_code == 404
        assert "error" in r.json()

    def test_configured_but_not_loaded_builtin_returns_stub(self):
        config = _make_config(builtin={"broken": PluginEntry(enabled=True)})
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins/broken")

        assert r.status_code == 200
        body = r.json()
        assert body["loaded"] is False
        assert body["detail_panel"] is None
        assert body["manifest"] is None

    def test_list_payload_has_no_detail_panel(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins")

        assert r.status_code == 200
        for entry in r.json()["plugins"]:
            assert "detail_panel" not in entry
            assert "manifest" not in entry

    def test_plugin_js_route_still_resolves(self):
        """Route-ordering regression: {id} must not swallow /api/plugins/js."""
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins/js")

        assert r.status_code == 200
        assert r.headers["content-type"].startswith("application/javascript")

    def test_not_in_protected_paths(self):
        assert "/api/plugins" not in PROTECTED_PATHS

    def test_reachable_without_credentials_when_auth_enabled(self):
        config = _build_config(
            {
                "auth": {"enabled": True, "type": "token", "token": "secret"},
                "features": {"websocket": False, "demo_mode": True},
            }
        )
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            plugins = client.get("/api/plugins").json()["plugins"]
            plugin_id = plugins[0]["id"]

            r = client.get(f"/api/plugins/{plugin_id}")

        assert r.status_code == 200
