"""Tests for CORS policy (SEC-2: no wildcard, same-origin by default)."""

import pytest
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.server import create_app

EVIL_ORIGIN = "https://evil.example"
ALLOWED_ORIGIN = "https://harbor.example.ts.net"


def _make_config(allowed_origins=None):
    config = BuoyConfig()
    config.node = NodeConfig(name="compass")
    config.network = NetworkConfig(allowed_origins=allowed_origins or [])
    config.features = FeaturesConfig(websocket=False, demo_mode=True)
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    """Each test gets its own DB in tmp_path and a clean global state."""
    monkeypatch.chdir(tmp_path)
    yield
    if srv._metric_store:
        srv._metric_store.close()
        srv._metric_store = None


def _preflight(client, origin):
    return client.options(
        "/api/container/anything/restart",
        headers={
            "Origin": origin,
            "Access-Control-Request-Method": "POST",
        },
    )


class TestDefaultCORS:
    """No allowlist configured: same-origin only."""

    def test_no_wildcard_origin_header(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = _preflight(client, EVIL_ORIGIN)
        assert r.headers.get("access-control-allow-origin") != "*"

    def test_foreign_origin_not_granted(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = _preflight(client, EVIL_ORIGIN)
        assert "access-control-allow-origin" not in r.headers
        assert "access-control-allow-methods" not in r.headers

    def test_simple_get_no_cors_headers(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/health", headers={"Origin": EVIL_ORIGIN})
        assert "access-control-allow-origin" not in r.headers


class TestAllowlistedCORS:
    """Explicit origin allowlist configured (e.g. fleet peers)."""

    def test_allowlisted_origin_echoed(self):
        app = create_app(_make_config(allowed_origins=[ALLOWED_ORIGIN]))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = _preflight(client, ALLOWED_ORIGIN)
        assert r.headers.get("access-control-allow-origin") == ALLOWED_ORIGIN
        assert r.headers.get("access-control-allow-origin") != "*"

    def test_nonlisted_origin_denied(self):
        app = create_app(_make_config(allowed_origins=[ALLOWED_ORIGIN]))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = _preflight(client, EVIL_ORIGIN)
        assert r.headers.get("access-control-allow-origin") != EVIL_ORIGIN
        assert r.headers.get("access-control-allow-origin") != "*"


class TestRestartRequiresJSON:
    """The restart endpoint must reject CORS "simple requests".

    A cross-origin <form> POST, or a fetch() using a CORS-safelisted
    Content-Type (text/plain, application/x-www-form-urlencoded,
    multipart/form-data), is sent by the browser *without* a preflight —
    even with no CORS middleware installed at all. Requiring
    Content-Type: application/json forces every cross-origin caller through
    a preflight, so the missing Access-Control-Allow-Origin response is what
    actually stops the browser from issuing the real POST.
    """

    def test_missing_content_type_rejected(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post("/api/container/anything/restart")
        assert r.status_code == 415

    def test_form_urlencoded_content_type_rejected(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/api/container/anything/restart",
                headers={"Content-Type": "application/x-www-form-urlencoded"},
                content=b"",
            )
        assert r.status_code == 415

    def test_text_plain_content_type_rejected(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/api/container/anything/restart",
                headers={"Content-Type": "text/plain"},
                content=b"",
            )
        assert r.status_code == 415

    def test_json_content_type_accepted(self):
        app = create_app(_make_config())
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.post(
                "/api/container/anything/restart",
                headers={"Content-Type": "application/json"},
                json={},
            )
        assert r.status_code == 200
