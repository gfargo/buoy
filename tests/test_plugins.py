"""Tests for Buoy built-in plugins.

Each plugin's collect() method is tested by mocking external calls
(HTTP APIs, subprocess, filesystem) and verifying the PanelData output.
"""

import importlib
import json
import time
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

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_github" in js


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

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        assert "render_loki" in plugin.frontend_js()


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

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        assert "render_uptime_kuma" in plugin.frontend_js()


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

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        assert "render_plane" in plugin.frontend_js()


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

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert "render_cron_health" in js
        assert "<table" in js


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

    def test_manifest(self):
        plugin = self._make_plugin()
        assert plugin.manifest.id == "prometheus_exporter"
        assert plugin.manifest.refresh_interval == 9999  # Pulled on demand


# =============================================================================
# Speedtest plugin
# =============================================================================


class TestSpeedtestPlugin:
    """Tests for the internet speedtest tracker plugin."""

    def _make_plugin(self, config=None):
        from buoy.plugins.builtin.speedtest import SpeedtestPlugin

        plugin = SpeedtestPlugin()
        plugin.configure(config or {})
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
        plugin = self._make_plugin()
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

        with patch("asyncio.create_subprocess_exec", return_value=mock_proc):
            entry = await plugin._run_test()

        assert entry["ok"] is True
        assert abs(entry["download_mbps"] - 480.0) < 0.1
        assert abs(entry["upload_mbps"] - 22.0) < 0.1
        assert entry["ping_ms"] == 12.5
        assert entry["server"] == "Test Server"

    @pytest.mark.asyncio
    async def test_run_test_binary_missing(self):
        plugin = self._make_plugin()
        with patch(
            "asyncio.create_subprocess_exec", side_effect=FileNotFoundError("speedtest-cli")
        ):
            entry = await plugin._run_test()
        assert entry["ok"] is False
        assert "not found" in entry.get("error", "")

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_speedtest" in js


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

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_journal_errors" in js


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

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_actual_budget" in js


# =============================================================================
# Plugin discovery
# =============================================================================

ALL_BUILTIN_IDS = {
    "actual_budget",
    "backup_status",
    "cron_health",
    "github",
    "journal_errors",
    "loki",
    "plane",
    "prometheus_exporter",
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
