"""Tests for Buoy authentication middleware."""

import base64
import time

from buoy.auth import RATE_LIMIT_MAX, RATE_LIMIT_WINDOW, AuthMiddleware, _rate_limit


class FakeAuthConfig:
    """Minimal auth config for testing."""

    def __init__(self, enabled=True, auth_type="token", token=None, username=None, password=None):
        self.enabled = enabled
        self.type = auth_type
        self.token = token
        self.username = username
        self.password = password


class FakeRequest:
    """Minimal request object for unit-testing auth logic."""

    def __init__(self, path="/api/stats", headers=None, client_host="127.0.0.1"):
        self.url = type("URL", (), {"path": path})()
        self.headers = headers or {}
        self.client = type("Client", (), {"host": client_host})()


class TestProtectedPathDetection:
    """Test _is_protected path matching."""

    def _make_middleware(self, **kwargs):
        config = FakeAuthConfig(**kwargs)
        # AuthMiddleware needs an app, but we're testing methods directly
        mw = AuthMiddleware.__new__(AuthMiddleware)
        mw.auth_config = config
        return mw

    def test_container_path_is_protected(self):
        mw = self._make_middleware()
        assert mw._is_protected("/api/container/grafana/restart") is True

    def test_container_logs_is_protected(self):
        mw = self._make_middleware()
        assert mw._is_protected("/api/container/plane-api-1/logs") is True

    def test_stats_not_protected(self):
        mw = self._make_middleware()
        assert mw._is_protected("/api/stats") is False

    def test_health_not_protected(self):
        mw = self._make_middleware()
        assert mw._is_protected("/api/health") is False

    def test_fleet_not_protected(self):
        mw = self._make_middleware()
        assert mw._is_protected("/api/fleet") is False


class TestTokenAuth:
    """Test Bearer token authentication."""

    def _make_middleware(self, token="secret123"):
        config = FakeAuthConfig(auth_type="token", token=token)
        mw = AuthMiddleware.__new__(AuthMiddleware)
        mw.auth_config = config
        return mw

    def test_valid_token(self):
        mw = self._make_middleware(token="my-secret")
        request = FakeRequest(headers={"Authorization": "Bearer my-secret"})
        assert mw._authenticate(request) is True

    def test_invalid_token(self):
        mw = self._make_middleware(token="my-secret")
        request = FakeRequest(headers={"Authorization": "Bearer wrong-token"})
        assert mw._authenticate(request) is False

    def test_missing_header(self):
        mw = self._make_middleware(token="my-secret")
        request = FakeRequest(headers={})
        assert mw._authenticate(request) is False

    def test_wrong_scheme(self):
        mw = self._make_middleware(token="my-secret")
        request = FakeRequest(headers={"Authorization": "Basic abc123"})
        assert mw._authenticate(request) is False

    def test_no_token_configured_passes(self):
        mw = self._make_middleware(token=None)
        request = FakeRequest(headers={})
        assert mw._authenticate(request) is True

    def test_empty_token_configured_passes(self):
        mw = self._make_middleware(token="")
        request = FakeRequest(headers={})
        assert mw._authenticate(request) is True


class TestBasicAuth:
    """Test HTTP Basic authentication."""

    def _make_middleware(self, username="admin", password="pass123"):
        config = FakeAuthConfig(auth_type="basic", username=username, password=password)
        mw = AuthMiddleware.__new__(AuthMiddleware)
        mw.auth_config = config
        return mw

    def _encode_basic(self, user, password):
        creds = base64.b64encode(f"{user}:{password}".encode()).decode()
        return f"Basic {creds}"

    def test_valid_credentials(self):
        mw = self._make_middleware()
        request = FakeRequest(headers={"Authorization": self._encode_basic("admin", "pass123")})
        assert mw._authenticate(request) is True

    def test_wrong_password(self):
        mw = self._make_middleware()
        request = FakeRequest(headers={"Authorization": self._encode_basic("admin", "wrong")})
        assert mw._authenticate(request) is False

    def test_wrong_username(self):
        mw = self._make_middleware()
        request = FakeRequest(headers={"Authorization": self._encode_basic("root", "pass123")})
        assert mw._authenticate(request) is False

    def test_no_credentials_configured_passes(self):
        mw = self._make_middleware(username=None, password=None)
        request = FakeRequest(headers={})
        assert mw._authenticate(request) is True

    def test_malformed_base64(self):
        mw = self._make_middleware()
        request = FakeRequest(headers={"Authorization": "Basic !!!invalid!!!"})
        assert mw._authenticate(request) is False

    def test_missing_colon_in_decoded(self):
        # Valid base64 but no colon separator
        creds = base64.b64encode(b"nocolon").decode()
        mw = self._make_middleware()
        request = FakeRequest(headers={"Authorization": f"Basic {creds}"})
        assert mw._authenticate(request) is False


class TestRateLimiting:
    """Test the sliding window rate limiter."""

    def _make_middleware(self):
        config = FakeAuthConfig()
        mw = AuthMiddleware.__new__(AuthMiddleware)
        mw.auth_config = config
        return mw

    def setup_method(self):
        """Clear rate limit state between tests."""
        _rate_limit.clear()

    def test_first_request_passes(self):
        mw = self._make_middleware()
        assert mw._check_rate_limit("10.0.0.1") is True

    def test_under_limit_passes(self):
        mw = self._make_middleware()
        for _ in range(RATE_LIMIT_MAX - 1):
            mw._check_rate_limit("10.0.0.2")
        assert mw._check_rate_limit("10.0.0.2") is True

    def test_at_limit_blocks(self):
        mw = self._make_middleware()
        for _ in range(RATE_LIMIT_MAX):
            mw._check_rate_limit("10.0.0.3")
        assert mw._check_rate_limit("10.0.0.3") is False

    def test_different_ips_independent(self):
        mw = self._make_middleware()
        for _ in range(RATE_LIMIT_MAX):
            mw._check_rate_limit("10.0.0.4")
        # 10.0.0.4 is blocked, but 10.0.0.5 is not
        assert mw._check_rate_limit("10.0.0.4") is False
        assert mw._check_rate_limit("10.0.0.5") is True

    def test_old_entries_expire(self):
        mw = self._make_middleware()
        ip = "10.0.0.6"
        # Fill with timestamps from the past (beyond the window)
        _rate_limit[ip] = [time.time() - RATE_LIMIT_WINDOW - 10] * RATE_LIMIT_MAX
        # Should pass because old entries are pruned
        assert mw._check_rate_limit(ip) is True
