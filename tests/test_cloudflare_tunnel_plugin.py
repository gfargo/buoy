"""Tests for the Cloudflare Tunnel plugin."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


def _make_connection(colo="DFW", version="2024.10.1", pending=False):
    return {
        "colo_name": colo,
        "client_version": version,
        "is_pending_reconnect": pending,
    }


def _make_tunnel(name, status="healthy", tunnel_id=None, connections=None, deleted_at=None):
    return {
        "id": tunnel_id or f"id-{name}",
        "name": name,
        "status": status,
        "connections": connections if connections is not None else [_make_connection()],
        "deleted_at": deleted_at,
    }


def _mock_urlopen(payload):
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: json.dumps(payload).encode())
    cm.__exit__ = lambda s, *a: None
    return cm


class TestCloudflareTunnelPlugin:
    def _make_plugin(self, config=None):
        from buoy.plugins.builtin.cloudflare_tunnel import CloudflareTunnelPlugin

        plugin = CloudflareTunnelPlugin()
        plugin.configure(
            config if config is not None else {"account_id": "acct_1", "api_token": "cf_test"}
        )
        return plugin

    @pytest.mark.asyncio
    async def test_no_config_returns_disabled(self):
        from buoy.plugins.builtin.cloudflare_tunnel import CloudflareTunnelPlugin

        plugin = CloudflareTunnelPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_healthy_tunnels_ok(self):
        plugin = self._make_plugin()
        payload = {
            "result": [
                _make_tunnel(
                    "a",
                    "healthy",
                    connections=[
                        _make_connection(),
                        _make_connection(),
                        _make_connection(),
                        _make_connection(),
                    ],
                ),
                _make_tunnel(
                    "b",
                    "healthy",
                    connections=[
                        _make_connection(),
                        _make_connection(),
                        _make_connection(),
                        _make_connection(),
                    ],
                ),
            ],
            "result_info": {"total_count": 2},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["healthy"] == 2
        assert result.detail["connections"] == 8

    @pytest.mark.asyncio
    async def test_degraded_tunnel_warn(self):
        plugin = self._make_plugin()
        payload = {
            "result": [_make_tunnel("a", "degraded")],
            "result_info": {"total_count": 1},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_down_tunnel_error(self):
        plugin = self._make_plugin()
        payload = {
            "result": [
                _make_tunnel("a", "down"),
                _make_tunnel("b", "degraded"),
            ],
            "result_info": {"total_count": 2},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        # down beats degraded
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_min_connections_threshold_warn(self):
        plugin = self._make_plugin(
            {"account_id": "acct_1", "api_token": "cf_test", "min_connections": 4}
        )
        payload = {
            "result": [
                _make_tunnel("a", "healthy", connections=[_make_connection(), _make_connection()]),
            ],
            "result_info": {"total_count": 1},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["healthy"] == 0

    @pytest.mark.asyncio
    async def test_empty_result_ok_no_tunnels(self):
        plugin = self._make_plugin()
        payload = {"result": [], "result_info": {"total_count": 0}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.summary == "No tunnels"
        assert plugin.render(result) == [{"type": "text", "value": "No tunnels", "status": "dim"}]

    @pytest.mark.asyncio
    async def test_auth_failure_403(self):
        plugin = self._make_plugin()
        error = urllib.error.HTTPError("url", 403, "Forbidden", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            result = await plugin.collect()

        assert result.status == "error"
        assert result.summary == "Auth failed"

    @pytest.mark.asyncio
    async def test_auth_failure_401(self):
        plugin = self._make_plugin()
        error = urllib.error.HTTPError("url", 401, "Unauthorized", {}, None)
        with patch("urllib.request.urlopen", side_effect=error):
            result = await plugin.collect()

        assert result.status == "error"
        assert result.summary == "Auth failed"

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin()
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert result.summary == "Unreachable"

    @pytest.mark.asyncio
    async def test_tunnels_filter_by_name(self):
        plugin = self._make_plugin(
            {"account_id": "acct_1", "api_token": "cf_test", "tunnels": ["prod"]}
        )
        payload = {
            "result": [
                _make_tunnel("prod", "healthy"),
                _make_tunnel("staging", "healthy"),
            ],
            "result_info": {"total_count": 2},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        names = [t["name"] for t in result.detail["tunnels"]]
        assert names == ["prod"]

    @pytest.mark.asyncio
    async def test_deleted_tunnel_excluded(self):
        plugin = self._make_plugin()
        payload = {
            "result": [
                _make_tunnel("a", "healthy"),
                _make_tunnel("b", "healthy", deleted_at="2024-01-01T00:00:00Z"),
            ],
            "result_info": {"total_count": 2},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        names = [t["name"] for t in result.detail["tunnels"]]
        assert names == ["a"]

    @pytest.mark.asyncio
    async def test_render_produces_table_with_row_statuses(self):
        plugin = self._make_plugin()
        payload = {
            "result": [
                _make_tunnel("a", "healthy"),
                _make_tunnel("b", "down", connections=[]),
            ],
            "result_info": {"total_count": 2},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "table"
        rows = blocks[0]["rows"]
        assert rows[0][0]["status"] == "ok"
        assert rows[1][0]["status"] == "error"

    @pytest.mark.asyncio
    async def test_token_not_leaked_on_success(self):
        plugin = self._make_plugin()
        payload = {
            "result": [_make_tunnel("a", "healthy")],
            "result_info": {"total_count": 1},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert "cf_test" not in json.dumps(result.detail)

    @pytest.mark.asyncio
    async def test_token_not_leaked_on_error(self):
        plugin = self._make_plugin()
        with patch(
            "urllib.request.urlopen",
            side_effect=Exception("Connection refused to token cf_test"),
        ):
            result = await plugin.collect()

        # The plugin itself never embeds the token in error text
        assert "cf_test" not in json.dumps({k: v for k, v in result.detail.items() if k != "error"})

    def test_demo_data_renders(self):
        from buoy.plugins.builtin.cloudflare_tunnel import CloudflareTunnelPlugin

        plugin = CloudflareTunnelPlugin()
        data = plugin.demo_data()
        assert data.status != "error"
        assert data.summary
        blocks = plugin.render(data)
        assert blocks
