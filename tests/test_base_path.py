"""Tests for network.base_path (buoy#102 / OSS-1299): reverse-proxy sub-path hosting."""

import pytest
from starlette.testclient import TestClient

from buoy.auth import RATE_LIMIT_MAX, _rate_limit
from buoy.config import (
    BuoyConfig,
    FeaturesConfig,
    NetworkConfig,
    NodeConfig,
    load_config,
    normalize_base_path,
)
from buoy.server import create_app


def _make_config(base_path="/buoy", auth_enabled=False):
    config = BuoyConfig()
    config.node = NodeConfig(name="compass")
    config.network = NetworkConfig(base_path=base_path)
    config.features = FeaturesConfig(websocket=False, demo_mode=True)
    if auth_enabled:
        config.auth.enabled = True
        config.auth.type = "token"
        config.auth.token = "s3cret"
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Each test gets an isolated working directory and limiter reset."""
    monkeypatch.chdir(tmp_path)
    _rate_limit.clear()
    yield
    _rate_limit.clear()


class TestNormalizeBasePath:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("", ""),
            ("/", ""),
            ("buoy", "/buoy"),
            ("/buoy", "/buoy"),
            ("/buoy/", "/buoy"),
            ("//buoy//", "/buoy"),
            ("//a//b//", "/a/b"),
            ("a/b", "/a/b"),
        ],
    )
    def test_normalize(self, raw, expected):
        assert normalize_base_path(raw) == expected

    def test_env_override(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv("BUOY_NETWORK_BASE_PATH", "/buoy/")
        config = load_config()
        assert config.network.base_path == "/buoy"


class TestPrefixedRouting:
    def test_prefixed_health(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/buoy/api/health")
        assert r.status_code == 200

    def test_prefixed_root_serves_html(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/buoy/")
        assert r.status_code == 200
        assert "text/html" in r.headers["content-type"]

    def test_prefixed_static_asset(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/buoy/static/js/buoy.js")
        assert r.status_code == 200

    def test_prefixed_stats(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/buoy/api/stats")
        assert r.status_code == 200

    def test_unprefixed_still_works_when_base_path_set(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r_health = client.get("/api/health")
            r_root = client.get("/")
        assert r_health.status_code == 200
        assert r_root.status_code == 200

    def test_trailing_slash_redirect(self):
        """Starlette's default redirect_slashes behaviour, locked down explicitly."""
        app = create_app(_make_config())
        with TestClient(app, follow_redirects=False) as client:
            r = client.get("/buoy")
        assert r.status_code == 307
        assert r.headers["location"] == "http://testserver/buoy/"


class TestHtmlTemplating:
    def test_prefixed_html_rewrites_static_urls(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/buoy/")
        assert "/buoy/static/js/buoy.js" in r.text
        assert 'content="/buoy"' in r.text
        assert '"/static/' not in r.text

    def test_no_base_path_html_unchanged(self):
        app = create_app(_make_config(base_path=""))
        with TestClient(app) as client:
            r = client.get("/")
        assert '"/static/js/buoy.js"' in r.text
        assert 'content=""' in r.text


class TestSecurityRegression:
    """A prefixed path must not bypass auth or rate limiting (BUG-45 risk #1)."""

    def test_prefixed_restart_requires_auth(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/buoy/api/container/foo/restart",
                headers={"Content-Type": "application/json"},
                json={},
            )
        assert r.status_code == 401

    def test_unprefixed_restart_requires_auth(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/api/container/foo/restart",
                headers={"Content-Type": "application/json"},
                json={},
            )
        assert r.status_code == 401

    def test_prefixed_config_debug_requires_auth(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/buoy/api/config/debug")
        assert r.status_code == 401

    def test_prefixed_container_path_rate_limited(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            for _ in range(RATE_LIMIT_MAX):
                r = client.get("/buoy/api/container/foo")
                assert r.status_code in (401, 503)
            r = client.get("/buoy/api/container/foo")
        assert r.status_code == 429

    def test_base_path_prefix_of_protected_path_still_requires_auth(self):
        """A base_path that is itself a prefix of a protected path (e.g. /api)
        must not strip away the protected-path match and let the root routes
        serve the request unauthenticated."""
        app = create_app(_make_config(base_path="/api", auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/api/container/foo/restart",
                headers={"Content-Type": "application/json"},
                json={},
            )
        assert r.status_code == 401


class TestCacheControlHeader:
    def test_prefixed_static_has_no_cache_control(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/buoy/static/css/buoy.css")
        assert "cache-control" not in r.headers

    def test_prefixed_api_has_cache_control(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/buoy/api/health")
        assert r.headers.get("cache-control") == "no-cache"


class TestNoBasePathUnchanged:
    def test_health(self):
        app = create_app(_make_config(base_path=""))
        with TestClient(app) as client:
            r = client.get("/api/health")
        assert r.status_code == 200
