"""Tests for static-service health checker startup wiring in buoy.server."""

import asyncio
from unittest.mock import patch

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig, StaticService
from buoy.server import BuoyAppState, on_shutdown, on_startup


def _make_config(*, static=None, demo_mode=False):
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.features = FeaturesConfig(demo_mode=demo_mode, websocket=False, history=False)
    config.services.static = static or []
    return config


class TestHealthCheckStartupGating:
    @pytest.mark.asyncio
    async def test_loop_not_started_without_health_check_entries(self):
        """A static entry with no health_check must not start the loop at all."""
        config = _make_config(static=[StaticService(name="Bookmark", url="https://example.com")])
        state = BuoyAppState(config=config)
        try:
            await on_startup(state)
            await asyncio.sleep(0.05)
            assert state.static_health == {}
        finally:
            await on_shutdown(state)

    @pytest.mark.asyncio
    async def test_demo_mode_selects_demo_checker_and_makes_no_outbound_calls(self):
        """README guarantees --demo never makes a real outbound call."""
        config = _make_config(
            static=[
                StaticService(name="NAS", url="https://nas.local", health_check="https://nas.local")
            ],
            demo_mode=True,
        )
        state = BuoyAppState(config=config)
        try:
            with patch("httpx.AsyncClient") as mock_cls:
                await on_startup(state)
                await asyncio.sleep(0.05)
            mock_cls.assert_not_called()
            assert state.static_health["NAS"]["status"] == "ok"
        finally:
            await on_shutdown(state)

    @pytest.mark.asyncio
    async def test_shutdown_clears_static_health(self):
        config = _make_config(
            static=[
                StaticService(name="NAS", url="https://nas.local", health_check="https://nas.local")
            ],
            demo_mode=True,
        )
        state = BuoyAppState(config=config)
        await on_startup(state)
        await asyncio.sleep(0.05)
        assert state.static_health

        await on_shutdown(state)
        assert state.static_health == {}
