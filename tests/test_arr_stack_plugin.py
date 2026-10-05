"""Tests for the *arr stack plugin (Sonarr/Radarr/Prowlarr/Bazarr)."""

from __future__ import annotations

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest

from buoy.plugins.builtin.arr_stack import ArrStackPlugin
from buoy.plugins.loader import validate_plugin_config

_ALL_CONFIG = {
    "sonarr_url": "http://sonarr.local",
    "sonarr_api_key": "sonarr-key",
    "radarr_url": "http://radarr.local",
    "radarr_api_key": "radarr-key",
    "prowlarr_url": "http://prowlarr.local",
    "prowlarr_api_key": "prowlarr-key",
    "bazarr_url": "http://bazarr.local",
    "bazarr_api_key": "bazarr-key",
}


def _default_routes() -> dict[str, object]:
    """A fully-healthy response for every endpoint the plugin might hit."""
    return {
        "http://sonarr.local/api/v3/queue/status": {"totalCount": 0},
        "http://sonarr.local/api/v3/wanted/missing?pageSize=1": {"totalRecords": 0},
        "http://sonarr.local/api/v3/health": [],
        "http://radarr.local/api/v3/queue/status": {"totalCount": 0},
        "http://radarr.local/api/v3/wanted/missing?pageSize=1": {"totalRecords": 0},
        "http://radarr.local/api/v3/health": [],
        "http://prowlarr.local/api/v1/health": [],
        "http://prowlarr.local/api/v1/indexerstatus": [],
        "http://bazarr.local/api/health": [],
        "http://bazarr.local/api/episodes/wanted": {"total": 0},
        "http://bazarr.local/api/movies/wanted": {"total": 0},
    }


def _urlopen_stub(routes: dict[str, object], captured: list[str] | None = None):
    def fake(req, timeout=None, context=None):
        url = req.full_url
        if captured is not None:
            captured.append(url)
        if url not in routes:
            raise AssertionError(f"unexpected URL requested: {url}")
        response = routes[url]
        if isinstance(response, BaseException):
            raise response
        payload = json.dumps(response).encode()
        cm = MagicMock()
        cm.__enter__ = lambda s, p=payload: MagicMock(read=lambda: p, status=200)
        cm.__exit__ = lambda s, *a: None
        return cm

    return fake


def _make_plugin(config: dict) -> ArrStackPlugin:
    plugin = ArrStackPlugin()
    plugin.configure(config)
    return plugin


class TestArrStackPlugin:
    @pytest.mark.asyncio
    async def test_not_configured_returns_disabled(self):
        plugin = _make_plugin({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_only_sonarr_configured_hits_only_sonarr(self):
        plugin = _make_plugin({"sonarr_url": "http://sonarr.local", "sonarr_api_key": "sonarr-key"})
        captured: list[str] = []
        with patch(
            "urllib.request.urlopen", side_effect=_urlopen_stub(_default_routes(), captured)
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert len(result.detail["services"]) == 1
        assert result.detail["services"][0]["key"] == "sonarr"
        assert all(url.startswith("http://sonarr.local") for url in captured)

    @pytest.mark.asyncio
    async def test_all_services_healthy(self):
        plugin = _make_plugin(_ALL_CONFIG)
        routes = _default_routes()
        routes["http://sonarr.local/api/v3/queue/status"] = {"totalCount": 2}
        routes["http://sonarr.local/api/v3/wanted/missing?pageSize=1"] = {"totalRecords": 4}
        routes["http://radarr.local/api/v3/queue/status"] = {"totalCount": 1}
        routes["http://radarr.local/api/v3/wanted/missing?pageSize=1"] = {"totalRecords": 3}
        routes["http://bazarr.local/api/episodes/wanted"] = {"total": 2}
        routes["http://bazarr.local/api/movies/wanted"] = {"total": 1}

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["queue_total"] == 3
        assert result.detail["missing_total"] == 4 + 3 + 3
        assert len(result.detail["services"]) == 4

    @pytest.mark.asyncio
    async def test_warning_health_sets_warn_status(self):
        plugin = _make_plugin(_ALL_CONFIG)
        routes = _default_routes()
        routes["http://radarr.local/api/v3/health"] = [
            {"source": "RemotePathMappingCheck", "type": "warning", "message": "bad mapping"}
        ]

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert len(result.detail["health"]) == 1
        assert result.detail["health"][0]["message"] == "bad mapping"

    @pytest.mark.asyncio
    async def test_error_health_sets_error_status(self):
        plugin = _make_plugin(_ALL_CONFIG)
        routes = _default_routes()
        routes["http://sonarr.local/api/v3/health"] = [
            {"source": "DatabaseCheck", "type": "error", "message": "db unreachable"}
        ]

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_one_unreachable_service_isolated_from_others(self):
        plugin = _make_plugin(_ALL_CONFIG)
        routes = _default_routes()
        routes["http://sonarr.local/api/v3/queue/status"] = Exception("Connection refused")
        routes["http://radarr.local/api/v3/queue/status"] = {"totalCount": 5}
        routes["http://radarr.local/api/v3/wanted/missing?pageSize=1"] = {"totalRecords": 7}

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()

        assert result.status == "error"
        services_by_key = {s["key"]: s for s in result.detail["services"]}
        assert services_by_key["sonarr"]["status"] == "error"
        assert services_by_key["radarr"]["status"] == "ok"
        assert services_by_key["radarr"]["queue"] == 5
        assert services_by_key["radarr"]["missing"] == 7

    @pytest.mark.asyncio
    async def test_partial_config_missing_api_key_reported_as_error_row(self):
        plugin = _make_plugin({"sonarr_url": "http://sonarr.local"})
        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(_default_routes())):
            result = await plugin.collect()

        assert result.status == "error"
        assert result.detail["services"][0]["status"] == "error"
        assert "api key" in result.detail["services"][0]["error"]

    @pytest.mark.asyncio
    async def test_queue_threshold_boundary(self):
        # queue_warn_threshold arrives as a string via BUOY_PLUGIN_ARR_STACK_QUEUE_WARN_THRESHOLD;
        # validate_plugin_config (loader.py) is what coerces it to int before configure().
        settings, errors = validate_plugin_config(
            "arr_stack",
            ArrStackPlugin.manifest.config_schema,
            {
                "sonarr_url": "http://sonarr.local",
                "sonarr_api_key": "sonarr-key",
                "queue_warn_threshold": "5",
            },
        )
        assert not errors
        assert settings["queue_warn_threshold"] == 5
        plugin = _make_plugin(settings)
        routes = _default_routes()
        routes["http://sonarr.local/api/v3/queue/status"] = {"totalCount": 5}

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()
        assert result.status == "ok"

        routes["http://sonarr.local/api/v3/queue/status"] = {"totalCount": 6}
        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()
        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_queue_status_404_falls_back_to_queue_endpoint(self):
        plugin = _make_plugin({"sonarr_url": "http://sonarr.local", "sonarr_api_key": "sonarr-key"})
        routes = _default_routes()
        routes["http://sonarr.local/api/v3/queue/status"] = urllib.error.HTTPError(
            "http://sonarr.local/api/v3/queue/status", 404, "Not Found", None, None
        )
        routes["http://sonarr.local/api/v3/queue?pageSize=1"] = {"totalRecords": 9}

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()

        assert result.detail["services"][0]["queue"] == 9

    @pytest.mark.asyncio
    async def test_bazarr_payload_shape_variants_are_summed(self):
        plugin = _make_plugin({"bazarr_url": "http://bazarr.local", "bazarr_api_key": "bazarr-key"})
        routes = _default_routes()
        routes["http://bazarr.local/api/episodes/wanted"] = {"total": 3}
        routes["http://bazarr.local/api/movies/wanted"] = {"data": [{"id": 1}, {"id": 2}]}

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()

        assert result.detail["services"][0]["missing"] == 5

    @pytest.mark.asyncio
    async def test_render_shape(self):
        plugin = _make_plugin(_ALL_CONFIG)
        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(_default_routes())):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "table"
        assert blocks[0]["columns"] == ["Service", "Queue", "Missing", "Health"]
        assert blocks[1]["type"] == "text"
        assert blocks[1]["status"] == "dim"

    @pytest.mark.asyncio
    async def test_render_marks_unreachable_row_and_lists_health(self):
        plugin = _make_plugin(_ALL_CONFIG)
        routes = _default_routes()
        routes["http://sonarr.local/api/v3/queue/status"] = Exception("Connection refused")
        routes["http://radarr.local/api/v3/health"] = [
            {"source": "Check", "type": "warning", "message": "bad config"}
        ]

        with patch("urllib.request.urlopen", side_effect=_urlopen_stub(routes)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        table_rows = {row[0]["value"]: row for row in blocks[0]["rows"]}
        assert table_rows["Sonarr"][1]["status"] == "error"
        assert blocks[1]["type"] == "list"
        assert any("bad config" in item["primary"] for item in blocks[1]["items"])

    @pytest.mark.asyncio
    async def test_verify_ssl_false_uses_unverified_context(self):
        plugin = _make_plugin(
            {
                "sonarr_url": "http://sonarr.local",
                "sonarr_api_key": "sonarr-key",
                "verify_ssl": False,
            }
        )
        seen_contexts = []

        def fake(req, timeout=None, context=None):
            seen_contexts.append(context)
            url = req.full_url
            payload = json.dumps(_default_routes()[url]).encode()
            cm = MagicMock()
            cm.__enter__ = lambda s, p=payload: MagicMock(read=lambda: p, status=200)
            cm.__exit__ = lambda s, *a: None
            return cm

        with patch("urllib.request.urlopen", side_effect=fake):
            await plugin.collect()

        assert all(ctx is not None and ctx.verify_mode.name == "CERT_NONE" for ctx in seen_contexts)

    def test_demo_data_renders(self):
        plugin = _make_plugin({})
        data = plugin.demo_data()
        assert data.status != "error"
        assert data.summary
        blocks = plugin.render(data)
        assert blocks
