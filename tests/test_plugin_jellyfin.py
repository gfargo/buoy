"""Tests for the Jellyfin media server status plugin."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestJellyfinPlugin:
    def _make_plugin(self, url="http://jellyfin:8096", api_key="test-key"):
        from buoy.plugins.builtin.jellyfin import JellyfinPlugin

        plugin = JellyfinPlugin()
        plugin.configure({"url": url, "api_key": api_key})
        return plugin

    @pytest.mark.asyncio
    async def test_no_config_returns_disabled(self):
        from buoy.plugins.builtin.jellyfin import JellyfinPlugin

        plugin = JellyfinPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_active_streams_with_transcoding(self):
        plugin = self._make_plugin()

        sessions_response = json.dumps(
            [
                {
                    "NowPlayingItem": {"Name": "Movie A"},
                    "UserName": "alice",
                    "PlayState": {"PlayMethod": "DirectPlay"},
                },
                {
                    "NowPlayingItem": {"Name": "Movie B"},
                    "UserName": "bob",
                    "PlayState": {"PlayMethod": "Transcode"},
                    "TranscodingInfo": {"Container": "ts"},
                },
                # Idle session — should not count
                {"UserName": "carol"},
            ]
        ).encode()

        libraries_response = json.dumps(
            {
                "Items": [
                    {"Name": "Movies", "CollectionType": "movies"},
                    {"Name": "TV Shows", "CollectionType": "tvshows"},
                ]
            }
        ).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: sessions_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: libraries_response)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "2 streams" in result.summary
        assert "1 transcoding" in result.summary
        assert result.detail["transcoding_count"] == 1
        assert len(result.detail["streams"]) == 2
        assert len(result.detail["libraries"]) == 2

    @pytest.mark.asyncio
    async def test_idle_no_streams(self):
        plugin = self._make_plugin()

        sessions_response = json.dumps([{"UserName": "carol"}]).encode()
        libraries_response = json.dumps({"Items": []}).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: sessions_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: libraries_response)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.summary == "Idle"
        assert result.detail["transcoding_count"] == 0

    @pytest.mark.asyncio
    async def test_unreachable(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    @pytest.mark.asyncio
    async def test_render_produces_stream_list_and_library_badges(self):
        plugin = self._make_plugin()

        sessions_response = json.dumps(
            [
                {
                    "NowPlayingItem": {"Name": "Movie B"},
                    "UserName": "bob",
                    "PlayState": {"PlayMethod": "Transcode"},
                    "TranscodingInfo": {"Container": "ts"},
                }
            ]
        ).encode()
        libraries_response = json.dumps(
            {"Items": [{"Name": "Movies", "CollectionType": "movies"}]}
        ).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: sessions_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: libraries_response)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "list"
        assert "Movie B" in blocks[0]["items"][0]["primary"]
        assert blocks[0]["items"][0]["status"] == "warn"
        assert blocks[1]["type"] == "badges"
        assert blocks[1]["items"][0]["label"] == "Movies"

    @pytest.mark.asyncio
    async def test_render_idle_shows_text(self):
        plugin = self._make_plugin()
        sessions_response = json.dumps([{"UserName": "carol"}]).encode()
        libraries_response = json.dumps({"Items": []}).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: sessions_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: libraries_response)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks == [{"type": "text", "value": "No active streams", "status": "dim"}]
