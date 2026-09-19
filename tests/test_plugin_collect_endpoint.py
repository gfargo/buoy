"""Tests for POST /api/plugins/{id}/collect (manual refresh-now)."""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from buoy.auth import PROTECTED_PATHS, RATE_LIMIT_MAX, _rate_limit
from buoy.config import BuoyConfig, FeaturesConfig, PluginEntry, PluginsConfig
from buoy.plugins.loader import PluginManager
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest
from buoy.server import create_app


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    _rate_limit.clear()
    yield
    _rate_limit.clear()


class FakePlugin(Plugin):
    manifest = PluginManifest(id="fake", name="Fake Plugin")

    async def collect(self) -> PanelData:
        return PanelData(status="ok", summary="Fake data")


def _make_client(config: BuoyConfig | None = None) -> TestClient:
    return TestClient(create_app(config or BuoyConfig()), raise_server_exceptions=False)


def _inject_plugin_manager(client: TestClient, mgr: PluginManager) -> None:
    """Replace the real (empty) PluginManager created at startup with *mgr*.

    Avoids the default config's plugin discovery (which would try to run
    real collect() calls against real endpoints) while still exercising the
    real route handler + auth/rate-limit middleware stack.
    """
    client.app.state.buoy.plugin_manager = mgr


class TestCollectEndpointBasics:
    def test_unknown_id_returns_404(self):
        with _make_client() as client:
            r = client.post("/api/plugins/nope/collect", json={})
        assert r.status_code == 404

    def test_missing_content_type_returns_415(self):
        with _make_client() as client:
            r = client.post("/api/plugins/nope/collect")
        assert r.status_code == 415

    def test_get_not_allowed(self):
        with _make_client() as client:
            r = client.get("/api/plugins/nope/collect")
        assert r.status_code == 405

    def test_ok_returns_fresh_detail_payload_with_health(self):
        with _make_client() as client:
            mgr = PluginManager(BuoyConfig())
            mgr._plugins = {"fake": FakePlugin()}
            _inject_plugin_manager(client, mgr)

            r = client.post("/api/plugins/fake/collect", json={})

        assert r.status_code == 200
        body = r.json()
        assert body["id"] == "fake"
        assert "health" in body
        assert body["health"]["consecutive_failures"] == 0
        assert body["status"] == "ok"

    def test_busy_returns_409(self):
        with _make_client() as client:
            mgr = PluginManager(BuoyConfig())
            mgr._plugins = {"fake": FakePlugin()}
            mgr._collecting = {"fake"}
            _inject_plugin_manager(client, mgr)

            r = client.post("/api/plugins/fake/collect", json={})

        assert r.status_code == 409
        assert "error" in r.json()

    def test_disabled_returns_400(self):
        with _make_client() as client:
            mgr = PluginManager(BuoyConfig())
            mgr._plugins = {"fake": FakePlugin()}
            mgr._disabled_ids = {"fake"}
            _inject_plugin_manager(client, mgr)

            r = client.post("/api/plugins/fake/collect", json={})

        assert r.status_code == 400
        assert "error" in r.json()

    def test_no_plugin_manager_returns_404(self):
        with _make_client() as client:
            client.app.state.buoy.plugin_manager = None
            r = client.post("/api/plugins/fake/collect", json={})
        assert r.status_code == 404

    def test_demo_mode_returns_demo_true_without_real_collect(self):
        """Demo mode never calls collect() — /api/plugins auto-enables github by default."""
        config = BuoyConfig()
        config.features = FeaturesConfig(demo_mode=True)
        with _make_client(config) as client:
            r = client.post("/api/plugins/github/collect", json={})

        assert r.status_code == 200
        assert r.json() == {"demo": True}

    def test_ok_result_reflects_new_collect_output(self):
        """A second refresh-now call sees whatever the plugin's collect() returns this time."""
        with _make_client() as client:
            mgr = PluginManager(BuoyConfig())
            mgr._plugins = {"fake": FakePlugin()}
            mgr._latest_data = {"fake": PanelData(status="pending", summary="Collecting…")}
            _inject_plugin_manager(client, mgr)

            r = client.post("/api/plugins/fake/collect", json={})

        assert r.status_code == 200
        body = r.json()
        assert body["status"] == "ok"
        assert body["summary"] == "Fake data"


class TestCollectEndpointProtection:
    def test_not_in_plain_protected_paths(self):
        """The collect route needs the id-aware regex, not a PROTECTED_PATHS prefix."""
        assert not any("/api/plugins/" in p for p in PROTECTED_PATHS)

    def test_rate_limited(self):
        with _make_client() as client:
            mgr = PluginManager(BuoyConfig())
            mgr._plugins = {"fake": FakePlugin()}
            _inject_plugin_manager(client, mgr)

            for _ in range(RATE_LIMIT_MAX):
                r = client.post("/api/plugins/fake/collect", json={})
                assert r.status_code == 200
            r = client.post("/api/plugins/fake/collect", json={})

        assert r.status_code == 429

    def test_401_when_auth_enabled_and_unauthenticated(self):
        config = BuoyConfig()
        config.auth.enabled = True
        config.auth.type = "token"
        config.auth.token = "s3cret"
        with _make_client(config) as client:
            r = client.post("/api/plugins/fake/collect", json={})
        assert r.status_code == 401

    def test_200_when_auth_enabled_and_authenticated(self):
        config = BuoyConfig()
        config.auth.enabled = True
        config.auth.type = "token"
        config.auth.token = "s3cret"
        with _make_client(config) as client:
            mgr = PluginManager(BuoyConfig())
            mgr._plugins = {"fake": FakePlugin()}
            _inject_plugin_manager(client, mgr)

            r = client.post(
                "/api/plugins/fake/collect",
                json={},
                headers={"Authorization": "Bearer s3cret"},
            )

        assert r.status_code == 200

    def test_plugin_detail_get_still_unauthenticated_when_auth_enabled(self):
        """Only the /collect POST is protected — GET /api/plugins/{id} stays public."""
        config = BuoyConfig()
        config.auth.enabled = True
        config.auth.type = "token"
        config.auth.token = "s3cret"
        config.features = FeaturesConfig(demo_mode=True)
        with _make_client(config) as client:
            r = client.get("/api/plugins/github")
        assert r.status_code == 200

    def test_plugin_js_still_unauthenticated_when_auth_enabled(self):
        config = BuoyConfig()
        config.auth.enabled = True
        config.auth.type = "token"
        config.auth.token = "s3cret"
        with _make_client(config) as client:
            r = client.get("/api/plugins/js")
        assert r.status_code == 200


class TestCollectSecretRedaction:
    """Acceptance: secrets never appear in the collect response, YAML or env-sourced."""

    def test_yaml_secret_redacted_in_collect_response(self):
        class TokenPlugin(Plugin):
            manifest = PluginManifest(
                id="tokenplug",
                name="Token Plugin",
                config_schema={"token": {"type": "string", "secret": True}},
            )

            async def collect(self) -> PanelData:
                return PanelData(status="ok")

        with _make_client() as client:
            config = BuoyConfig()
            config.plugins = PluginsConfig(
                builtin={"tokenplug": PluginEntry(enabled=True, settings={"token": "s3cr3t"})}
            )
            mgr = PluginManager(config)
            plugin = TokenPlugin()
            plugin.configure({"token": "s3cr3t"})
            mgr._plugins = {"tokenplug": plugin}
            _inject_plugin_manager(client, mgr)

            r = client.post("/api/plugins/tokenplug/collect", json={})

        assert r.status_code == 200
        assert "s3cr3t" not in r.text
        row = next(row for row in r.json()["config"] if row["key"] == "token")
        assert row["value"] == "***REDACTED***"

    def test_env_secret_redacted_in_collect_response(self, monkeypatch):
        monkeypatch.setenv("BUOY_PLUGIN_TOKENPLUG_TOKEN", "env-s3cr3t")

        class TokenPlugin(Plugin):
            manifest = PluginManifest(
                id="tokenplug",
                name="Token Plugin",
                config_schema={"token": {"type": "string", "secret": True}},
            )

            async def collect(self) -> PanelData:
                return PanelData(status="ok")

        with _make_client() as client:
            config = BuoyConfig()
            config.plugins = PluginsConfig(builtin={"tokenplug": PluginEntry(enabled=True)})
            mgr = PluginManager(config)
            plugin = TokenPlugin()
            plugin.configure({"token": "env-s3cr3t"})
            mgr._plugins = {"tokenplug": plugin}
            _inject_plugin_manager(client, mgr)

            r = client.post("/api/plugins/tokenplug/collect", json={})

        assert r.status_code == 200
        assert "env-s3cr3t" not in r.text
        row = next(row for row in r.json()["config"] if row["key"] == "token")
        assert row["value"] == "***REDACTED***"
        assert row["source"] == "env:BUOY_PLUGIN_TOKENPLUG_TOKEN"
