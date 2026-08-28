"""Tests for the /api/config/debug endpoint — redaction logic and route protection."""

import dataclasses

import pytest
from starlette.testclient import TestClient

from buoy.auth import PROTECTED_PATHS, AuthMiddleware, _rate_limit
from buoy.config import _build_config
from buoy.server import _redact_secrets, create_app


@pytest.fixture(autouse=True)
def _clear_rate_limit():
    """The debug handler's rate limiter shares module-level state (buoy.auth._rate_limit)
    keyed by client IP; clear it so requests in one test don't count against another."""
    _rate_limit.clear()
    yield
    _rate_limit.clear()


class FakeAuthConfig:
    enabled = True
    type = "token"
    token = "x"
    username = None
    password = None


def _make_middleware():
    mw = AuthMiddleware.__new__(AuthMiddleware)
    mw.auth_config = FakeAuthConfig()
    mw.base_path = ""
    return mw


class TestRedactSecrets:
    """Unit tests for the _redact_secrets helper."""

    def test_redacts_token(self):
        assert _redact_secrets({"token": "secret123"})["token"] == "***REDACTED***"

    def test_redacts_password(self):
        assert _redact_secrets({"password": "hunter2"})["password"] == "***REDACTED***"

    def test_redacts_secret_fragment(self):
        assert _redact_secrets({"api_secret": "abc"})["api_secret"] == "***REDACTED***"

    def test_redacts_key_fragment(self):
        assert _redact_secrets({"api_key": "xyz"})["api_key"] == "***REDACTED***"

    def test_preserves_non_secret_strings(self):
        result = _redact_secrets({"name": "compass", "tier": "1A"})
        assert result["name"] == "compass"
        assert result["tier"] == "1A"

    def test_preserves_booleans_with_key_in_name(self):
        # keyboard_shortcuts contains "key" but is a bool — must NOT be redacted
        result = _redact_secrets({"keyboard_shortcuts": True})
        assert result["keyboard_shortcuts"] is True

    def test_preserves_integers(self):
        assert _redact_secrets({"stats_interval": 5})["stats_interval"] == 5

    def test_empty_string_not_redacted(self):
        assert _redact_secrets({"token": ""})["token"] == ""

    def test_redacts_nested_secret(self):
        nested = {"auth": {"token": "my-token", "type": "token", "enabled": True}}
        result = _redact_secrets(nested)
        assert result["auth"]["token"] == "***REDACTED***"
        assert result["auth"]["type"] == "token"
        assert result["auth"]["enabled"] is True

    def test_redacts_deeply_nested(self):
        data = {"plugins": {"builtin": {"github": {"settings": {"token": "ghp_xxx"}}}}}
        result = _redact_secrets(data)
        assert result["plugins"]["builtin"]["github"]["settings"]["token"] == "***REDACTED***"

    def test_handles_list_of_dicts(self):
        data = {"items": [{"name": "a"}, {"name": "b"}]}
        result = _redact_secrets(data)
        assert result["items"][0]["name"] == "a"
        assert result["items"][1]["name"] == "b"

    def test_no_secrets_returns_equivalent_dict(self):
        data = {"name": "buoy", "port": 8090}
        assert _redact_secrets(data) == data


class TestConfigDebugRedaction:
    """Test that full config serialization + redaction works correctly."""

    def test_auth_token_redacted(self):
        config = _build_config({"auth": {"enabled": True, "type": "token", "token": "secret123"}})
        result = _redact_secrets(dataclasses.asdict(config))
        assert result["auth"]["token"] == "***REDACTED***"

    def test_auth_password_redacted(self):
        config = _build_config(
            {"auth": {"enabled": True, "type": "basic", "username": "admin", "password": "p4ss"}}
        )
        result = _redact_secrets(dataclasses.asdict(config))
        assert result["auth"]["password"] == "***REDACTED***"

    def test_plugin_token_redacted(self):
        config = _build_config(
            {"plugins": {"builtin": {"github": {"enabled": True, "token": "ghp_xxx"}}}}
        )
        result = _redact_secrets(dataclasses.asdict(config))
        assert result["plugins"]["builtin"]["github"]["settings"]["token"] == "***REDACTED***"

    def test_non_secret_fields_intact(self):
        config = _build_config({"node": {"name": "harbor", "tier": "1A"}})
        result = _redact_secrets(dataclasses.asdict(config))
        assert result["node"]["name"] == "harbor"
        assert result["node"]["tier"] == "1A"

    def test_keyboard_shortcuts_not_redacted(self):
        config = _build_config({})
        result = _redact_secrets(dataclasses.asdict(config))
        assert result["features"]["keyboard_shortcuts"] is True

    def test_top_level_keys_present(self):
        config = _build_config({})
        result = _redact_secrets(dataclasses.asdict(config))
        for k in {"node", "network", "services", "theme", "auth", "features", "refresh", "plugins"}:
            assert k in result, f"Missing top-level key: {k}"

    def test_asdict_does_not_mutate_live_config(self):
        config = _build_config({"auth": {"enabled": True, "type": "token", "token": "live-secret"}})
        _redact_secrets(dataclasses.asdict(config))
        assert config.auth.token == "live-secret"


class TestConfigDebugProtectedPath:
    """Test that /api/config/debug requires authentication."""

    def test_config_debug_is_protected(self):
        mw = _make_middleware()
        assert mw._is_protected("/api/config/debug") is True

    def test_public_config_not_protected(self):
        mw = _make_middleware()
        assert mw._is_protected("/api/config") is False

    def test_config_debug_in_protected_paths_set(self):
        assert "/api/config/debug" in PROTECTED_PATHS


# ── Helpers for handler-level tests ───────────────────────────────────────────


def _make_test_client(token: str, auth_enabled: bool = False) -> TestClient:
    """Create a TestClient with a minimal app-local config."""
    config = _build_config({"auth": {"enabled": auth_enabled, "type": "token", "token": token}})
    return TestClient(create_app(config), raise_server_exceptions=False)


class TestConfigDebugHandlerAuth:
    """Test that api_config_debug enforces auth independently of auth.enabled.

    SEC-4: the endpoint must be inaccessible on a default install where
    auth.enabled=False and no token is configured.
    """

    def test_no_token_configured_returns_403(self):
        """Default install: auth.enabled=False, no token → 403."""
        config = _build_config({})  # all defaults: auth.enabled=False, token=""
        app = create_app(config)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/api/config/debug")
        assert resp.status_code == 403

    def test_token_configured_no_header_returns_401(self):
        """Token set but no Authorization header → 401."""
        client = _make_test_client(token="my-secret")
        resp = client.get("/api/config/debug")
        assert resp.status_code == 401
        assert resp.headers.get("WWW-Authenticate") is not None

    def test_token_configured_wrong_token_returns_401(self):
        """Token set, wrong token provided → 401."""
        client = _make_test_client(token="my-secret")
        resp = client.get("/api/config/debug", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 401

    def test_token_configured_correct_token_returns_200(self):
        """Token set and correct token provided → 200."""
        client = _make_test_client(token="my-secret")
        resp = client.get("/api/config/debug", headers={"Authorization": "Bearer my-secret"})
        assert resp.status_code == 200
        data = resp.json()
        # All top-level keys should be present
        for k in {"node", "network", "services", "theme", "auth", "features", "refresh", "plugins"}:
            assert k in data, f"Missing top-level key: {k}"

    def test_token_configured_correct_token_secrets_redacted(self):
        """Secrets are redacted in the 200 response."""
        client = _make_test_client(token="my-secret")
        resp = client.get("/api/config/debug", headers={"Authorization": "Bearer my-secret"})
        assert resp.status_code == 200
        data = resp.json()
        # The token itself should be redacted
        assert data["auth"]["token"] == "***REDACTED***"

    def test_auth_enabled_false_with_token_still_requires_auth(self):
        """auth.enabled=False but token configured → endpoint still enforces the token."""
        client = _make_test_client(token="my-secret", auth_enabled=False)
        # No header → 401
        resp = client.get("/api/config/debug")
        assert resp.status_code == 401
        # Correct header → 200
        resp = client.get("/api/config/debug", headers={"Authorization": "Bearer my-secret"})
        assert resp.status_code == 200

    def test_basic_auth_without_token_still_returns_403(self):
        """auth.type=basic with username/password but no auth.token → 403.

        This is intentional: the debug endpoint is token-only (see
        api_config_debug docstring), so basic-auth-only installs must also
        set auth.token to reach it. Basic credentials alone are not accepted.
        """
        config = _build_config(
            {
                "auth": {
                    "enabled": True,
                    "type": "basic",
                    "username": "admin",
                    "password": "hunter2",
                    "token": "",
                }
            }
        )
        app = create_app(config)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get(
            "/api/config/debug",
            auth=("admin", "hunter2"),
        )
        assert resp.status_code == 403


class TestConfigDebugRateLimit:
    """The handler enforces its own rate limit so token brute-forcing isn't
    possible when auth.enabled=False and AuthMiddleware is never installed."""

    def test_rate_limited_when_auth_middleware_not_installed(self):
        """auth.enabled=False, token set → repeated wrong-token guesses get 429."""
        from buoy.auth import RATE_LIMIT_MAX

        client = _make_test_client(token="my-secret", auth_enabled=False)
        for _ in range(RATE_LIMIT_MAX):
            resp = client.get("/api/config/debug", headers={"Authorization": "Bearer wrong"})
            assert resp.status_code == 401
        resp = client.get("/api/config/debug", headers={"Authorization": "Bearer wrong"})
        assert resp.status_code == 429

    def test_rate_limit_checked_before_token_configured_check(self):
        """Rate limiting applies even to the no-token-configured (403) path."""
        from buoy.auth import RATE_LIMIT_MAX

        config = _build_config({})  # auth.enabled=False, token=""
        app = create_app(config)
        client = TestClient(app, raise_server_exceptions=False)
        for _ in range(RATE_LIMIT_MAX):
            resp = client.get("/api/config/debug")
            assert resp.status_code == 403
        resp = client.get("/api/config/debug")
        assert resp.status_code == 429

    def test_no_double_counting_when_auth_middleware_active(self):
        """auth.enabled=True → AuthMiddleware already rate-limits this path, so
        the handler must not check again. All RATE_LIMIT_MAX requests should
        succeed, not just half of them.
        """
        from buoy.auth import RATE_LIMIT_MAX

        client = _make_test_client(token="my-secret", auth_enabled=True)
        for _ in range(RATE_LIMIT_MAX):
            resp = client.get("/api/config/debug", headers={"Authorization": "Bearer my-secret"})
            assert resp.status_code == 200
        resp = client.get("/api/config/debug", headers={"Authorization": "Bearer my-secret"})
        assert resp.status_code == 429
