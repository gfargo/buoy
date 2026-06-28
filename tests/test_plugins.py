"""Tests for Buoy built-in plugins.

Each plugin's collect() method is tested by mocking external calls
(HTTP APIs, subprocess, filesystem) and verifying the PanelData output.
"""

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

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
# Trigger.dev plugin
# =============================================================================


class TestTriggerDevPlugin:
    """Tests for the Trigger.dev task run status plugin."""

    def _make_plugin(self, **cfg):
        from buoy.plugins.builtin.trigger_dev import TriggerDevPlugin

        plugin = TriggerDevPlugin()
        plugin.configure({"api_key": "tr_dev_test", "url": "http://trigger.local", **cfg})
        return plugin

    @pytest.mark.asyncio
    async def test_no_api_key_returns_disabled(self):
        from buoy.plugins.builtin.trigger_dev import TriggerDevPlugin

        plugin = TriggerDevPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_failures_present_warn(self):
        plugin = self._make_plugin()

        call_count = [0]
        failed_resp = json.dumps(
            {
                "data": [
                    {
                        "id": "run_1",
                        "taskIdentifier": "send-email",
                        "createdAt": "2026-06-28T01:00:00Z",
                    },
                    {
                        "id": "run_2",
                        "taskIdentifier": "process-payment",
                        "createdAt": "2026-06-28T00:00:00Z",
                    },
                ]
            }
        ).encode()
        queued_resp = json.dumps({"data": []}).encode()

        def mock_urlopen(req, timeout=None):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: failed_resp)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: queued_resp)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "2 failed" in result.summary
        assert result.detail["last_failed_task"] == "send-email"
        assert result.detail["failed_count"] == 2
        assert len(result.detail["recent_failures"]) == 2

    @pytest.mark.asyncio
    async def test_no_failures_ok(self):
        plugin = self._make_plugin()

        empty = json.dumps({"data": []}).encode()

        def mock_urlopen(req, timeout=None):
            cm = MagicMock()
            cm.__enter__ = lambda s: MagicMock(read=lambda: empty)
            cm.__exit__ = lambda s, *a: None
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "All clear" in result.summary
        assert result.detail["failed_count"] == 0
        assert result.detail["queue_depth"] == 0

    @pytest.mark.asyncio
    async def test_queue_backing_up_error(self):
        plugin = self._make_plugin(queue_threshold=5)

        no_failures = json.dumps({"data": []}).encode()
        queued_resp = json.dumps({"data": [{"id": f"run_{i}"} for i in range(10)]}).encode()
        call_count = [0]

        def mock_urlopen(req, timeout=None):
            cm = MagicMock()
            if call_count[0] == 0:
                cm.__enter__ = lambda s: MagicMock(read=lambda: no_failures)
            else:
                cm.__enter__ = lambda s: MagicMock(read=lambda: queued_resp)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        with patch("urllib.request.urlopen", side_effect=mock_urlopen):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Queue" in result.summary
        assert result.detail["queue_depth"] == 10

    @pytest.mark.asyncio
    async def test_unreachable_error(self):
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
        assert "render_trigger_dev" in js
