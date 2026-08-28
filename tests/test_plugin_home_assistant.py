"""Tests for the Home Assistant plugin."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestHomeAssistantPlugin:
    def _make_plugin(self, url="http://homeassistant:8123", token="test-token", **extra):
        from buoy.plugins.builtin.home_assistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin()
        plugin.configure({"url": url, "token": token, **extra})
        return plugin

    def _mock_urlopen(self, config_response, states_response):
        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: config_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: states_response)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        return mock_urlopen

    @pytest.mark.asyncio
    async def test_no_config_returns_disabled(self):
        from buoy.plugins.builtin.home_assistant import HomeAssistantPlugin

        plugin = HomeAssistantPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_healthy_all_ok(self):
        plugin = self._make_plugin()

        config_response = json.dumps({"version": "2024.8.0"}).encode()
        states_response = json.dumps(
            [
                {"entity_id": "light.kitchen", "state": "on"},
                {"entity_id": "sensor.temp", "state": "21.5"},
                {"entity_id": "automation.morning", "state": "on"},
            ]
        ).encode()

        with patch(
            "urllib.request.urlopen",
            side_effect=self._mock_urlopen(config_response, states_response),
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "3 entities" in result.summary
        assert "1 automations" in result.summary
        assert result.detail["unavailable_count"] == 0
        assert result.detail["update_count"] == 0
        assert result.detail["version"] == "2024.8.0"

    @pytest.mark.asyncio
    async def test_unavailable_and_update_warn(self):
        plugin = self._make_plugin()

        config_response = json.dumps({"version": "2024.8.0"}).encode()
        states_response = json.dumps(
            [
                {"entity_id": "light.kitchen", "state": "unavailable"},
                {"entity_id": "sensor.temp", "state": "unknown"},
                {"entity_id": "automation.morning", "state": "on"},
                {"entity_id": "update.core", "state": "on"},
                {"entity_id": "update.supervisor", "state": "off"},
            ]
        ).encode()

        with patch(
            "urllib.request.urlopen",
            side_effect=self._mock_urlopen(config_response, states_response),
        ):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["unavailable_count"] == 2
        assert "light.kitchen" in result.detail["unavailable"]
        assert "sensor.temp" in result.detail["unavailable"]
        assert result.detail["update_count"] == 1
        assert result.detail["updates"] == ["update.core"]
        assert "2 unavailable" in result.summary
        assert "1 update" in result.summary

    @pytest.mark.asyncio
    async def test_disabled_automation_flagged(self):
        plugin = self._make_plugin()

        config_response = json.dumps({"version": "2024.8.0"}).encode()
        states_response = json.dumps(
            [
                {"entity_id": "automation.morning", "state": "on"},
                {"entity_id": "automation.night", "state": "off"},
            ]
        ).encode()

        with patch(
            "urllib.request.urlopen",
            side_effect=self._mock_urlopen(config_response, states_response),
        ):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["disabled_automation_count"] == 1
        assert result.detail["disabled_automations"] == ["automation.night"]

    @pytest.mark.asyncio
    async def test_unreachable(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary
        assert "Connection refused" in result.detail["error"]

    @pytest.mark.asyncio
    async def test_render_produces_expected_blocks(self):
        plugin = self._make_plugin()

        config_response = json.dumps({"version": "2024.8.0"}).encode()
        states_response = json.dumps(
            [
                {"entity_id": "light.kitchen", "state": "unavailable"},
                {"entity_id": "automation.night", "state": "off"},
                {"entity_id": "update.core", "state": "on"},
            ]
        ).encode()

        with patch(
            "urllib.request.urlopen",
            side_effect=self._mock_urlopen(config_response, states_response),
        ):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "keyvalue"

        unavailable_block = next(
            b
            for b in blocks[1:]
            if b["type"] == "list" and b["items"][0]["secondary"] == "unavailable"
        )
        assert unavailable_block["items"][0]["primary"] == "light.kitchen"
        assert unavailable_block["items"][0]["status"] == "error"

        update_block = next(
            b
            for b in blocks[1:]
            if b["type"] == "list" and b["items"][0]["secondary"] == "update available"
        )
        assert update_block["items"][0]["primary"] == "update.core"
        assert update_block["items"][0]["status"] == "warn"

        disabled_block = next(
            b
            for b in blocks[1:]
            if b["type"] == "list" and b["items"][0]["secondary"] == "disabled"
        )
        assert disabled_block["items"][0]["primary"] == "automation.night"
        assert disabled_block["items"][0]["status"] == "dim"

    @pytest.mark.asyncio
    async def test_render_healthy_shows_text(self):
        plugin = self._make_plugin()

        config_response = json.dumps({"version": "2024.8.0"}).encode()
        states_response = json.dumps([{"entity_id": "light.kitchen", "state": "on"}]).encode()

        with patch(
            "urllib.request.urlopen",
            side_effect=self._mock_urlopen(config_response, states_response),
        ):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[-1] == {"type": "text", "value": "All healthy", "status": "dim"}

    @pytest.mark.asyncio
    async def test_verify_ssl_false_disables_cert_verification(self):
        plugin = self._make_plugin(verify_ssl=False)

        config_response = json.dumps({"version": "2024.8.0"}).encode()
        states_response = json.dumps([]).encode()

        captured_contexts = []

        def mock_urlopen(req, timeout=None, context=None, **kwargs):
            captured_contexts.append(context)
            cm = MagicMock()
            if len(captured_contexts) == 1:
                cm.__enter__ = lambda s: MagicMock(read=lambda: config_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: states_response)
            cm.__exit__ = lambda s, *a: None
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            await plugin.collect()

        assert all(ctx is not None for ctx in captured_contexts)
        import ssl

        assert all(ctx.verify_mode == ssl.CERT_NONE for ctx in captured_contexts)
