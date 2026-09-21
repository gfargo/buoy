"""Tests for the PWA manifest and service worker routes (OSS-1540 / buoy#202)."""

import pytest
from starlette.testclient import TestClient

from buoy._version import VERSION
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.server import _resolve_static_dir, create_app


def _make_config(base_path="", pwa=True):
    config = BuoyConfig()
    config.node = NodeConfig(name="compass")
    config.network = NetworkConfig(base_path=base_path)
    config.features = FeaturesConfig(websocket=False, demo_mode=True, pwa=pwa)
    return config


@pytest.fixture(autouse=True)
def isolate_store(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)


class TestManifest:
    def test_manifest_ok(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/manifest.webmanifest")
        assert r.status_code == 200
        assert "manifest" in r.headers["content-type"]
        data = r.json()
        assert "compass" in data["name"]
        assert data["display"] == "standalone"
        assert data["start_url"] == "/"
        assert data["scope"] == "/"

    def test_manifest_icons_exist_on_disk(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/manifest.webmanifest")
        data = r.json()
        static_dir = _resolve_static_dir()
        assert data["icons"], "manifest must declare at least one icon"
        for icon in data["icons"]:
            filename = icon["src"].rsplit("/", 1)[-1]
            assert (static_dir / "icons" / filename).exists(), (
                f"manifest references {icon['src']!r} but {filename} is missing from static/icons/"
            )

    def test_manifest_has_maskable_icon(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/manifest.webmanifest")
        data = r.json()
        purposes = [icon.get("purpose") for icon in data["icons"]]
        assert "maskable" in purposes

    def test_manifest_under_base_path(self):
        app = create_app(_make_config(base_path="/buoy"))
        with TestClient(app) as client:
            r = client.get("/buoy/manifest.webmanifest")
        assert r.status_code == 200
        data = r.json()
        assert data["start_url"] == "/buoy/"
        assert data["scope"] == "/buoy/"
        for icon in data["icons"]:
            assert icon["src"].startswith("/buoy/static/icons/")

    def test_manifest_disabled_when_pwa_off(self):
        app = create_app(_make_config(pwa=False))
        with TestClient(app) as client:
            r = client.get("/")
        assert 'rel="manifest"' not in r.text


class TestServiceWorker:
    def test_sw_ok(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/sw.js")
        assert r.status_code == 200
        assert "javascript" in r.headers["content-type"]
        assert r.headers.get("cache-control") == "no-cache"
        assert "__BUOY_" not in r.text
        assert VERSION in r.text

    def test_sw_under_base_path(self):
        app = create_app(_make_config(base_path="/buoy"))
        with TestClient(app) as client:
            r = client.get("/buoy/sw.js")
        assert r.status_code == 200
        assert "__BUOY_" not in r.text

    def test_sw_tombstone_when_pwa_off(self):
        app = create_app(_make_config(pwa=False))
        with TestClient(app) as client:
            r = client.get("/sw.js")
        assert r.status_code == 200
        assert "unregister" in r.text


class TestConfigExposesPwaFlag:
    def test_api_config_reports_pwa_enabled(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            r = client.get("/api/config")
        assert r.json()["features"]["pwa"] is True

    def test_api_config_reports_pwa_disabled(self):
        app = create_app(_make_config(pwa=False))
        with TestClient(app) as client:
            r = client.get("/api/config")
        assert r.json()["features"]["pwa"] is False
