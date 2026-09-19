"""Tests for buoy.collectors.health.StaticHealthChecker."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.collectors.health import StaticHealthChecker
from buoy.config import BuoyConfig, NetworkConfig, StaticService


def _make_config(static, verify_ssl=True):
    config = BuoyConfig()
    config.network = NetworkConfig(verify_ssl=verify_ssl)
    config.services.static = static
    return config


def _make_mock_client(status_code=200):
    mock_response = MagicMock()
    mock_response.status_code = status_code

    mock_client = AsyncMock()
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=None)
    mock_client.get = AsyncMock(return_value=mock_response)
    return mock_client


class TestCheckAll:
    @pytest.mark.asyncio
    async def test_200_response_is_ok(self):
        static = [
            StaticService(name="NAS", url="https://nas.local", health_check="https://nas.local")
        ]
        checker = StaticHealthChecker(_make_config(static))

        with patch("httpx.AsyncClient", return_value=_make_mock_client(200)):
            result = await checker.check_all()

        assert result["NAS"]["status"] == "ok"
        assert "latency_ms" in result["NAS"]

    @pytest.mark.asyncio
    async def test_500_response_is_error(self):
        static = [
            StaticService(name="NAS", url="https://nas.local", health_check="https://nas.local")
        ]
        checker = StaticHealthChecker(_make_config(static))

        with patch("httpx.AsyncClient", return_value=_make_mock_client(500)):
            result = await checker.check_all()

        assert result["NAS"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_exception_does_not_propagate(self):
        static = [
            StaticService(name="NAS", url="https://nas.local", health_check="https://nas.local")
        ]
        checker = StaticHealthChecker(_make_config(static))

        with patch("httpx.AsyncClient", side_effect=RuntimeError("boom")):
            result = await checker.check_all()

        assert result["NAS"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_entries_without_health_check_are_skipped(self):
        static = [
            StaticService(name="NAS", url="https://nas.local", health_check="https://nas.local"),
            StaticService(name="Bookmark", url="https://example.com"),
        ]
        checker = StaticHealthChecker(_make_config(static))

        with patch("httpx.AsyncClient", return_value=_make_mock_client(200)):
            result = await checker.check_all()

        assert set(result.keys()) == {"NAS"}

    @pytest.mark.asyncio
    async def test_no_entries_returns_empty_without_client(self):
        checker = StaticHealthChecker(_make_config([]))

        with patch("httpx.AsyncClient") as mock_cls:
            result = await checker.check_all()

        mock_cls.assert_not_called()
        assert result == {}

    @pytest.mark.asyncio
    async def test_verify_inherits_network_default(self):
        static = [
            StaticService(name="NAS", url="https://nas.local", health_check="https://nas.local")
        ]
        checker = StaticHealthChecker(_make_config(static, verify_ssl=False))

        with patch("httpx.AsyncClient", return_value=_make_mock_client(200)) as mock_cls:
            await checker.check_all()

        assert mock_cls.call_args.kwargs["verify"] is False

    @pytest.mark.asyncio
    async def test_verify_per_entry_override_wins(self):
        static = [
            StaticService(
                name="NAS",
                url="https://nas.local",
                health_check="https://nas.local",
                verify_ssl=False,
            )
        ]
        checker = StaticHealthChecker(_make_config(static, verify_ssl=True))

        with patch("httpx.AsyncClient", return_value=_make_mock_client(200)) as mock_cls:
            await checker.check_all()

        assert mock_cls.call_args.kwargs["verify"] is False
