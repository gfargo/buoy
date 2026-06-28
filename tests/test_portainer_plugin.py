"""Tests for the Portainer remote container stats plugin."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestPortainerPlugin:
    """Tests for the Portainer remote container stats plugin."""

    def _make_plugin(self, url="http://portainer:9000", api_key="test-key", endpoint_id=1):
        from buoy.plugins.builtin.portainer import PortainerPlugin

        plugin = PortainerPlugin()
        plugin.configure({"url": url, "api_key": api_key, "endpoint_id": endpoint_id})
        return plugin

    @pytest.mark.asyncio
    async def test_not_configured_returns_disabled(self):
        from buoy.plugins.builtin.portainer import PortainerPlugin

        plugin = PortainerPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_all_running_returns_ok(self):
        plugin = self._make_plugin()

        response = json.dumps(
            [
                {
                    "Names": ["/nginx"],
                    "Image": "nginx:latest",
                    "State": "running",
                    "Status": "Up 2 hours (healthy)",
                },
                {
                    "Names": ["/redis"],
                    "Image": "redis:7",
                    "State": "running",
                    "Status": "Up 1 hour",
                },
            ]
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "2/2 running" in result.summary
        assert result.detail["running"] == 2
        assert result.detail["total"] == 2

    @pytest.mark.asyncio
    async def test_unhealthy_returns_warn(self):
        plugin = self._make_plugin()

        response = json.dumps(
            [
                {
                    "Names": ["/nginx"],
                    "Image": "nginx:latest",
                    "State": "running",
                    "Status": "Up 2 hours (healthy)",
                },
                {
                    "Names": ["/broken"],
                    "Image": "myapp:1.0",
                    "State": "running",
                    "Status": "Up 1 hour (unhealthy)",
                },
            ]
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_exited_container_returns_warn(self):
        plugin = self._make_plugin()

        response = json.dumps(
            [
                {
                    "Names": ["/nginx"],
                    "Image": "nginx:latest",
                    "State": "running",
                    "Status": "Up 2 hours",
                },
                {
                    "Names": ["/stopped"],
                    "Image": "myapp:1.0",
                    "State": "exited",
                    "Status": "Exited (1) 5 minutes ago",
                },
            ]
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "1/2 running" in result.summary

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary
