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

    def test_detail_includes_health_config_and_disabled(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            plugins = client.get("/api/plugins").json()["plugins"]
            plugin_id = plugins[0]["id"]

            r = client.get(f"/api/plugins/{plugin_id}")

        body = r.json()
        assert "health" in body
        assert "last_attempt_at" in body["health"]
        assert "last_collect_duration_ms" in body["health"]
        assert "timed_out" in body["health"]
        assert "timeout_seconds" in body["health"]
        assert "config" in body
        assert "config_errors" in body
        assert body["disabled"] is False
        assert "id" in body["manifest"]
        assert "refresh_interval_override" in body["manifest"]
        assert "plugins_interval_floor" in body["manifest"]

    def test_not_loaded_stub_detail_has_empty_health_and_config(self):
        config = _make_config(builtin={"broken": PluginEntry(enabled=True)})
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins/broken")

        body = r.json()
        assert body["health"] is None
        assert body["config"] == []
        assert body["config_errors"] == []
        assert body["disabled"] is False

    def test_disabled_by_config_validation_shows_full_error_list(self):
        """A builtin missing a required field shows every validation error, not one truncated line."""
        config = _make_config(builtin={"github": PluginEntry(enabled=True)}, demo_mode=False)
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins/github")

        assert r.status_code == 200
        body = r.json()
        assert body["disabled"] is True
        assert len(body["config_errors"]) >= 1
        assert "token" in body["config_errors"][0]

    def test_config_secret_redacted_via_yaml_settings(self):
        config = _make_config(
            builtin={"github": PluginEntry(enabled=True, settings={"token": "s3cr3t"})},
            demo_mode=True,
        )
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins/github")

        assert r.status_code == 200
        assert "s3cr3t" not in r.text
        row = next(row for row in r.json()["config"] if row["key"] == "token")
        assert row["value"] == "***REDACTED***"
        assert row["secret"] is True
        assert row["source"] == "yaml"

    def test_config_secret_redacted_via_env_override(self, monkeypatch):
        monkeypatch.setenv("BUOY_GITHUB_TOKEN", "env-s3cr3t")
        config = _make_config(builtin={"github": PluginEntry(enabled=True)}, demo_mode=True)
        app = create_app(config)
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/plugins/github")

        assert r.status_code == 200
        assert "env-s3cr3t" not in r.text
        row = next(row for row in r.json()["config"] if row["key"] == "token")
        assert row["value"] == "***REDACTED***"
        assert row["source"] == "env:BUOY_GITHUB_TOKEN"
