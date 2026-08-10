"""Tests for the Tailscale network status plugin."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.plugins.builtin.tailscale import TailscalePlugin


def _make_plugin():
    plugin = TailscalePlugin()
    plugin.configure({})
    return plugin


def _mock_proc(returncode, stdout_bytes):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout_bytes, b""))
    return proc


def _peer(hostname, online, cur_addr="", relay="", exit_node=False):
    return {
        "HostName": hostname,
        "Online": online,
        "CurAddr": cur_addr,
        "Relay": relay,
        "ExitNode": exit_node,
        "LastSeen": "2026-06-27T10:00:00Z" if not online else "0001-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
async def test_all_peers_online_returns_ok():
    plugin = _make_plugin()
    payload = json.dumps(
        {
            "BackendState": "Running",
            "Peer": {
                "k1": _peer("alpha", True, cur_addr="100.64.0.1:41641"),
                "k2": _peer("beta", True, cur_addr="100.64.0.2:41641"),
            },
        }
    ).encode()
    proc = _mock_proc(0, payload)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await plugin.collect()
    assert result.status == "ok"
    assert "2/2" in result.summary


@pytest.mark.asyncio
async def test_some_offline_returns_warn():
    plugin = _make_plugin()
    payload = json.dumps(
        {
            "BackendState": "Running",
            "Peer": {
                "k1": _peer("alpha", True, cur_addr="100.64.0.1:41641"),
                "k2": _peer("beta", False, relay="nyc"),
            },
        }
    ).encode()
    proc = _mock_proc(0, payload)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await plugin.collect()
    assert result.status == "warn"
    assert "1/2" in result.summary


@pytest.mark.asyncio
async def test_command_failure_returns_error():
    plugin = _make_plugin()
    proc = _mock_proc(1, b"")
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await plugin.collect()
    assert result.status == "error"


@pytest.mark.asyncio
async def test_binary_missing_returns_error():
    plugin = _make_plugin()
    with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("nsenter")):
        result = await plugin.collect()
    assert result.status == "error"


@pytest.mark.asyncio
async def test_direct_vs_relay_classification():
    plugin = _make_plugin()
    payload = json.dumps(
        {
            "BackendState": "Running",
            "Peer": {
                "k1": _peer("direct-peer", True, cur_addr="100.64.0.1:41641"),
                "k2": _peer("relay-peer", True, relay="nyc"),
            },
        }
    ).encode()
    proc = _mock_proc(0, payload)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await plugin.collect()
    peers_by_name = {p["name"]: p for p in result.detail["peers"]}
    assert peers_by_name["direct-peer"]["conn_type"] == "direct"
    assert peers_by_name["relay-peer"]["conn_type"] == "relay"


@pytest.mark.asyncio
async def test_render_produces_table_with_conn_and_online_status():
    plugin = _make_plugin()
    payload = json.dumps(
        {
            "BackendState": "Running",
            "Peer": {
                "k1": _peer("direct-peer", True, cur_addr="100.64.0.1:41641"),
                "k2": _peer("relay-peer", False, relay="nyc"),
            },
        }
    ).encode()
    proc = _mock_proc(0, payload)
    with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
        result = await plugin.collect()

    blocks = plugin.render(result)
    assert blocks[0]["type"] == "table"
    rows_by_peer = {r[0]["value"]: r for r in blocks[0]["rows"]}
    assert rows_by_peer["direct-peer"][1] == {
        "value": "direct",
        "status": "ok",
        "truncate": False,
        "mono": False,
    }
    assert rows_by_peer["relay-peer"][2]["status"] == "error"


def test_render_no_peers_shows_text():
    from buoy.plugins.protocol import PanelData

    plugin = _make_plugin()
    blocks = plugin.render(PanelData(status="ok", detail={"peers": []}))
    assert blocks == [{"type": "text", "value": "No peers", "status": "dim"}]


class TestFormatRelativeAge:
    def test_empty_returns_empty(self):
        from buoy.plugins.builtin.tailscale import _format_relative_age

        assert _format_relative_age("") == ""

    def test_unparseable_returns_empty(self):
        from buoy.plugins.builtin.tailscale import _format_relative_age

        assert _format_relative_age("not-a-timestamp") == ""

    def test_recent_seconds_are_just_now(self):
        from datetime import UTC, datetime, timedelta

        from buoy.plugins.builtin.tailscale import _format_relative_age

        ts = (datetime.now(UTC) - timedelta(seconds=10)).isoformat().replace("+00:00", "Z")
        assert _format_relative_age(ts) == "just now"

    def test_minutes_ago(self):
        from datetime import UTC, datetime, timedelta

        from buoy.plugins.builtin.tailscale import _format_relative_age

        ts = (datetime.now(UTC) - timedelta(minutes=3)).isoformat().replace("+00:00", "Z")
        assert _format_relative_age(ts) == "3m ago"

    def test_hours_ago(self):
        from datetime import UTC, datetime, timedelta

        from buoy.plugins.builtin.tailscale import _format_relative_age

        ts = (datetime.now(UTC) - timedelta(hours=2)).isoformat().replace("+00:00", "Z")
        assert _format_relative_age(ts) == "2h ago"

    def test_days_ago(self):
        from datetime import UTC, datetime, timedelta

        from buoy.plugins.builtin.tailscale import _format_relative_age

        ts = (datetime.now(UTC) - timedelta(days=5)).isoformat().replace("+00:00", "Z")
        assert _format_relative_age(ts) == "5d ago"

    def test_tolerates_fractional_seconds(self):
        from datetime import UTC, datetime, timedelta

        from buoy.plugins.builtin.tailscale import _format_relative_age

        seen = datetime.now(UTC) - timedelta(minutes=10)
        ts = seen.strftime("%Y-%m-%dT%H:%M:%S.") + f"{seen.microsecond:06d}123Z"
        assert _format_relative_age(ts) == "10m ago"
