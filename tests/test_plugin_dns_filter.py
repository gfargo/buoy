"""Tests for the DNS Filter plugin (Pi-hole / AdGuard Home)."""

import json
from unittest.mock import MagicMock, patch

import pytest


def _mock_urlopen(response_data: dict) -> MagicMock:
    payload = json.dumps(response_data).encode()
    mock_cm = MagicMock()
    mock_cm.__enter__ = lambda s: MagicMock(read=lambda: payload)
    mock_cm.__exit__ = lambda s, *a: None
    return mock_cm


class TestDnsFilterPlugin:
    def _make_plugin(self, config: dict):
        from buoy.plugins.builtin.dns_filter import DnsFilterPlugin

        plugin = DnsFilterPlugin()
        plugin.configure(config)
        return plugin

    @pytest.mark.asyncio
    async def test_not_configured_returns_disabled(self):
        plugin = self._make_plugin({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_pihole_ok(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole"})
        data = {
            "dns_queries_today": 10000,
            "ads_blocked_today": 1200,
            "ads_percentage_today": 12.0,
            "status": "enabled",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "ok"
        assert "10,000" in result.summary
        assert "12.0%" in result.summary
        assert result.detail["queries"] == 10000
        assert result.detail["blocked"] == 1200

    @pytest.mark.asyncio
    async def test_pihole_warn_when_blocked_over_threshold(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole"})
        data = {
            "dns_queries_today": 1000,
            "ads_blocked_today": 300,
            "ads_percentage_today": 30.0,
            "status": "enabled",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "warn"
        assert result.detail["pct"] == 30.0

    @pytest.mark.asyncio
    async def test_pihole_disabled_returns_error(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole"})
        data = {
            "dns_queries_today": 0,
            "ads_blocked_today": 0,
            "ads_percentage_today": 0.0,
            "status": "disabled",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "error"
        assert "disabled" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_adguard_ok(self):
        plugin = self._make_plugin({
            "type": "adguard",
            "url": "http://adguard:3000",
            "username": "admin",
            "password": "secret",
        })
        data = {
            "num_dns_queries": 5000,
            "num_blocked_filtering": 500,
            "top_blocked_domains": {"ads.example.com": 120, "tracker.io": 80},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "ok"
        assert result.detail["queries"] == 5000
        assert result.detail["pct"] == 10.0
        assert len(result.detail["top_blocked"]) == 2

    @pytest.mark.asyncio
    async def test_adguard_zero_queries_no_division_error(self):
        plugin = self._make_plugin({"type": "adguard", "url": "http://adguard:3000"})
        data = {"num_dns_queries": 0, "num_blocked_filtering": 0, "top_blocked_domains": {}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "ok"
        assert result.detail["pct"] == 0.0

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole"})
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()
        assert result.status == "error"
        assert "Unreachable" in result.summary

    def test_has_frontend_js(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole"})
        js = plugin.frontend_js()
        assert js is not None
        assert "render_dns_filter" in js
