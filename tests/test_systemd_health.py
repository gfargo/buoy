"""Tests for the systemd_health built-in plugin."""

from unittest.mock import AsyncMock, patch

import pytest


class TestSystemdHealthPlugin:
    """Tests for the systemd service health plugin."""

    def _make_plugin(self, units=None):
        from buoy.plugins.builtin.systemd_health import SystemdHealthPlugin

        plugin = SystemdHealthPlugin()
        plugin.configure({"units": units} if units is not None else {})
        return plugin

    @staticmethod
    def _make_proc(output: bytes):
        mock = AsyncMock()
        mock.communicate = AsyncMock(return_value=(output, b""))
        return mock

    @pytest.mark.asyncio
    async def test_not_configured(self):
        plugin = self._make_plugin()
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_all_active(self):
        plugin = self._make_plugin(["tailscaled", "docker", "caddy"])

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[
                self._make_proc(b"active\n"),
                self._make_proc(b"active\n"),
                self._make_proc(b"active\n"),
            ],
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "3/3 active" in result.summary
        assert len(result.detail["units"]) == 3
        assert all(row["state"] == "active" for row in result.detail["units"])

    @pytest.mark.asyncio
    async def test_one_inactive(self):
        plugin = self._make_plugin(["tailscaled", "caddy"])

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[
                self._make_proc(b"active\n"),
                self._make_proc(b"inactive\n"),
            ],
        ):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "1/2 active" in result.summary

    @pytest.mark.asyncio
    async def test_one_failed(self):
        plugin = self._make_plugin(["tailscaled", "caddy"])

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[
                self._make_proc(b"active\n"),
                self._make_proc(b"failed\n"),
            ],
        ):
            result = await plugin.collect()

        assert result.status == "error"
        assert "caddy" in result.summary
        assert "failed" in result.summary

    @pytest.mark.asyncio
    async def test_nsenter_missing(self):
        plugin = self._make_plugin(["tailscaled"])

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("nsenter")):
            result = await plugin.collect()

        assert result.status in ("error", "disabled", "warn")
        assert result.detail["units"][0]["state"] == "unknown"

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert "render_systemd_health" in js
        assert "<table" in js
