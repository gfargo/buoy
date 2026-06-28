"""Tests for the SnapRAID parity status plugin."""

from unittest.mock import patch

import pytest

# =============================================================================
# SnapRAID plugin
# =============================================================================

_SYNCED_OUTPUT = """\
SnapRAID status report
  100% of the array is scrubbed
  0d:03h:12m:44s since last sync
  0 errors
  Files are in sync.
"""

_UNSYNCED_OUTPUT = """\
SnapRAID status report
  1204 files to sync
  0d:25h:10m:00s since last sync
  0 errors
"""

_STALE_OUTPUT = """\
SnapRAID status report
  0d:30h:00m:00s since last sync
  0 errors
  Files are in sync.
"""

_DISK_ERROR_OUTPUT = """\
SnapRAID status report
  DANGER! 3 errors found!
  0d:01h:00m:00s since last sync
"""


class TestSnapraidPlugin:
    """Tests for the SnapRAID parity status plugin."""

    def _make_plugin(self, **cfg):
        from buoy.plugins.builtin.snapraid import SnapraidPlugin

        plugin = SnapraidPlugin()
        plugin.configure(cfg)
        return plugin

    # --- _parse_status unit tests ---

    def test_parse_synced(self):
        from buoy.plugins.builtin.snapraid import _parse_status

        r = _parse_status(_SYNCED_OUTPUT)
        assert r["unsynced_count"] == 0
        assert r["last_sync_age_hours"] == pytest.approx(3.2, abs=0.1)
        assert r["disk_errors"] is False
        assert r["scrub_pct"] == 100

    def test_parse_unsynced(self):
        from buoy.plugins.builtin.snapraid import _parse_status

        r = _parse_status(_UNSYNCED_OUTPUT)
        assert r["unsynced_count"] == 1204
        assert r["last_sync_age_hours"] > 24

    def test_parse_disk_errors(self):
        from buoy.plugins.builtin.snapraid import _parse_status

        r = _parse_status(_DISK_ERROR_OUTPUT)
        assert r["disk_errors"] is True

    def test_parse_no_errors_zero_count(self):
        from buoy.plugins.builtin.snapraid import _parse_status

        # "0 errors" should NOT set disk_errors
        r = _parse_status(_SYNCED_OUTPUT)
        assert r["disk_errors"] is False

    # --- collect() end-to-end tests ---

    @pytest.mark.asyncio
    async def test_not_configured_returns_disabled(self):
        plugin = self._make_plugin()  # no status_file
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_synced_recent_ok(self, tmp_path):
        f = tmp_path / "snapraid.status"
        f.write_text(_SYNCED_OUTPUT)
        plugin = self._make_plugin(status_file=str(f))
        result = await plugin.collect()
        assert result.status == "ok"
        assert "synced" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_unsynced_files_warn(self, tmp_path):
        f = tmp_path / "snapraid.status"
        f.write_text(_UNSYNCED_OUTPUT)
        plugin = self._make_plugin(status_file=str(f))
        result = await plugin.collect()
        assert result.status == "warn"
        assert "1,204" in result.summary

    @pytest.mark.asyncio
    async def test_stale_sync_warn(self, tmp_path):
        f = tmp_path / "snapraid.status"
        f.write_text(_STALE_OUTPUT)
        plugin = self._make_plugin(status_file=str(f), sync_max_age_hours=24)
        result = await plugin.collect()
        assert result.status == "warn"
        assert "30" in result.summary  # age hours

    @pytest.mark.asyncio
    async def test_disk_errors_error(self, tmp_path):
        f = tmp_path / "snapraid.status"
        f.write_text(_DISK_ERROR_OUTPUT)
        plugin = self._make_plugin(status_file=str(f))
        result = await plugin.collect()
        assert result.status == "error"
        assert "error" in result.summary.lower()

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_snapraid" in js
