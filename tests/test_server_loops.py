"""Tests that buoy.server's background loops log failures instead of swallowing them."""

import asyncio
import logging

import pytest

from buoy.config import BuoyConfig, NetworkConfig, NodeConfig, RefreshConfig
from buoy.server import BuoyAppState, _image_update_loop, _latency_loop, _stats_loop


def _make_config():
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.refresh = RefreshConfig(stats_interval=0, fleet_interval=0, image_updates_interval=0)
    return config


class _ExplodingMetricStore:
    def record(self, *args, **kwargs):
        raise RuntimeError("disk full")


class _ExplodingNetworkCollector:
    async def measure_latency(self):
        raise RuntimeError("peer unreachable")


class _ExplodingImageChecker:
    async def check_all(self):
        raise RuntimeError("registry unreachable")


class TestStatsLoopLogging:
    @pytest.mark.asyncio
    async def test_iteration_failure_is_logged(self, caplog):
        state = BuoyAppState(config=_make_config())
        state.metric_store = _ExplodingMetricStore()

        with caplog.at_level(logging.WARNING, logger="buoy.server"):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(_stats_loop(state), timeout=0.2)

        assert any("stats loop iteration failed" in r.message for r in caplog.records)


class TestLatencyLoopLogging:
    @pytest.mark.asyncio
    async def test_iteration_failure_is_logged(self, caplog):
        state = BuoyAppState(config=_make_config())
        state.collectors["network"] = _ExplodingNetworkCollector()
        state.metric_store = object()  # truthy, so measure_latency() is reached

        with caplog.at_level(logging.WARNING, logger="buoy.server"):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(_latency_loop(state), timeout=0.2)

        assert any("latency loop iteration failed" in r.message for r in caplog.records)


class TestImageUpdateLoopLogging:
    @pytest.mark.asyncio
    async def test_initial_check_failure_is_logged(self, caplog):
        state = BuoyAppState(config=_make_config())

        with caplog.at_level(logging.WARNING, logger="buoy.server"):
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    _image_update_loop(state, _ExplodingImageChecker()), timeout=0.2
                )

        assert any("image update check failed" in r.message for r in caplog.records)
