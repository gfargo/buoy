"""Tests for the Plex media server status plugin."""

from __future__ import annotations

import json
import ssl
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


def _cm(body: bytes):
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: body)
    cm.__exit__ = lambda s, *a: None
    return cm


def _fake_urlopen(routes: dict[str, bytes | Exception]):
    """Dispatch on req.full_url — Plex makes 2+N calls per cycle, so keying on
    call order (as the Jellyfin tests do) is too fragile here."""

    def _urlopen(req, timeout=None, context=None, **kwargs):
        full_url = req.full_url
        # Longest prefix first: "/library/sections" is itself a prefix of
        # "/library/sections/1/all", so shortest-first matching would hijack
        # per-section count requests.
        for prefix in sorted(routes, key=len, reverse=True):
            if full_url.startswith(prefix):
                outcome = routes[prefix]
                if isinstance(outcome, Exception):
                    raise outcome
                return _cm(outcome)
        raise AssertionError(f"unexpected request: {full_url}")

    return _urlopen


def _session(
    title="Movie A",
    grandparent=None,
    user="alice",
    player_product="Chrome",
    state="playing",
    video_decision=None,
    audio_decision=None,
    duration=1000,
    offset=250,
    bandwidth=None,
):
    payload = {
        "title": title,
        "User": {"title": user},
        "Player": {"product": player_product, "state": state},
        "duration": duration,
        "viewOffset": offset,
    }
    if grandparent:
        payload["grandparentTitle"] = grandparent
    if video_decision or audio_decision:
        ts = {}
        if video_decision:
            ts["videoDecision"] = video_decision
        if audio_decision:
            ts["audioDecision"] = audio_decision
        payload["TranscodeSession"] = ts
    if bandwidth is not None:
        payload["Session"] = {"bandwidth": bandwidth}
    return payload


def _sessions_body(sessions):
    return json.dumps({"MediaContainer": {"Metadata": sessions}}).encode()


def _identity_body(version="1.40.1.1234"):
    return json.dumps({"MediaContainer": {"version": version}}).encode()


def _sections_body(sections):
    return json.dumps({"MediaContainer": {"Directory": sections}}).encode()


def _count_body(total):
    return json.dumps({"MediaContainer": {"totalSize": total}}).encode()


class TestPlexPlugin:
    def _make_plugin(self, url="http://plex:32400", token="test-token", **extra):
        from buoy.plugins.builtin.plex import PlexPlugin

        plugin = PlexPlugin()
        plugin.configure({"url": url, "token": token, **extra})
        return plugin

    @pytest.mark.asyncio
    async def test_no_config_returns_disabled(self):
        from buoy.plugins.builtin.plex import PlexPlugin

        plugin = PlexPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_transcode_vs_direct_stream_distinction(self):
        """Regression: a TranscodeSession with videoDecision=copy (remux) must
        NOT count as transcoding — only an actual transcode/transcode decision
        should. Presence-only detection (the Jellyfin approach) over-counts."""
        plugin = self._make_plugin()

        sessions = [
            _session(title="Real Transcode", video_decision="transcode", state="playing"),
            _session(title="Direct Stream", video_decision="copy", state="playing"),
        ]
        routes = {
            "http://plex:32400/status/sessions": _sessions_body(sessions),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body([]),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "2 streams" in result.summary
        assert "1 transcoding" in result.summary
        assert result.detail["transcoding_count"] == 1

    @pytest.mark.asyncio
    async def test_paused_and_idle_sessions(self):
        plugin = self._make_plugin()

        sessions = [
            _session(title="Paused Movie", state="paused"),
        ]
        routes = {
            "http://plex:32400/status/sessions": _sessions_body(sessions),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body([]),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        assert result.detail["paused_count"] == 1
        assert "Idle" in result.summary
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_no_sessions_is_ok_idle(self):
        plugin = self._make_plugin()

        routes = {
            "http://plex:32400/status/sessions": _sessions_body([]),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body([]),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.summary == "Idle"

    @pytest.mark.asyncio
    async def test_library_counts_parsed_and_tolerate_per_section_failure(self):
        plugin = self._make_plugin()

        sections = [
            {"key": "1", "title": "Movies", "type": "movie"},
            {"key": "2", "title": "TV", "type": "show"},
        ]
        routes = {
            "http://plex:32400/status/sessions": _sessions_body([]),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body(sections),
            "http://plex:32400/library/sections/1/all": _count_body(842),
            "http://plex:32400/library/sections/2/all": Exception("boom"),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        libs = {lib["title"]: lib["count"] for lib in result.detail["libraries"]}
        assert libs["Movies"] == 842
        assert libs["TV"] is None
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_library_counts_disabled_skips_section_requests(self):
        plugin = self._make_plugin(library_counts=False)

        sections = [{"key": "1", "title": "Movies", "type": "movie"}]

        def _urlopen(req, timeout=None, context=None, **kwargs):
            full_url = req.full_url
            if full_url.startswith("http://plex:32400/library/sections/"):
                raise AssertionError("section count request should not have been made")
            if full_url == "http://plex:32400/status/sessions":
                return _cm(_sessions_body([]))
            if full_url == "http://plex:32400/identity":
                return _cm(_identity_body())
            if full_url == "http://plex:32400/library/sections":
                return _cm(_sections_body(sections))
            raise AssertionError(f"unexpected request: {full_url}")

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            result = await plugin.collect()

        assert result.detail["libraries"] == [{"title": "Movies", "type": "movie", "count": None}]

    @pytest.mark.asyncio
    async def test_unreachable(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    @pytest.mark.asyncio
    async def test_non_json_body_is_error(self):
        plugin = self._make_plugin()

        routes = {"http://plex:32400/status/sessions": b"<xml>not json</xml>"}
        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_invalid_token_reported_distinctly(self):
        plugin = self._make_plugin()

        err = urllib.error.HTTPError(
            "http://plex:32400/status/sessions", 401, "Unauthorized", None, None
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Invalid token" in result.summary

    @pytest.mark.asyncio
    async def test_generic_http_error_is_unreachable(self):
        plugin = self._make_plugin()

        err = urllib.error.HTTPError(
            "http://plex:32400/status/sessions", 500, "Server Error", None, None
        )
        with patch("urllib.request.urlopen", side_effect=err):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    @pytest.mark.asyncio
    async def test_identity_failure_is_tolerated(self):
        plugin = self._make_plugin()

        def _urlopen(req, timeout=None, context=None, **kwargs):
            full_url = req.full_url
            if full_url == "http://plex:32400/status/sessions":
                return _cm(_sessions_body([]))
            if full_url == "http://plex:32400/identity":
                raise Exception("identity endpoint down")
            if full_url == "http://plex:32400/library/sections":
                return _cm(_sections_body([]))
            raise AssertionError(f"unexpected request: {full_url}")

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["server_version"] is None

    @pytest.mark.asyncio
    async def test_render_detail_empty_sessions_shows_text(self):
        plugin = self._make_plugin()

        routes = {
            "http://plex:32400/status/sessions": _sessions_body([]),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body([]),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        blocks = plugin.render_detail(result)
        assert blocks[0] == {"type": "text", "value": "No active streams", "status": "dim"}

    @pytest.mark.asyncio
    async def test_verify_ssl_false_disables_cert_verification(self):
        plugin = self._make_plugin(verify_ssl=False)

        captured_ctx = []

        def _urlopen(req, timeout=None, context=None, **kwargs):
            captured_ctx.append(context)
            full_url = req.full_url
            if full_url == "http://plex:32400/status/sessions":
                return _cm(_sessions_body([]))
            if full_url == "http://plex:32400/identity":
                return _cm(_identity_body())
            if full_url == "http://plex:32400/library/sections":
                return _cm(_sections_body([]))
            raise AssertionError(f"unexpected request: {full_url}")

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            await plugin.collect()

        assert captured_ctx
        assert all(ctx is not None and ctx.verify_mode == ssl.CERT_NONE for ctx in captured_ctx)

    @pytest.mark.asyncio
    async def test_token_never_sent_as_query_param(self):
        plugin = self._make_plugin(token="super-secret-token")

        seen_urls = []

        def _urlopen(req, timeout=None, context=None, **kwargs):
            seen_urls.append(req.full_url)
            full_url = req.full_url
            if full_url == "http://plex:32400/status/sessions":
                return _cm(_sessions_body([_session()]))
            if full_url == "http://plex:32400/identity":
                return _cm(_identity_body())
            if full_url == "http://plex:32400/library/sections":
                return _cm(_sections_body([]))
            raise AssertionError(f"unexpected request: {full_url}")

        with patch("urllib.request.urlopen", side_effect=_urlopen):
            await plugin.collect()

        assert seen_urls
        assert all("super-secret-token" not in url for url in seen_urls)
        assert plugin.manifest.config_schema["token"]["secret"] is True

    @pytest.mark.asyncio
    async def test_render_produces_expected_block_shapes(self):
        plugin = self._make_plugin()

        sessions = [
            _session(
                title="Ozymandias",
                grandparent="Breaking Bad",
                video_decision="transcode",
                state="playing",
            ),
        ]
        sections = [{"key": "1", "title": "Movies", "type": "movie"}]
        routes = {
            "http://plex:32400/status/sessions": _sessions_body(sessions),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body(sections),
            "http://plex:32400/library/sections/1/all": _count_body(842),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "keyvalue"
        list_block = blocks[1]
        assert list_block["type"] == "list"
        assert "Breaking Bad - Ozymandias" in list_block["items"][0]["primary"]
        assert list_block["items"][0]["status"] == "warn"
        badges_block = blocks[2]
        assert badges_block["type"] == "badges"
        assert badges_block["items"][0]["label"] == "Movies 842"

        detail_blocks = plugin.render_detail(result)
        table_block = detail_blocks[0]
        assert table_block["type"] == "table"
        assert table_block["columns"] == [
            "Title",
            "User",
            "Player",
            "State",
            "Decision",
            "Progress",
        ]

    @pytest.mark.asyncio
    async def test_streams_count_survives_max_rows_truncation(self):
        """Regression: with more sessions than max_rows, the active count must
        reflect the full session list, not just the rows kept after truncation.
        Ten paused sessions sort first here, so a naive recount over the
        truncated rows(len == max_rows) would see zero active sessions."""
        plugin = self._make_plugin(max_rows=10)

        sessions = [_session(title=f"Paused {i}", state="paused") for i in range(10)] + [
            _session(title=f"Playing {i}", state="playing") for i in range(5)
        ]
        routes = {
            "http://plex:32400/status/sessions": _sessions_body(sessions),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body([]),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        assert result.detail["active_count"] == 5
        assert "5 streams" in result.summary
        assert len(result.detail["sessions"]) == 10  # truncated to max_rows

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "keyvalue"
        streams_row = next(row for row in blocks[0]["rows"] if row["label"] == "Streams")
        assert streams_row["value"] == "5"

    @pytest.mark.asyncio
    async def test_render_idle_shows_text(self):
        plugin = self._make_plugin()

        routes = {
            "http://plex:32400/status/sessions": _sessions_body([]),
            "http://plex:32400/identity": _identity_body(),
            "http://plex:32400/library/sections": _sections_body([]),
        }

        with patch("urllib.request.urlopen", side_effect=_fake_urlopen(routes)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[-1] == {"type": "text", "value": "No active streams", "status": "dim"}
