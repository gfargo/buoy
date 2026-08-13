"""Tests for the shared timeout-safe subprocess communicate() helper."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.subprocess_utils import communicate


@pytest.mark.asyncio
async def test_communicate_returns_output_on_success():
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(b"out", b""))
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    result = await communicate(proc, timeout=5)

    assert result == (b"out", b"")
    proc.kill.assert_not_called()
    proc.wait.assert_not_called()


@pytest.mark.asyncio
async def test_communicate_kills_and_reaps_on_timeout():
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock()

    with patch("buoy.subprocess_utils.asyncio.wait_for", side_effect=TimeoutError()):
        with pytest.raises(TimeoutError):
            await communicate(proc, timeout=5)

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_communicate_swallows_process_lookup_error_from_kill():
    proc = MagicMock()
    proc.kill = MagicMock(side_effect=ProcessLookupError())
    proc.wait = AsyncMock()

    with patch("buoy.subprocess_utils.asyncio.wait_for", side_effect=TimeoutError()):
        with pytest.raises(TimeoutError):
            await communicate(proc, timeout=5)

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()


@pytest.mark.asyncio
async def test_communicate_swallows_process_lookup_error_from_wait():
    proc = MagicMock()
    proc.kill = MagicMock()
    proc.wait = AsyncMock(side_effect=ProcessLookupError())

    with patch("buoy.subprocess_utils.asyncio.wait_for", side_effect=TimeoutError()):
        with pytest.raises(TimeoutError):
            await communicate(proc, timeout=5)

    proc.kill.assert_called_once()
    proc.wait.assert_awaited_once()
