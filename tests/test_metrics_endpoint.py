"""Tests for the /metrics endpoint — gating, auth, and rate-limiting.

Acceptance criteria (OSS-1301 / buoy#100):
- /metrics returns 404 when prometheus_exporter plugin is disabled (default).
- /metrics returns 404 when plugins.enabled=False regardless of per-plugin flag.
- /metrics returns 200 with Prometheus text when the plugin is enabled.
- /metrics is always rate-limited (PROTECTED_PATHS membership).
- /metrics requires auth when auth.enabled=True.
"""

from __future__ import annotations

import pytest
from starlette.testclient import TestClient

from buoy.auth import PROTECTED_PATHS, RATE_LIMIT_MAX, _rate_limit
from buoy.config import BuoyConfig, PluginEntry, PluginsConfig
from buoy.server import _prometheus_enabled, create_app

# ── Fixtures ───────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    """Prevent rate-limit state from bleeding between tests."""
    _rate_limit.clear()
    yield
    _rate_limit.clear()


def _config_with_plugin_enabled() -> BuoyConfig:
    """Return a BuoyConfig with prometheus_exporter enabled."""
    config = BuoyConfig()
    config.plugins = PluginsConfig(
        enabled=True,
        builtin={"prometheus_exporter": PluginEntry(enabled=True)},
    )
    return config


def _config_plugin_disabled() -> BuoyConfig:
    """Return a BuoyConfig with prometheus_exporter explicitly disabled."""
    config = BuoyConfig()
    config.plugins = PluginsConfig(
        enabled=True,
        builtin={"prometheus_exporter": PluginEntry(enabled=False)},
    )
    return config


def _make_client(config: BuoyConfig) -> TestClient:
    return TestClient(create_app(config), raise_server_exceptions=False)


# ── Unit tests for _prometheus_enabled ────────────────────────────────────────


class TestPrometheusEnabledHelper:
    def test_returns_false_for_default_config(self):
        assert _prometheus_enabled(BuoyConfig()) is False

    def test_returns_false_when_plugins_globally_disabled(self):
        config = BuoyConfig()
        config.plugins = PluginsConfig(
            enabled=False,
            builtin={"prometheus_exporter": PluginEntry(enabled=True)},
        )
        assert _prometheus_enabled(config) is False

    def test_returns_false_when_plugin_entry_disabled(self):
        assert _prometheus_enabled(_config_plugin_disabled()) is False

    def test_returns_false_when_no_plugin_entry(self):
        config = BuoyConfig()
        config.plugins = PluginsConfig(enabled=True, builtin={})
        assert _prometheus_enabled(config) is False

    def test_returns_true_when_plugin_enabled(self):
        assert _prometheus_enabled(_config_with_plugin_enabled()) is True

    def test_returns_false_for_none_config(self):
        assert _prometheus_enabled(None) is False


# ── Route-level tests ─────────────────────────────────────────────────────────


class TestMetrics404WhenDisabled:
    def test_default_config_returns_404(self):
        """Default install: no prometheus_exporter entry → /metrics not found."""
        client = _make_client(BuoyConfig())
        resp = client.get("/metrics")
        assert resp.status_code == 404

    def test_plugin_explicitly_disabled_returns_404(self):
        client = _make_client(_config_plugin_disabled())
        resp = client.get("/metrics")
        assert resp.status_code == 404

    def test_plugins_globally_disabled_returns_404(self):
        """plugins.enabled=False + per-plugin entry enabled=True → still 404."""
        config = BuoyConfig()
        config.plugins = PluginsConfig(
            enabled=False,
            builtin={"prometheus_exporter": PluginEntry(enabled=True)},
        )
        client = _make_client(config)
        resp = client.get("/metrics")
        assert resp.status_code == 404


class TestMetrics200WhenEnabled:
    def test_returns_200_with_prometheus_content_type(self):
        client = _make_client(_config_with_plugin_enabled())
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        assert "version=0.0.4" in resp.headers["content-type"]

    def test_response_body_contains_expected_metrics(self):
        client = _make_client(_config_with_plugin_enabled())
        resp = client.get("/metrics")
        assert resp.status_code == 200
        body = resp.text
        assert "buoy_cpu_percent" in body
        assert "buoy_memory_used_bytes" in body
        assert "buoy_containers_running" in body


# ── PROTECTED_PATHS membership ────────────────────────────────────────────────


class TestMetricsInProtectedPaths:
    def test_metrics_in_protected_paths_set(self):
        assert "/metrics" in PROTECTED_PATHS


# ── Rate limiting ─────────────────────────────────────────────────────────────


class TestMetricsRateLimited:
    def test_rate_limited_when_plugin_enabled(self):
        """Exceeding RATE_LIMIT_MAX requests from the same IP yields 429."""
        client = _make_client(_config_with_plugin_enabled())
        for _ in range(RATE_LIMIT_MAX):
            resp = client.get("/metrics")
            assert resp.status_code == 200
        resp = client.get("/metrics")
        assert resp.status_code == 429


# ── Auth gating ───────────────────────────────────────────────────────────────


class TestMetricsAuthGating:
    def _make_authed_client(self, token: str = "s3cret") -> TestClient:
        config = _config_with_plugin_enabled()
        config.auth.enabled = True
        config.auth.type = "token"
        config.auth.token = token
        return _make_client(config)

    def test_unauthenticated_returns_401_when_auth_enabled(self):
        client = self._make_authed_client()
        resp = client.get("/metrics")
        assert resp.status_code == 401

    def test_wrong_token_returns_401(self):
        client = self._make_authed_client(token="s3cret")
        resp = client.get("/metrics", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_valid_token_returns_200(self):
        client = self._make_authed_client(token="s3cret")
        resp = client.get("/metrics", headers={"Authorization": "Bearer s3cret"})
        assert resp.status_code == 200
        assert "buoy_cpu_percent" in resp.text

    def test_no_auth_required_when_auth_disabled(self):
        """auth.enabled=False → /metrics accessible without credentials."""
        client = _make_client(_config_with_plugin_enabled())
        resp = client.get("/metrics")
        assert resp.status_code == 200
