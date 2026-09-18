"""Tests for the Download Clients plugin (qBittorrent/Transmission/SABnzbd/NZBGet)."""

import json
import urllib.error
import urllib.request
from email.message import Message
from unittest.mock import MagicMock, patch

import pytest

from buoy.plugins.builtin.download_clients import (
    DownloadClientsPlugin,
    _fmt_eta,
    _fmt_rate,
    _nzbget_counts,
    _qbit_counts,
    _sabnzbd_counts,
    _sanitize_error,
    _transmission_counts,
    _worst,
)
from buoy.plugins.protocol import PanelData


def _make_plugin(**settings):
    plugin = DownloadClientsPlugin()
    plugin.configure(settings)
    return plugin


def _cm(body: bytes):
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: body)
    cm.__exit__ = lambda s, *a: None
    return cm


def _http_error_409(session_id: str) -> urllib.error.HTTPError:
    hdrs = Message()
    hdrs["X-Transmission-Session-Id"] = session_id
    return urllib.error.HTTPError(
        url="http://t:9091/transmission/rpc", code=409, msg="Conflict", hdrs=hdrs, fp=None
    )


# =============================================================================
# Pure helpers
# =============================================================================


class TestWorst:
    def test_error_wins(self):
        assert _worst(["ok", "warn", "error"]) == "error"

    def test_warn_wins_over_ok(self):
        assert _worst(["ok", "warn"]) == "warn"

    def test_all_ok(self):
        assert _worst(["ok", "ok"]) == "ok"


class TestSanitizeError:
    def test_auth_failure(self):
        assert _sanitize_error("authentication failed") == "authentication failed"
        assert _sanitize_error("HTTP Error 401: Unauthorized") == "authentication failed"

    def test_timeout(self):
        assert _sanitize_error("timed out") == "timed out"

    def test_unreachable(self):
        assert _sanitize_error("Connection refused") == "unreachable"

    def test_default_never_echoes_raw_message(self):
        raw = "could not connect to http://user:hunter2@host/jsonrpc"
        result = _sanitize_error(raw)
        assert "hunter2" not in result
        assert "user" not in result


class TestFmtHelpers:
    def test_fmt_rate_scales(self):
        assert _fmt_rate(500) == "500 B/s"
        assert _fmt_rate(2048) == "2.0 KB/s"
        assert _fmt_rate(5 * 1024**2) == "5.0 MB/s"

    def test_fmt_eta_unknown_sentinels(self):
        assert _fmt_eta(None) == "—"
        assert _fmt_eta(-1) == "—"
        assert _fmt_eta(8_640_000) == "—"

    def test_fmt_eta_formats_seconds(self):
        assert _fmt_eta(30) == "30s"
        assert _fmt_eta(90) == "1m"
        assert _fmt_eta(3660) == "1h1m"


class TestQbitCounts:
    def test_active_and_queued(self):
        torrents = {
            "a": {"name": "A", "state": "downloading", "progress": 0.5, "dlspeed": 100, "eta": 10},
            "b": {"name": "B", "state": "queuedDL", "progress": 0.0, "dlspeed": 0, "eta": 8640000},
            "c": {"name": "C", "state": "pausedDL", "progress": 0.2, "dlspeed": 0, "eta": 8640000},
        }
        active, queued, items = _qbit_counts(torrents)
        assert active == 1
        assert queued == 1
        assert len(items) == 3


class TestTransmissionCounts:
    def test_active_and_queued(self):
        torrents = [
            {"name": "A", "status": 4, "percentDone": 0.5, "rateDownload": 100, "eta": 10},
            {"name": "B", "status": 3, "percentDone": 0.0, "rateDownload": 0, "eta": -1},
            {"name": "C", "status": 0, "percentDone": 1.0, "rateDownload": 0, "eta": -1},
        ]
        active, queued, items = _transmission_counts(torrents)
        assert active == 1
        assert queued == 1
        assert len(items) == 3


class TestSabnzbdCounts:
    def test_downloading_vs_queued(self):
        slots = [
            {"filename": "a", "percentage": "50", "status": "Downloading"},
            {"filename": "b", "percentage": "0", "status": "Queued"},
            {"filename": "c", "percentage": "0", "status": "Paused"},
        ]
        active, queued, items = _sabnzbd_counts(slots)
        assert active == 1
        assert queued == 2


class TestNzbgetCounts:
    def test_downloading_vs_queued(self):
        groups = [
            {"NZBFilename": "a", "FileSizeMB": 100, "RemainingSizeMB": 50, "Status": "DOWNLOADING"},
            {"NZBFilename": "b", "FileSizeMB": 100, "RemainingSizeMB": 100, "Status": "QUEUED"},
        ]
        active, queued, items = _nzbget_counts(groups)
        assert active == 1
        assert queued == 1
        assert items[0]["progress"] == 50.0


# =============================================================================
# collect() — no config
# =============================================================================


class TestNoClients:
    @pytest.mark.asyncio
    async def test_no_clients_returns_disabled(self):
        plugin = _make_plugin()
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_empty_clients_list_returns_disabled(self):
        plugin = _make_plugin(clients=[])
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_non_dict_client_entries_are_ignored(self):
        plugin = _make_plugin(clients=["not-a-dict"])
        result = await plugin.collect()
        assert result.status == "disabled"


# =============================================================================
# qBittorrent
# =============================================================================


class TestQbittorrent:
    def _maindata(self):
        return json.dumps(
            {
                "server_state": {
                    "dl_info_speed": 1000,
                    "up_info_speed": 200,
                    "free_space_on_disk": 100 * 1024**3,
                    "global_ratio": 1.5,
                },
                "torrents": {
                    "h1": {
                        "name": "A",
                        "state": "downloading",
                        "progress": 0.5,
                        "dlspeed": 500,
                        "eta": 100,
                    },
                    "h2": {
                        "name": "B",
                        "state": "queuedDL",
                        "progress": 0.0,
                        "dlspeed": 0,
                        "eta": 8640000,
                    },
                },
            }
        ).encode()

    @pytest.mark.asyncio
    async def test_login_and_maindata_parsed(self):
        plugin = _make_plugin(
            clients=[
                {
                    "name": "qbit",
                    "type": "qbittorrent",
                    "url": "http://q:8080",
                    "username": "admin",
                    "password": "pw",
                }
            ]
        )
        maindata = self._maindata()
        call_log = []

        def _open(req, timeout=None):
            if req.data:
                call_log.append(("login", req.data))
                return _cm(b"Ok.")
            call_log.append(("maindata", None))
            return _cm(maindata)

        opener = MagicMock()
        opener.open = _open

        with patch("urllib.request.build_opener", return_value=opener):
            result = await plugin.collect()

        assert [c[0] for c in call_log] == ["login", "maindata"]
        assert b"admin" in call_log[0][1]
        assert b"pw" in call_log[0][1]

        row = result.detail["clients"][0]
        assert row["status"] == "ok"
        assert row["active"] == 1
        assert row["queued"] == 1
        assert row["dl_bytes_s"] == 1000
        assert row["ul_bytes_s"] == 200
        assert row["ratio"] == 1.5
        assert row["free_bytes"] == 100 * 1024**3

    @pytest.mark.asyncio
    async def test_no_username_skips_login(self):
        plugin = _make_plugin(
            clients=[{"name": "qbit", "type": "qbittorrent", "url": "http://q:8080"}]
        )
        maindata = self._maindata()
        call_count = [0]

        def _open(req, timeout=None):
            call_count[0] += 1
            return _cm(maindata)

        opener = MagicMock()
        opener.open = _open

        with patch("urllib.request.build_opener", return_value=opener):
            result = await plugin.collect()

        assert call_count[0] == 1
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_auth_failure_returns_error_row(self):
        plugin = _make_plugin(
            clients=[
                {
                    "name": "qbit",
                    "type": "qbittorrent",
                    "url": "http://q:8080",
                    "username": "admin",
                    "password": "wrong",
                }
            ]
        )
        opener = MagicMock()
        opener.open = lambda req, timeout=None: _cm(b"Fails.")

        with patch("urllib.request.build_opener", return_value=opener):
            result = await plugin.collect()

        assert result.status == "error"
        assert result.detail["clients"][0]["error"] == "authentication failed"


# =============================================================================
# Transmission
# =============================================================================


class TestTransmission:
    @pytest.mark.asyncio
    async def test_409_retry_then_success(self):
        plugin = _make_plugin(
            clients=[{"name": "trans", "type": "transmission", "url": "http://t:9091"}]
        )
        responses = [
            json.dumps(
                {
                    "arguments": {
                        "downloadSpeed": 1000,
                        "uploadSpeed": 500,
                        "cumulative-stats": {"downloadedBytes": 1000, "uploadedBytes": 200},
                    }
                }
            ).encode(),
            json.dumps(
                {
                    "arguments": {
                        "torrents": [
                            {
                                "name": "A",
                                "status": 4,
                                "percentDone": 0.5,
                                "rateDownload": 500,
                                "eta": 100,
                            }
                        ]
                    }
                }
            ).encode(),
            json.dumps({"arguments": {"download-dir": "/downloads"}}).encode(),
            json.dumps({"arguments": {"size-bytes": 500_000_000_000}}).encode(),
        ]
        call_count = [0]
        retry_headers = []

        def mock_urlopen(req, timeout=None, context=None):
            if call_count[0] == 0:
                call_count[0] += 1
                raise _http_error_409("sess-abc")
            idx = call_count[0] - 1
            if idx == 0:
                retry_headers.append(req.get_header("X-transmission-session-id"))
            call_count[0] += 1
            return _cm(responses[idx])

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert retry_headers == ["sess-abc"]
        row = result.detail["clients"][0]
        assert row["status"] == "ok"
        assert row["dl_bytes_s"] == 1000
        assert row["ratio"] == pytest.approx(0.2)
        assert row["free_bytes"] == 500_000_000_000
        assert row["active"] == 1


# =============================================================================
# SABnzbd
# =============================================================================


class TestSabnzbd:
    @pytest.mark.asyncio
    async def test_queue_parsed_and_apikey_sent(self):
        plugin = _make_plugin(
            clients=[
                {
                    "name": "sab",
                    "type": "sabnzbd",
                    "url": "http://sab:8080",
                    "api_key": "secret123",
                }
            ]
        )
        body = json.dumps(
            {
                "queue": {
                    "status": "Downloading",
                    "kbpersec": "500.0",
                    "diskspace1": "500.0",
                    "slots": [
                        {"filename": "movie.mkv", "percentage": "50", "status": "Downloading"},
                        {"filename": "show.mkv", "percentage": "0", "status": "Queued"},
                    ],
                }
            }
        ).encode()
        captured_urls = []

        def mock_urlopen(req, timeout=None, context=None):
            captured_urls.append(req.full_url)
            return _cm(body)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert "apikey=secret123" in captured_urls[0]
        row = result.detail["clients"][0]
        assert row["status"] == "ok"
        assert row["active"] == 1
        assert row["queued"] == 1
        assert row["dl_bytes_s"] == 512000
        assert row["ratio"] is None
        assert row["free_bytes"] == int(500.0 * 1024**3)

    @pytest.mark.asyncio
    async def test_paused_status_is_warn(self):
        plugin = _make_plugin(
            clients=[{"name": "sab", "type": "sabnzbd", "url": "http://sab:8080"}]
        )
        body = json.dumps(
            {
                "queue": {
                    "status": "Paused",
                    "kbpersec": "0",
                    "diskspace1": "500.0",
                    "slots": [],
                }
            }
        ).encode()

        with patch("urllib.request.urlopen", side_effect=lambda req, **kw: _cm(body)):
            result = await plugin.collect()

        assert result.detail["clients"][0]["status"] == "warn"


# =============================================================================
# NZBGet
# =============================================================================


class TestNzbget:
    @pytest.mark.asyncio
    async def test_status_and_groups_parsed(self):
        plugin = _make_plugin(
            clients=[
                {
                    "name": "nzb",
                    "type": "nzbget",
                    "url": "http://nzb:6789",
                    "username": "admin",
                    "password": "pw",
                }
            ]
        )
        status_resp = json.dumps(
            {"result": {"DownloadRate": 100000, "DownloadPaused": False, "FreeDiskSpaceMB": 500000}}
        ).encode()
        groups_resp = json.dumps(
            {
                "result": [
                    {
                        "NZBFilename": "a.nzb",
                        "FileSizeMB": 100,
                        "RemainingSizeMB": 50,
                        "Status": "DOWNLOADING",
                    },
                    {
                        "NZBFilename": "b.nzb",
                        "FileSizeMB": 100,
                        "RemainingSizeMB": 100,
                        "Status": "QUEUED",
                    },
                ]
            }
        ).encode()
        call_count = [0]
        auth_headers = []

        def mock_urlopen(req, timeout=None, context=None):
            auth_headers.append(req.get_header("Authorization"))
            idx = call_count[0]
            call_count[0] += 1
            return _cm(status_resp if idx == 0 else groups_resp)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert all(h and h.startswith("Basic ") for h in auth_headers)
        row = result.detail["clients"][0]
        assert row["status"] == "ok"
        assert row["active"] == 1
        assert row["queued"] == 1
        assert row["dl_bytes_s"] == 100000
        assert row["free_bytes"] == 500000 * 1024 * 1024

    @pytest.mark.asyncio
    async def test_free_disk_lo_hi_fallback(self):
        plugin = _make_plugin(clients=[{"name": "nzb", "type": "nzbget", "url": "http://nzb:6789"}])
        status_resp = json.dumps(
            {
                "result": {
                    "DownloadRate": 0,
                    "DownloadPaused": True,
                    "FreeDiskSpaceLo": 0,
                    "FreeDiskSpaceHi": 50,
                }
            }
        ).encode()
        groups_resp = json.dumps({"result": []}).encode()
        call_count = [0]

        def mock_urlopen(req, timeout=None, context=None):
            idx = call_count[0]
            call_count[0] += 1
            return _cm(status_resp if idx == 0 else groups_resp)

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        row = result.detail["clients"][0]
        assert row["free_bytes"] == 50 * (2**32)
        assert row["status"] == "warn"


# =============================================================================
# Aggregation, thresholds, sanitization
# =============================================================================


class TestAggregation:
    @pytest.mark.asyncio
    async def test_unreachable_client_does_not_hide_others(self):
        plugin = _make_plugin(
            clients=[
                {"name": "qbit", "type": "qbittorrent", "url": "http://q:8080"},
                {"name": "bad", "type": "transmission", "url": "http://bad:9091"},
            ]
        )
        maindata = json.dumps(
            {
                "server_state": {
                    "dl_info_speed": 100,
                    "up_info_speed": 0,
                    "free_space_on_disk": 100 * 1024**3,
                    "global_ratio": 1.0,
                },
                "torrents": {},
            }
        ).encode()
        opener = MagicMock()
        opener.open = lambda req, timeout=None: _cm(maindata)

        with (
            patch("urllib.request.build_opener", return_value=opener),
            patch("urllib.request.urlopen", side_effect=ConnectionRefusedError("refused")),
        ):
            result = await plugin.collect()

        assert result.status == "error"
        rows = {r["name"]: r for r in result.detail["clients"]}
        assert rows["qbit"]["status"] == "ok"
        assert rows["bad"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_unknown_type_is_per_client_error(self):
        plugin = _make_plugin(clients=[{"name": "x", "type": "deluge", "url": "http://d"}])
        result = await plugin.collect()
        assert result.status == "error"
        assert result.detail["clients"][0]["error"]

    @pytest.mark.asyncio
    async def test_error_message_is_sanitized(self):
        plugin = _make_plugin(
            clients=[{"name": "x", "type": "nzbget", "url": "http://user:secret@host:6789"}]
        )
        with patch(
            "urllib.request.urlopen",
            side_effect=Exception("http://user:secret@host:6789/jsonrpc unreachable"),
        ):
            result = await plugin.collect()

        blob = json.dumps(result.detail) + result.summary
        assert "secret" not in blob

    @pytest.mark.asyncio
    async def test_no_secrets_in_detail(self):
        plugin = _make_plugin(
            clients=[
                {
                    "name": "x",
                    "type": "sabnzbd",
                    "url": "http://s",
                    "api_key": "topsecret",
                }
            ]
        )
        with patch("urllib.request.urlopen", side_effect=Exception("boom")):
            result = await plugin.collect()

        blob = json.dumps(result.detail)
        assert "topsecret" not in blob


class TestThresholds:
    def _row(self, **overrides):
        row = {
            "name": "a",
            "type": "qbittorrent",
            "status": "ok",
            "error": "",
            "active": 0,
            "queued": 0,
            "dl_bytes_s": 0,
            "ul_bytes_s": 0,
            "ratio": None,
            "free_bytes": 20 * 1024**3,
            "items": [],
        }
        row.update(overrides)
        return row

    def test_below_warn_threshold(self):
        plugin = _make_plugin(disk_warn_gb=50, disk_critical_gb=10)
        data = plugin._make_panel([self._row(free_bytes=20 * 1024**3)])
        assert data.detail["clients"][0]["status"] == "warn"

    def test_below_critical_threshold(self):
        plugin = _make_plugin(disk_warn_gb=50, disk_critical_gb=10)
        data = plugin._make_panel([self._row(free_bytes=5 * 1024**3)])
        assert data.detail["clients"][0]["status"] == "error"

    def test_above_thresholds_stays_ok(self):
        plugin = _make_plugin(disk_warn_gb=50, disk_critical_gb=10)
        data = plugin._make_panel([self._row(free_bytes=100 * 1024**3)])
        assert data.detail["clients"][0]["status"] == "ok"


# =============================================================================
# render() / demo_data()
# =============================================================================


class TestRenderAndDemo:
    def test_demo_data_renders_without_raising(self):
        plugin = DownloadClientsPlugin()
        data = plugin.demo_data()
        assert data.status in {"ok", "warn"}
        assert data.summary

        blocks = plugin.render(data)
        assert blocks[0]["type"] == "keyvalue"
        assert blocks[1]["type"] == "badges"
        assert blocks[2]["type"] == "table"
        assert blocks[2]["columns"] == ["Name", "Client", "Progress", "Speed", "ETA"]

    def test_render_shows_free_disk_space_per_client(self):
        plugin = DownloadClientsPlugin()
        data = plugin.demo_data()

        blocks = plugin.render(data)
        disk_badges = next(b for b in blocks if b["type"] == "badges")
        labels = [item["label"] for item in disk_badges["items"]]
        assert any("420 GB free" in label for label in labels)
        assert any("15 GB free" in label for label in labels)
        # The demo SABnzbd client is below the default 50 GB warn threshold.
        sab_badge = next(item for item in disk_badges["items"] if "sab" in item["label"])
        assert sab_badge["status"] == "warn"

    def test_render_omits_disk_badges_when_no_free_bytes_reported(self):
        plugin = DownloadClientsPlugin()
        row = {
            "name": "a",
            "type": "qbittorrent",
            "status": "error",
            "error": "unreachable",
            "active": 0,
            "queued": 0,
            "dl_bytes_s": 0,
            "ul_bytes_s": 0,
            "ratio": None,
            "free_bytes": None,
            "items": [],
        }
        data = plugin._make_panel([row])

        blocks = plugin.render(data)
        assert all(b["type"] != "badges" for b in blocks)

    def test_render_no_clients_shows_dim_text(self):
        plugin = DownloadClientsPlugin()
        blocks = plugin.render(PanelData(status="disabled", summary="", detail={}))
        assert blocks == [{"type": "text", "value": "No downloads", "status": "dim"}]
