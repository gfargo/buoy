"""Tests for Buoy built-in plugins.

Each plugin's collect() method is tested by mocking external calls
(HTTP APIs, subprocess, filesystem) and verifying the PanelData output.
"""

import importlib
import json
import time
from datetime import date, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.config import BuoyConfig, PluginEntry, PluginsConfig
from buoy.plugins.loader import PluginManager
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

# =============================================================================
# Plugin protocol / base class
# =============================================================================


class TestPluginProtocol:
    """Tests for the base Plugin class and PanelData."""

    def test_panel_data_defaults(self):
        data = PanelData()
        assert data.status == "ok"
        assert data.summary == ""
        assert data.detail == {}

    def test_panel_data_with_values(self):
        data = PanelData(status="warn", summary="3 issues", detail={"items": [1, 2, 3]})
        assert data.status == "warn"
        assert data.summary == "3 issues"
        assert data.detail["items"] == [1, 2, 3]

    def test_plugin_manifest_defaults(self):
        m = PluginManifest(id="test", name="Test")
        assert m.icon == ""
        assert m.refresh_interval == 60

    def test_plugin_configure(self):
        plugin = Plugin()
        plugin.configure({"token": "abc123", "url": "http://localhost"})
        assert plugin.config["token"] == "abc123"

    def test_plugin_config_is_per_instance(self):
        """Each Plugin instance must have its own config dict (no shared mutable default)."""
        a, b = Plugin(), Plugin()
        a.config["x"] = 1
        assert b.config == {}, "mutating one instance should not affect another"
        assert Plugin().config == {}, "fresh instance must not inherit mutations"

    @pytest.mark.asyncio
    async def test_base_collect_raises(self):
        plugin = Plugin()
        with pytest.raises(NotImplementedError):
            await plugin.collect()

    def test_frontend_js_returns_none_by_default(self):
        plugin = Plugin()
        assert plugin.frontend_js() is None

    @pytest.mark.asyncio
    async def test_setup_teardown_are_noops(self):
        plugin = Plugin()
        await plugin.setup()  # Should not raise
        await plugin.teardown()  # Should not raise


# =============================================================================
# GitHub plugin
# =============================================================================


class TestGitHubPlugin:
    """Tests for the GitHub notifications + PRs plugin."""

    def _make_plugin(self, token="ghp_test123"):
        from buoy.plugins.builtin.github import GitHubPlugin

        plugin = GitHubPlugin()
        plugin.configure({"token": token})
        return plugin

    @pytest.mark.asyncio
    async def test_no_token_returns_disabled(self):
        from buoy.plugins.builtin.github import GitHubPlugin

        plugin = GitHubPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_successful_collect(self):
        plugin = self._make_plugin()

        notifications_response = json.dumps(
            [
                {
                    "subject": {"title": "Fix CI", "type": "PullRequest"},
                    "repository": {"full_name": "gfargo/buoy"},
                },
                {
                    "subject": {"title": "Bump deps", "type": "Issue"},
                    "repository": {"full_name": "gfargo/strut"},
                },
            ]
        ).encode()

        prs_response = json.dumps(
            {
                "total_count": 1,
                "items": [
                    {"title": "Add tests", "html_url": "https://github.com/gfargo/buoy/pull/12"}
                ],
            }
        ).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: notifications_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: prs_response)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert result.status == "warn"  # has notifications
        assert "2 notifications" in result.summary
        assert "1 open PR" in result.summary
        assert result.detail["notification_count"] == 2
        assert result.detail["pr_count"] == 1

    @pytest.mark.asyncio
    async def test_all_clear(self):
        plugin = self._make_plugin()

        empty_notifications = json.dumps([]).encode()
        no_prs = json.dumps({"total_count": 0, "items": []}).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: empty_notifications)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: no_prs)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "All clear" in result.summary

    @pytest.mark.asyncio
    async def test_api_error_returns_error_status(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "API error" in result.summary

    @pytest.mark.asyncio
    async def test_render_produces_notification_and_pr_lists(self):
        plugin = self._make_plugin()

        notifications_response = json.dumps(
            [
                {
                    "subject": {"title": "Fix CI", "type": "PullRequest"},
                    "repository": {"full_name": "gfargo/buoy"},
                }
            ]
        ).encode()
        prs_response = json.dumps(
            {
                "total_count": 1,
                "items": [
                    {"title": "Add tests", "html_url": "https://github.com/gfargo/buoy/pull/12"}
                ],
            }
        ).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: notifications_response)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: prs_response)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "list"
        assert blocks[0]["items"][0]["primary"] == "PullRequest: Fix CI"
        assert blocks[1]["type"] == "list"
        assert blocks[1]["items"][0]["href"] == "https://github.com/gfargo/buoy/pull/12"

    @pytest.mark.asyncio
    async def test_render_all_clear_shows_text(self):
        plugin = self._make_plugin()
        empty_notifications = json.dumps([]).encode()
        no_prs = json.dumps({"total_count": 0, "items": []}).encode()

        call_count = [0]

        def mock_urlopen(req, timeout=None, **kwargs):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: empty_notifications)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: no_prs)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks == [{"type": "text", "value": "All clear", "status": "dim"}]


# =============================================================================
# Loki plugin
# =============================================================================


class TestLokiPlugin:
    """Tests for the Loki error logs plugin."""

    def _make_plugin(self, url="http://loki:3100"):
        from buoy.plugins.builtin.loki import LokiPlugin

        plugin = LokiPlugin()
        plugin.configure({"url": url})
        return plugin

    @pytest.mark.asyncio
    async def test_no_url_returns_disabled(self):
        from buoy.plugins.builtin.loki import LokiPlugin

        plugin = LokiPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_errors_found(self):
        plugin = self._make_plugin()

        loki_response = json.dumps(
            {
                "data": {
                    "result": [
                        {
                            "stream": {"job": "buoy"},
                            "values": [
                                ["1719300000000000000", "ERROR: connection timeout"],
                                ["1719299000000000000", "FATAL: disk full"],
                            ],
                        }
                    ]
                }
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: loki_response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "2 recent errors" in result.summary
        assert len(result.detail["entries"]) == 2

    @pytest.mark.asyncio
    async def test_no_errors(self):
        plugin = self._make_plugin()

        loki_response = json.dumps({"data": {"result": []}}).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: loki_response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "No errors" in result.summary

    @pytest.mark.asyncio
    async def test_unreachable(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("timeout")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    @pytest.mark.asyncio
    async def test_render_produces_list_of_error_lines(self):
        plugin = self._make_plugin()

        loki_response = json.dumps(
            {
                "data": {
                    "result": [
                        {
                            "stream": {"job": "buoy"},
                            "values": [["1719300000000000000", "ERROR: connection timeout"]],
                        }
                    ]
                }
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: loki_response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "list"
        item = blocks[0]["items"][0]
        assert item["primary"] == "ERROR: connection timeout"
        assert item["status"] == "error"
        assert "buoy" in item["secondary"]
        assert "UTC" in item["secondary"]

    @pytest.mark.asyncio
    async def test_render_no_errors_shows_text(self):
        plugin = self._make_plugin()
        loki_response = json.dumps({"data": {"result": []}}).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: loki_response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks == [{"type": "text", "value": "No recent errors", "status": "dim"}]


# =============================================================================
# UptimeKuma plugin
# =============================================================================


class TestUptimeKumaPlugin:
    """Tests for the UptimeKuma service health plugin."""

    def _make_plugin(self, url="http://uptime:3001"):
        from buoy.plugins.builtin.uptime_kuma import UptimeKumaPlugin

        plugin = UptimeKumaPlugin()
        plugin.configure({"url": url})
        return plugin

    @pytest.mark.asyncio
    async def test_no_url_returns_disabled(self):
        from buoy.plugins.builtin.uptime_kuma import UptimeKumaPlugin

        plugin = UptimeKumaPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_all_up(self):
        plugin = self._make_plugin()

        response = json.dumps(
            {
                "heartbeatList": {
                    "1": [{"status": 1, "msg": "Grafana"}],
                    "2": [{"status": 1, "msg": "Plane"}],
                    "3": [{"status": 1, "msg": "Trigger.dev"}],
                }
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "3/3 up" in result.summary

    @pytest.mark.asyncio
    async def test_some_down(self):
        plugin = self._make_plugin()

        response = json.dumps(
            {
                "heartbeatList": {
                    "1": [{"status": 1, "msg": "Grafana"}],
                    "2": [{"status": 0, "msg": "Plane"}],
                }
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "error"
        assert "1/2 up" in result.summary

    @pytest.mark.asyncio
    async def test_unreachable(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    @pytest.mark.asyncio
    async def test_render_produces_badges_with_up_down_status(self):
        plugin = self._make_plugin()

        response = json.dumps(
            {
                "heartbeatList": {
                    "1": [{"status": 1, "msg": "web"}],
                    "2": [{"status": 0, "msg": "db"}],
                }
            }
        ).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "badges"
        statuses = {b["label"]: b["status"] for b in blocks[0]["items"]}
        assert statuses["web"] == "ok"
        assert statuses["db"] == "error"


# =============================================================================
# Plane plugin
# =============================================================================


class TestPlanePlugin:
    """Tests for the Plane sprint/cycle progress plugin."""

    def _make_plugin(self):
        from buoy.plugins.builtin.plane import PlanePlugin

        plugin = PlanePlugin()
        plugin.configure(
            {
                "api_key": "test-key",
                "url": "https://plane.example.com",
                "workspace": "gfargo",
                "project": "cf7d9230",
            }
        )
        return plugin

    @pytest.mark.asyncio
    async def test_not_configured(self):
        from buoy.plugins.builtin.plane import PlanePlugin

        plugin = PlanePlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_active_cycle(self):
        from datetime import date, timedelta

        plugin = self._make_plugin()
        end = (date.today() + timedelta(days=5)).isoformat()
        start = (date.today() - timedelta(days=9)).isoformat()

        response = json.dumps(
            {
                "results": [
                    {
                        "name": "Sprint 4",
                        "start_date": start,
                        "end_date": end,
                        "total_issues": 20,
                        "completed_issues": 12,
                    }
                ]
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "60% complete" in result.summary
        assert result.detail["cycle"] == "Sprint 4"
        assert result.detail["days_left"] == 5

    @pytest.mark.asyncio
    async def test_no_active_cycle(self):
        from datetime import date, timedelta

        plugin = self._make_plugin()
        past_end = (date.today() - timedelta(days=5)).isoformat()
        past_start = (date.today() - timedelta(days=19)).isoformat()

        response = json.dumps(
            {
                "results": [
                    {
                        "name": "Sprint 3",
                        "start_date": past_start,
                        "end_date": past_end,
                        "total_issues": 10,
                        "completed_issues": 10,
                    }
                ]
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "No active cycle" in result.summary

    @pytest.mark.asyncio
    async def test_api_error(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("SSL error")):
            result = await plugin.collect()

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_render_produces_cycle_name_and_bar(self):
        plugin = self._make_plugin()
        today = date.today()
        cycles_response = json.dumps(
            {
                "results": [
                    {
                        "name": "Sprint 1",
                        "start_date": (today - timedelta(days=2)).isoformat(),
                        "end_date": (today + timedelta(days=5)).isoformat(),
                        "total_issues": 10,
                        "completed_issues": 4,
                    }
                ]
            }
        ).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: cycles_response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0] == {"type": "text", "value": "Sprint 1", "status": None}
        assert blocks[1]["type"] == "bar"
        assert blocks[1]["pct"] == 40

    @pytest.mark.asyncio
    async def test_render_no_active_cycle_shows_text(self):
        plugin = self._make_plugin()
        cycles_response = json.dumps({"results": []}).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: cycles_response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks == [{"type": "text", "value": "No active cycle", "status": "dim"}]


# =============================================================================
# Backup Status plugin
# =============================================================================


class TestBackupStatusPlugin:
    """Tests for the backup freshness/health plugin."""

    def _make_plugin(self, backup_dir="/backup"):
        from buoy.plugins.builtin.backup_status import BackupStatusPlugin

        plugin = BackupStatusPlugin()
        plugin.configure(
            {
                "backup_dir": backup_dir,
                "pattern": "*.sql.gz",
                "max_age_hours": 36,
                "min_size_bytes": 100,
            }
        )
        return plugin

    @pytest.mark.asyncio
    async def test_dir_not_found(self):
        plugin = self._make_plugin(backup_dir="/nonexistent/path")
        result = await plugin.collect()
        assert result.status == "warn"
        assert "Dir not found" in result.summary

    @pytest.mark.asyncio
    async def test_no_backups_found(self, tmp_path):
        plugin = self._make_plugin(backup_dir=str(tmp_path))
        result = await plugin.collect()
        assert result.status == "error"
        assert "No backups" in result.summary

    @pytest.mark.asyncio
    async def test_healthy_backup(self, tmp_path):
        # Create a recent, valid backup file
        backup = tmp_path / "plane-2026-06-26_1405.sql.gz"
        backup.write_bytes(b"x" * 1024)  # 1KB, above min_size

        plugin = self._make_plugin(backup_dir=str(tmp_path))
        result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["healthy"] is True
        assert result.detail["total_count"] == 1

    @pytest.mark.asyncio
    async def test_too_small_backup(self, tmp_path):
        backup = tmp_path / "plane-2026-06-26_1405.sql.gz"
        backup.write_bytes(b"x" * 10)  # 10 bytes, below 100 min

        plugin = self._make_plugin(backup_dir=str(tmp_path))
        result = await plugin.collect()

        assert result.status == "error"
        assert result.detail["healthy"] is False
        assert "too small" in result.detail["issues"][0]

    @pytest.mark.asyncio
    async def test_too_old_backup(self, tmp_path):
        import os

        backup = tmp_path / "plane-2026-06-20_0000.sql.gz"
        backup.write_bytes(b"x" * 1024)
        # Set mtime to 48h ago
        old_time = time.time() - (48 * 3600)
        os.utime(backup, (old_time, old_time))

        plugin = self._make_plugin(backup_dir=str(tmp_path))
        result = await plugin.collect()

        assert result.status == "error"
        assert result.detail["healthy"] is False
        assert "too old" in result.detail["issues"][0]

    @pytest.mark.asyncio
    async def test_multiple_backups_picks_latest(self, tmp_path):
        import os

        # Older backup
        old = tmp_path / "plane-2026-06-24_0000.sql.gz"
        old.write_bytes(b"x" * 1024)
        os.utime(old, (time.time() - 7200, time.time() - 7200))

        # Newer backup
        new = tmp_path / "plane-2026-06-26_1400.sql.gz"
        new.write_bytes(b"x" * 2048)

        plugin = self._make_plugin(backup_dir=str(tmp_path))
        result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["latest_file"] == "plane-2026-06-26_1400.sql.gz"
        assert result.detail["total_count"] == 2


# =============================================================================
# Cron Health plugin
# =============================================================================


class TestCronHealthPlugin:
    """Tests for the cron job monitoring plugin."""

    def _make_plugin(self):
        from buoy.plugins.builtin.cron_health import CronHealthPlugin

        plugin = CronHealthPlugin()
        plugin.configure({})
        return plugin

    @pytest.mark.asyncio
    async def test_no_cron_activity(self):
        plugin = self._make_plugin()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("nsenter")):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "No cron activity" in result.summary

    @pytest.mark.asyncio
    async def test_parses_cron_entries(self):
        plugin = self._make_plugin()

        journal_output = (
            b"Jun 26 14:05:01 compass CRON[1234]: (root) CMD (/backup/scripts/nightly-plane.sh)\n"
            b"Jun 26 14:06:01 compass CRON[1235]: (gfargo) CMD (python3 /home/gfargo/pironman5-oled/pages/rgb_status.py)\n"
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(journal_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "2 jobs" in result.summary
        assert len(result.detail["entries"]) == 2
        assert result.detail["entries"][0]["user"] == "root"
        assert result.detail["entries"][1]["user"] == "gfargo"

    @pytest.mark.asyncio
    async def test_empty_output(self):
        plugin = self._make_plugin()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "No cron activity" in result.summary

    @pytest.mark.asyncio
    async def test_render_produces_table_of_cron_entries(self):
        plugin = self._make_plugin()
        journal_output = (
            b"Jun 26 14:05:01 compass CRON[1234]: (root) CMD (/backup/scripts/nightly-plane.sh)\n"
        )
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(journal_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "table"
        assert blocks[0]["columns"] == ["Time", "User", "Command"]
        row = blocks[0]["rows"][0]
        assert row[1]["value"] == "root"
        assert row[2]["value"] == "/backup/scripts/nightly-plane.sh"

    def test_render_no_entries_shows_text(self):
        plugin = self._make_plugin()
        blocks = plugin.render(PanelData(status="ok", detail={"entries": []}))
        assert blocks == [{"type": "text", "value": "No cron activity in 24h", "status": "dim"}]


# =============================================================================
# Prometheus Exporter plugin
# =============================================================================


class TestPrometheusExporterPlugin:
    """Tests for the Prometheus metrics exporter."""

    def _make_plugin(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        plugin = PrometheusExporterPlugin()
        plugin.configure({})
        return plugin

    @pytest.mark.asyncio
    async def test_collect_returns_ok(self):
        plugin = self._make_plugin()
        result = await plugin.collect()
        assert result.status == "ok"
        assert "/metrics" in result.summary

    def test_format_metrics_basic(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        stats = {
            "hostname": "compass",
            "cpu": 42.5,
            "mem_used": 4.0,
            "mem_total": 8.0,
            "temp": 55,
            "disk_pct": 67,
            "containers": 21,
            "uptime_h": 120,
            "uptime_m": 30,
        }
        output = PrometheusExporterPlugin.format_metrics(stats)

        assert 'buoy_cpu_percent{host="compass"} 42.5' in output
        assert 'buoy_temperature_celsius{host="compass"} 55' in output
        assert 'buoy_disk_used_percent{host="compass"} 67' in output
        assert 'buoy_containers_running{host="compass"} 21' in output
        # Uptime: 120h * 3600 + 30 * 60 = 433800
        assert 'buoy_uptime_seconds{host="compass"} 433800' in output

    def test_format_metrics_with_nvme(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        stats = {
            "hostname": "compass",
            "cpu": 10,
            "mem_used": 2.0,
            "mem_total": 8.0,
            "temp": 39,
            "disk_pct": 45,
            "containers": 5,
            "uptime_h": 10,
            "uptime_m": 0,
            "nvme": {"temp": 38, "wear_pct": 2},
        }
        output = PrometheusExporterPlugin.format_metrics(stats)

        assert 'buoy_nvme_temperature_celsius{host="compass"} 38' in output
        assert 'buoy_nvme_wear_percent{host="compass"} 2' in output

    def test_format_metrics_without_nvme(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        stats = {
            "hostname": "watch",
            "cpu": 5,
            "mem_used": 1.0,
            "mem_total": 4.0,
            "temp": 44,
            "disk_pct": 30,
            "containers": 8,
            "uptime_h": 200,
            "uptime_m": 0,
        }
        output = PrometheusExporterPlugin.format_metrics(stats)

        assert "buoy_nvme" not in output

    def test_format_metrics_has_help_and_type(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        stats = {
            "hostname": "test",
            "cpu": 0,
            "mem_used": 0,
            "mem_total": 1,
            "temp": 0,
            "disk_pct": 0,
            "containers": 0,
            "uptime_h": 0,
            "uptime_m": 0,
        }
        output = PrometheusExporterPlugin.format_metrics(stats)

        assert "# HELP buoy_cpu_percent" in output
        assert "# TYPE buoy_cpu_percent gauge" in output
        assert "# HELP buoy_memory_used_bytes" in output
        assert "# TYPE buoy_memory_used_bytes gauge" in output

    def test_format_metrics_escapes_host_label(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        # hostname with all three characters that need escaping: " \ newline
        hostile_name = 'he said "hi"\\back\nline'
        stats = {
            "hostname": hostile_name,
            "cpu": 1,
            "mem_used": 0.0,
            "mem_total": 1.0,
            "temp": 0,
            "disk_pct": 0,
            "containers": 0,
            "uptime_h": 0,
            "uptime_m": 0,
        }
        output = PrometheusExporterPlugin.format_metrics(stats)

        # The raw unescaped double-quote must NOT appear inside a label value
        # (every label value is delimited by " so a bare " would break parsing)
        # We check that the escaped sequences ARE present instead.
        assert '\\"' in output  # escaped double-quote
        assert "\\\\" in output  # escaped backslash (two chars: \\)
        assert "\\n" in output  # escaped newline literal

        # Verify that the literal \n in the hostname was escaped (not injected as
        # a real line break).  Checking per-line after splitlines() is dead code
        # because splitlines() already splits on \n, so individual elements can
        # never contain a bare newline.  Instead, compare the line count against
        # a clean hostname: if escaping failed the hostile name would add an extra
        # line for each raw newline it contains.
        clean_stats = dict(stats, hostname="clean")
        clean_output = PrometheusExporterPlugin.format_metrics(clean_stats)
        assert len(output.splitlines()) == len(clean_output.splitlines()), (
            "Hostile hostname injected extra lines — newline escaping failed"
        )

    def test_format_metrics_uptime_uses_uptime_s(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        # uptime_s takes precedence; h/m would give a different (rounded) value
        stats = {
            "hostname": "compass",
            "cpu": 0,
            "mem_used": 0.0,
            "mem_total": 1.0,
            "temp": 0,
            "disk_pct": 0,
            "containers": 0,
            "uptime_h": 120,
            "uptime_m": 30,
            "uptime_s": 433830,  # 120*3600 + 30*60 + 30 extra seconds
        }
        output = PrometheusExporterPlugin.format_metrics(stats)

        # Should use the exact uptime_s value, not the h/m reconstruction (433800)
        assert 'buoy_uptime_seconds{host="compass"} 433830' in output
        assert 'buoy_uptime_seconds{host="compass"} 433800' not in output

    def test_format_metrics_uptime_fallback_without_uptime_s(self):
        from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

        # No uptime_s → fall back to h*3600 + m*60
        stats = {
            "hostname": "compass",
            "cpu": 0,
            "mem_used": 0.0,
            "mem_total": 1.0,
            "temp": 0,
            "disk_pct": 0,
            "containers": 0,
            "uptime_h": 120,
            "uptime_m": 30,
        }
        output = PrometheusExporterPlugin.format_metrics(stats)

        assert 'buoy_uptime_seconds{host="compass"} 433800' in output

    def test_manifest(self):
        plugin = self._make_plugin()
        assert plugin.manifest.id == "prometheus_exporter"
        assert plugin.manifest.refresh_interval == 9999  # Pulled on demand


# =============================================================================
# Speedtest plugin
# =============================================================================


class TestSpeedtestPlugin:
    """Tests for the internet speedtest tracker plugin."""

    def _make_plugin(self, config=None, binary="speedtest-cli"):
        from buoy.plugins.builtin.speedtest import SpeedtestPlugin

        plugin = SpeedtestPlugin()
        plugin.configure(config or {})
        # Simulate a binary being found so collect() doesn't short-circuit to
        # 'unavailable'.  Tests that exercise the no-binary path set _binary=None
        # explicitly after calling this helper.
        plugin._binary = binary
        return plugin

    @pytest.mark.asyncio
    async def test_no_history_returns_measuring(self):
        plugin = self._make_plugin()
        result = await plugin.collect()
        assert result.status == "ok"
        assert "Measuring" in result.summary

    @pytest.mark.asyncio
    async def test_successful_result_ok_status(self):
        plugin = self._make_plugin()
        plugin._history = [
            {
                "ts": 1000.0,
                "download_mbps": 480.0,
                "upload_mbps": 22.0,
                "ping_ms": 12.0,
                "server": "Test",
                "ok": True,
            }
        ]
        result = await plugin.collect()
        assert result.status == "ok"
        assert "480" in result.summary
        assert "22" in result.summary
        assert "12" in result.summary

    @pytest.mark.asyncio
    async def test_baseline_drop_returns_warn(self):
        """A >50% drop below median baseline should produce warn status."""
        plugin = self._make_plugin()
        # Nine entries at 400 Mbps set the baseline; latest drops to 150 (~62% drop)
        plugin._history = [
            {
                "ts": float(i),
                "download_mbps": 400.0,
                "upload_mbps": 20.0,
                "ping_ms": 10.0,
                "server": "",
                "ok": True,
            }
            for i in range(9)
        ] + [
            {
                "ts": 9.0,
                "download_mbps": 150.0,
                "upload_mbps": 20.0,
                "ping_ms": 10.0,
                "server": "",
                "ok": True,
            }
        ]
        result = await plugin.collect()
        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_failed_entry_returns_error(self):
        plugin = self._make_plugin()
        plugin._history = [
            {
                "ts": 1.0,
                "download_mbps": 0.0,
                "upload_mbps": 0.0,
                "ping_ms": 0.0,
                "server": "",
                "ok": False,
                "error": "timeout",
            }
        ]
        result = await plugin.collect()
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_run_test_parses_json(self):
        """Legacy speedtest-cli: flat schema, speeds in bits/s."""
        plugin = self._make_plugin(binary="speedtest-cli")
        speedtest_output = json.dumps(
            {
                "download": 480_000_000,
                "upload": 22_000_000,
                "ping": 12.5,
                "server": {"name": "Test Server"},
            }
        ).encode()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(speedtest_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            entry = await plugin._run_test()

        # Legacy CLI must use --json (not --format=json)
        call_args = mock_exec.call_args[0]
        assert "--json" in call_args
        assert "--format=json" not in call_args

        assert entry["ok"] is True
        assert abs(entry["download_mbps"] - 480.0) < 0.1
        assert abs(entry["upload_mbps"] - 22.0) < 0.1
        assert entry["ping_ms"] == 12.5
        assert entry["server"] == "Test Server"

    @pytest.mark.asyncio
    async def test_run_test_parses_ookla_json(self):
        """Ookla speedtest CLI: nested schema, bandwidth in Bytes/s."""
        plugin = self._make_plugin(binary="speedtest")
        # Ookla emits bandwidth in Bytes/s; 60_000_000 B/s ÷ 125_000 = 480 Mbps
        ookla_output = json.dumps(
            {
                "type": "result",
                "download": {"bandwidth": 60_000_000, "bytes": 0, "elapsed": 0},
                "upload": {"bandwidth": 2_750_000, "bytes": 0, "elapsed": 0},
                "ping": {"latency": 14.2, "jitter": 1.1},
                "server": {"name": "Ookla Server"},
            }
        ).encode()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(ookla_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            entry = await plugin._run_test()

        # Ookla CLI must use --format=json (not --json) and include license flags
        call_args = mock_exec.call_args[0]
        assert "--format=json" in call_args
        assert "--accept-license" in call_args
        assert "--accept-gdpr" in call_args
        assert "--json" not in call_args

        assert entry["ok"] is True
        # 60_000_000 B/s ÷ 125_000 = 480 Mbps
        assert abs(entry["download_mbps"] - 480.0) < 0.1
        # 2_750_000 B/s ÷ 125_000 = 22 Mbps
        assert abs(entry["upload_mbps"] - 22.0) < 0.1
        assert abs(entry["ping_ms"] - 14.2) < 0.01
        assert entry["server"] == "Ookla Server"

    @pytest.mark.asyncio
    async def test_run_test_ookla_uses_server_id_flag(self):
        """Ookla CLI uses --server-id (not --server) for server selection."""
        plugin = self._make_plugin(config={"server_id": "12345"}, binary="speedtest")
        ookla_output = json.dumps(
            {
                "download": {"bandwidth": 10_000_000, "bytes": 0, "elapsed": 0},
                "upload": {"bandwidth": 1_000_000, "bytes": 0, "elapsed": 0},
                "ping": {"latency": 5.0, "jitter": 0.5},
                "server": {"name": "S"},
            }
        ).encode()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(ookla_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc) as mock_exec:
            await plugin._run_test()

        call_args = mock_exec.call_args[0]
        assert "--server-id" in call_args
        assert "12345" in call_args
        # --server (legacy flag) must NOT appear as a standalone argument
        assert "--server" not in call_args

    @pytest.mark.asyncio
    async def test_run_test_binary_missing(self):
        plugin = self._make_plugin()
        with patch(
            "asyncio.create_subprocess_exec", side_effect=FileNotFoundError("speedtest-cli")
        ):
            entry = await plugin._run_test()
        assert entry["ok"] is False
        assert "not found" in entry.get("error", "")

    @pytest.mark.asyncio
    async def test_render_produces_sparkline_and_keyvalue(self):
        plugin = self._make_plugin()
        plugin._history = [
            {
                "ts": float(i),
                "download_mbps": 400.0 + i,
                "upload_mbps": 20.0,
                "ping_ms": 10.0,
                "server": "Test",
                "ok": True,
            }
            for i in range(3)
        ]
        result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "sparkline"
        assert blocks[0]["values"] == [400.0, 401.0, 402.0]
        assert blocks[1]["type"] == "keyvalue"

    @pytest.mark.asyncio
    async def test_render_measuring_shows_text(self):
        plugin = self._make_plugin()
        result = await plugin.collect()
        blocks = plugin.render(result)
        assert blocks == [{"type": "text", "value": "Measuring…", "status": "dim"}]

    # ── New tests for optional-dependency behaviour ────────────────────────────

    def test_find_speedtest_binary_returns_none_when_absent(self):
        """_find_speedtest_binary returns None when neither CLI is on PATH."""
        from buoy.plugins.builtin.speedtest import _find_speedtest_binary

        with patch("shutil.which", return_value=None):
            assert _find_speedtest_binary() is None

    def test_find_speedtest_binary_prefers_official_cli(self):
        """_find_speedtest_binary returns 'speedtest' (Ookla) before 'speedtest-cli'."""
        from buoy.plugins.builtin.speedtest import _find_speedtest_binary

        def _which(name):
            return f"/usr/bin/{name}" if name == "speedtest" else None

        with patch("shutil.which", side_effect=_which):
            assert _find_speedtest_binary() == "speedtest"

    def test_find_speedtest_binary_falls_back_to_legacy(self):
        """_find_speedtest_binary returns 'speedtest-cli' when only that is present."""
        from buoy.plugins.builtin.speedtest import _find_speedtest_binary

        def _which(name):
            return "/usr/local/bin/speedtest-cli" if name == "speedtest-cli" else None

        with patch("shutil.which", side_effect=_which):
            assert _find_speedtest_binary() == "speedtest-cli"

    @pytest.mark.asyncio
    async def test_collect_unavailable_when_no_binary(self):
        """collect() returns status='unavailable' and a helpful hint when no CLI is found."""
        plugin = self._make_plugin()
        plugin._binary = None  # simulate no binary found at setup()
        result = await plugin.collect()
        assert result.status == "unavailable"
        assert "speedtest" in result.summary.lower()
        assert "hint" in result.detail


# =============================================================================
# Journal Errors plugin
# =============================================================================


class TestJournalErrorsPlugin:
    """Tests for the system journal error count plugin."""

    def _make_plugin(self):
        from buoy.plugins.builtin.journal_errors import JournalErrorsPlugin

        plugin = JournalErrorsPlugin()
        plugin.configure({})
        return plugin

    @pytest.mark.asyncio
    async def test_no_errors_status_ok(self):
        plugin = self._make_plugin()

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("nsenter")):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "0 errors" in result.summary

    @pytest.mark.asyncio
    async def test_empty_output(self):
        plugin = self._make_plugin()

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(b"", b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "0 errors" in result.summary
        assert result.detail["entries"] == []

    @pytest.mark.asyncio
    async def test_parses_entries_warn(self):
        plugin = self._make_plugin()

        journal_output = (
            b"Jun 26 14:05:01 compass kernel: oom-kill event: constraint=CONSTRAINT_NONE\n"
            b"Jun 26 14:06:01 compass systemd[1]: Failed to start foo.service\n"
            b"Jun 26 14:07:01 compass sshd[9999]: error: PAM auth failed for root\n"
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(journal_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "3 errors" in result.summary
        assert len(result.detail["entries"]) == 3
        assert result.detail["entries"][0]["unit"] == "kernel"
        assert "oom-kill" in result.detail["entries"][0]["message"]

    @pytest.mark.asyncio
    async def test_many_entries_error(self):
        plugin = self._make_plugin()

        lines = (
            b"\n".join(
                f"Jun 26 14:{i:02d}:01 compass sshd[1]: error: something".encode() for i in range(6)
            )
            + b"\n"
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(lines, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        assert result.status == "error"
        assert "6 errors" in result.summary

    @pytest.mark.asyncio
    async def test_respects_max_entries(self):
        from buoy.plugins.builtin.journal_errors import JournalErrorsPlugin

        plugin = JournalErrorsPlugin()
        plugin.configure({"max_entries": 5})

        # Subprocess returns 5 lines (tail -5 is baked in the command)
        lines = (
            b"\n".join(
                f"Jun 26 14:{i:02d}:01 compass sshd[1]: error: something".encode() for i in range(5)
            )
            + b"\n"
        )

        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(lines, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        assert len(result.detail["entries"]) <= 5

    @pytest.mark.asyncio
    async def test_render_produces_table_with_error_colored_message(self):
        plugin = self._make_plugin()
        journal_output = b"Jun 26 14:05:01 compass kernel: oom-kill event\n"
        mock_proc = AsyncMock()
        mock_proc.communicate = AsyncMock(return_value=(journal_output, b""))

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "table"
        row = blocks[0]["rows"][0]
        assert row[1]["value"] == "kernel"
        assert row[2]["status"] == "error"
        assert row[2]["truncate"] is True

    def test_render_no_entries_shows_text(self):
        plugin = self._make_plugin()
        blocks = plugin.render(PanelData(status="ok", detail={"entries": []}))
        assert blocks == [{"type": "text", "value": "No journal errors in 24h", "status": "dim"}]


# =============================================================================
# Actual Budget plugin
# =============================================================================


class TestActualBudgetPlugin:
    """Tests for the Actual Budget monthly summary plugin."""

    def _make_plugin(self):
        from buoy.plugins.builtin.actual_budget import ActualBudgetPlugin

        plugin = ActualBudgetPlugin()
        plugin.configure(
            {
                "url": "http://actual-http-api:5007",
                "api_key": "test-key",
                "budget_sync_id": "abc-123",
            }
        )
        return plugin

    @pytest.mark.asyncio
    async def test_no_config_returns_disabled(self):
        from buoy.plugins.builtin.actual_budget import ActualBudgetPlugin

        plugin = ActualBudgetPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_under_budget_ok(self):
        plugin = self._make_plugin()

        response = json.dumps(
            {
                "categoryGroups": [
                    {
                        "categories": [
                            {"name": "Groceries", "budgeted": 500000, "spent": -300000},
                            {"name": "Transport", "budgeted": 200000, "spent": -100000},
                        ]
                    }
                ]
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["spent"] == 400.0
        assert result.detail["budgeted"] == 700.0
        assert result.detail["pct"] == 57
        assert "$400.00 / $700.00" in result.summary

    @pytest.mark.asyncio
    async def test_over_90_percent_warn(self):
        plugin = self._make_plugin()

        response = json.dumps(
            {
                "categoryGroups": [
                    {
                        "categories": [
                            {"name": "Groceries", "budgeted": 500000, "spent": -480000},
                        ]
                    }
                ]
            }
        ).encode()

        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["pct"] == 96

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin()

        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary
        assert "error" in result.detail

    @pytest.mark.asyncio
    async def test_render_produces_bar_and_category_breakdown(self):
        plugin = self._make_plugin()
        response = json.dumps(
            {
                "categoryGroups": [
                    {
                        "categories": [
                            {"name": "Groceries", "budgeted": 500000, "spent": -480000},
                        ]
                    }
                ]
            }
        ).encode()
        mock_cm = MagicMock()
        mock_cm.__enter__ = lambda s: MagicMock(read=lambda: response)
        mock_cm.__exit__ = lambda s, *a: None

        with patch("urllib.request.urlopen", return_value=mock_cm):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "bar"
        assert blocks[1]["type"] == "keyvalue"
        row = blocks[1]["rows"][0]
        assert row["label"] == "Groceries"
        assert row["status"] == "error"  # 96% > 90% threshold

    @pytest.mark.asyncio
    async def test_render_not_configured_shows_text(self):
        from buoy.plugins.builtin.actual_budget import ActualBudgetPlugin

        plugin = ActualBudgetPlugin()
        plugin.configure({})
        result = await plugin.collect()
        blocks = plugin.render(result)
        assert blocks == [{"type": "text", "value": "Not configured", "status": "dim"}]


# =============================================================================
# Plugin discovery
# =============================================================================

ALL_BUILTIN_IDS = {
    "actual_budget",
    "backup_status",
    "cron_health",
    "dns_filter",
    "github",
    "immich",
    "jellyfin",
    "journal_errors",
    "loki",
    "plane",
    "prometheus_exporter",
    "smart_disk",
    "snapraid",
    "speedtest",
    "systemd_health",
    "tailscale",
    "uptime_kuma",
}


def _make_config(enabled_ids: set[str]) -> BuoyConfig:
    """Build a BuoyConfig with the given plugin IDs enabled."""
    builtin = {pid: PluginEntry(enabled=True, settings={}) for pid in enabled_ids}
    return BuoyConfig(plugins=PluginsConfig(enabled=True, builtin=builtin))


class TestPluginDiscovery:
    """Tests for auto-discovery of built-in plugins via pkgutil."""

    @pytest.mark.asyncio
    async def test_discovery_finds_all_builtins(self):
        """All 11 built-in plugin IDs are discovered and loaded when enabled."""
        config = _make_config(ALL_BUILTIN_IDS)
        mgr = PluginManager(config)
        await mgr._load_builtins()
        assert set(mgr._plugins.keys()) == ALL_BUILTIN_IDS

    @pytest.mark.asyncio
    async def test_unconfigured_plugin_stays_dormant(self):
        """Plugins absent from config (or disabled) are not activated."""
        config = _make_config(set())  # nothing enabled
        mgr = PluginManager(config)
        await mgr._load_builtins()
        assert mgr._plugins == {}

    @pytest.mark.asyncio
    async def test_disabled_plugin_stays_dormant(self):
        """A plugin present in config but with enabled=False is not activated."""
        config = BuoyConfig(
            plugins=PluginsConfig(
                enabled=True,
                builtin={"github": PluginEntry(enabled=False, settings={})},
            )
        )
        mgr = PluginManager(config)
        await mgr._load_builtins()
        assert "github" not in mgr._plugins

    @pytest.mark.asyncio
    async def test_import_error_in_one_plugin_is_isolated(self):
        """An ImportError in one module is caught; other plugins still load."""
        # Enable two plugins: github (will fail) and loki (should succeed)
        config = _make_config({"github", "loki"})
        mgr = PluginManager(config)

        real_import = importlib.import_module

        def patched_import(name, *args, **kwargs):
            if name == "buoy.plugins.builtin.github":
                raise ImportError("simulated import failure")
            return real_import(name, *args, **kwargs)

        with patch("buoy.plugins.loader.importlib.import_module", side_effect=patched_import):
            await mgr._load_builtins()

        assert "github" not in mgr._plugins
        assert "loki" in mgr._plugins

    @pytest.mark.asyncio
    async def test_safe_collect_isolates_collect_error(self):
        """A collect() error is stored as PanelData(status='error') and does not raise."""
        from buoy.plugins.builtin.github import GitHubPlugin

        plugin = GitHubPlugin()
        plugin.configure({"token": "test"})

        config = _make_config({"github"})
        mgr = PluginManager(config)

        with patch.object(plugin, "collect", side_effect=RuntimeError("boom")):
            await mgr._safe_collect("github", plugin)

        assert mgr._latest_data["github"].status == "error"
        assert "boom" in mgr._latest_data["github"].detail.get("error", "")
