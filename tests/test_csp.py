"""Tests for Content-Security-Policy header (SEC-6)."""

import pytest
from starlette.testclient import TestClient

import buoy.server as srv
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, PeerConfig
from buoy.server import create_app

PEER_URL = "https://harbor.example.ts.net"


def _make_config(peers=None):
    config = BuoyConfig()
    config.node = NodeConfig(name="compass")
    config.network = NetworkConfig(allowed_origins=[], peers=peers or [])
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


def test_csp_header_present_on_api():
    app = create_app(_make_config())
    with TestClient(app) as client:
        r = client.get("/api/health")
    assert "content-security-policy" in r.headers


def test_csp_header_present_on_root():
    app = create_app(_make_config())
    with TestClient(app) as client:
        r = client.get("/")
    assert "content-security-policy" in r.headers


def test_csp_locks_down_dangerous_directives():
    app = create_app(_make_config())
    with TestClient(app) as client:
        r = client.get("/api/health")
    csp = r.headers["content-security-policy"]
    assert "default-src 'self'" in csp
    assert "object-src 'none'" in csp
    assert "base-uri 'none'" in csp
    assert "frame-ancestors 'none'" in csp


def test_csp_no_wildcard():
    app = create_app(_make_config())
    with TestClient(app) as client:
        r = client.get("/api/health")
    csp = r.headers["content-security-policy"]
    assert "*" not in csp
    assert "script-src 'self' 'unsafe-eval'" in csp
    assert "'unsafe-inline'" not in csp.split("script-src")[1].split(";")[0]


def test_csp_connect_src_allows_configured_fleet_peers():
    """fleet.js fetches each peer's /api/stats directly from the browser,
    so connect-src must allow configured peer origins in addition to 'self'."""
    app = create_app(_make_config(peers=[PeerConfig(name="harbor", url=PEER_URL)]))
    with TestClient(app) as client:
        r = client.get("/api/health")
    connect_src = r.headers["content-security-policy"].split("connect-src")[1].split(";")[0]
    assert "'self'" in connect_src
    assert PEER_URL in connect_src


def test_csp_connect_src_self_only_without_peers():
    app = create_app(_make_config())
    with TestClient(app) as client:
        r = client.get("/api/health")
    connect_src = r.headers["content-security-policy"].split("connect-src")[1].split(";")[0]
    assert connect_src.strip() == "'self'"
