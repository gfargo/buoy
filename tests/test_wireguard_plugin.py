"""Tests for the WireGuard tunnel status plugin."""

import time
from unittest.mock import AsyncMock, patch

import pytest


class TestWireGuardPlugin:
    """Tests for the WireGuard tunnel status plugin."""

    def _make_plugin(self, **settings):
        from buoy.plugins.builtin.wireguard import WireGuardPlugin

        plugin = WireGuardPlugin()
        plugin.configure(settings)
        return plugin

    @pytest.mark.asyncio
    async def test_interface_not_found(self):
        plugin = self._make_plugin()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("nsenter")):
            result = await plugin.collect()
        assert result.status == "error"
        assert "wg0" in result.summary

    @pytest.mark.asyncio
    async def test_empty_output(self):
        plugin = self._make_plugin()
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_no_peers(self):
        """Interface line only, no peers — 0/0 peers up, status ok."""
        plugin = self._make_plugin()
        # Interface line: private_key, public_key, listen_port, fwmark
        iface_line = b"privkey123\tpubkey456\t51820\toff\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(iface_line, b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()
        assert result.status == "ok"
        assert result.summary == "0/0 peers up"

    @pytest.mark.asyncio
    async def test_all_peers_up(self):
        plugin = self._make_plugin()
        now = int(time.time())
        # Interface line + 2 fresh peers
        dump = (
            b"privkey\tpubkey\t51820\toff\n"
            + f"AAAAAAAAAAAA\t(none)\t1.2.3.4:51820\t10.0.0.2/32\t{now - 30}\t1024\t2048\t0\n".encode()
            + f"BBBBBBBBBBBB\t(none)\t5.6.7.8:51820\t10.0.0.3/32\t{now - 60}\t512\t1024\t0\n".encode()
        )
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(dump, b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()
        assert result.status == "ok"
        assert result.summary == "2/2 peers up"
        assert len(result.detail["peers"]) == 2
        assert result.detail["peers"][0]["rx"] == 1024
        assert result.detail["peers"][0]["tx"] == 2048

    @pytest.mark.asyncio
    async def test_stale_peer_warn(self):
        plugin = self._make_plugin(stale_seconds=180)
        now = int(time.time())
        dump = (
            b"privkey\tpubkey\t51820\toff\n"
            + f"AAAAAAAAAAAA\t(none)\t1.2.3.4:51820\t10.0.0.2/32\t{now - 30}\t0\t0\t0\n".encode()
            + b"BBBBBBBBBBBB\t(none)\t(none)\t10.0.0.3/32\t0\t0\t0\t0\n"  # never handshaked
        )
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(dump, b""))
        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()
        assert result.status == "warn"
        assert result.summary == "1/2 peers up"
        assert result.detail["peers"][1]["stale"] is True

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_wireguard" in js
