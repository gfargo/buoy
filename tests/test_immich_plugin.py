"""Tests for the Immich photo library stats plugin."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestImmichPlugin:
    """Tests for the Immich photo library stats plugin."""

    def _make_plugin(self, url="http://immich:2283", api_key="test-api-key"):
        from buoy.plugins.builtin.immich import ImmichPlugin

        plugin = ImmichPlugin()
        plugin.configure({"url": url, "api_key": api_key})
        return plugin

    def _stats_response(self, photos=1234, videos=56, usage=10737418240):
        return json.dumps({"photos": photos, "videos": videos, "usage": usage}).encode()

    def _storage_response(self, pct=45.5, disk_use="49.4 GiB", disk_size="97.9 GiB"):
        return json.dumps(
            {
                "diskUsagePercentage": pct,
                "diskUse": disk_use,
                "diskSize": disk_size,
                "diskUseRaw": 53008195584,
                "diskSizeRaw": 105088745472,
            }
        ).encode()

    def _mock_urlopen(self, stats_data, storage_data):
        call_count = [0]

        def side_effect(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: stats_data)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: storage_data)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        return side_effect

    @pytest.mark.asyncio
    async def test_not_configured_returns_disabled(self):
        from buoy.plugins.builtin.immich import ImmichPlugin

        plugin = ImmichPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_not_configured_missing_api_key(self):
        from buoy.plugins.builtin.immich import ImmichPlugin

        plugin = ImmichPlugin()
        plugin.configure({"url": "http://immich:2283"})
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_healthy_collect(self):
        plugin = self._make_plugin()
        stats = self._stats_response(photos=1234, videos=56)
        storage = self._storage_response(pct=45.5)

        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(stats, storage)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "1,234 photos" in result.summary
        assert "56 videos" in result.summary
        assert "45.5%" in result.summary
        assert result.detail["photos"] == 1234
        assert result.detail["videos"] == 56
        assert result.detail["disk_pct"] == 45.5

    @pytest.mark.asyncio
    async def test_storage_warn_above_80_percent(self):
        plugin = self._make_plugin()
        stats = self._stats_response()
        storage = self._storage_response(pct=85.0)

        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(stats, storage)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["disk_pct"] == 85.0

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert result.summary == "Unreachable"
        assert "Connection refused" in result.detail["error"]

    @pytest.mark.asyncio
    async def test_storage_pct_derived_from_raw_when_missing(self):
        plugin = self._make_plugin()
        stats = self._stats_response()
        # Storage response without diskUsagePercentage — derive from raw values
        storage = json.dumps(
            {
                "diskUseRaw": 85000000000,
                "diskSizeRaw": 100000000000,
                "diskUse": "85.0 GiB",
                "diskSize": "100.0 GiB",
            }
        ).encode()

        with patch("urllib.request.urlopen", side_effect=self._mock_urlopen(stats, storage)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["disk_pct"] == 85.0

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_immich" in js
