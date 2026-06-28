"""Tests for the Trigger.dev task run status plugin."""

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest


def _now_iso():
    return datetime.now(tz=UTC).isoformat()


def _ago_iso(hours):
    return (datetime.now(tz=UTC) - timedelta(hours=hours)).isoformat()


def _make_run(task_id, status, finished_at=None):
    return {
        "id": f"run_{task_id}",
        "taskIdentifier": task_id,
        "status": status,
        "finishedAt": finished_at,
        "createdAt": _now_iso(),
    }


def _mock_urlopen(payload):
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: json.dumps(payload).encode())
    cm.__exit__ = lambda s, *a: None
    return cm


class TestTriggerDevPlugin:
    def _make_plugin(self, config=None):
        from buoy.plugins.builtin.trigger_dev import TriggerDevPlugin

        plugin = TriggerDevPlugin()
        plugin.configure(
            config
            if config is not None
            else {"url": "https://api.trigger.dev", "api_key": "tr_test", "project_ref": "proj_1"}
        )
        return plugin

    @pytest.mark.asyncio
    async def test_no_config_returns_disabled(self):
        from buoy.plugins.builtin.trigger_dev import TriggerDevPlugin

        plugin = TriggerDevPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_no_failures_ok(self):
        plugin = self._make_plugin()
        payload = {
            "data": [
                _make_run("task-a", "COMPLETED", _ago_iso(1)),
                _make_run("task-b", "COMPLETED", _ago_iso(2)),
            ]
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "OK" in result.summary
        assert result.detail["failures_24h"] == 0
        assert result.detail["last_failed"] is None

    @pytest.mark.asyncio
    async def test_failures_warn(self):
        plugin = self._make_plugin()
        payload = {
            "data": [
                _make_run("task-ok", "COMPLETED", _ago_iso(1)),
                _make_run("task-bad", "FAILED", _ago_iso(2)),
            ]
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "failed" in result.summary
        assert result.detail["failures_24h"] == 1
        assert result.detail["last_failed"] == "task-bad"

    @pytest.mark.asyncio
    async def test_old_failure_not_counted(self):
        """A failure older than 24h should not trigger warn."""
        plugin = self._make_plugin()
        payload = {
            "data": [
                _make_run("task-old", "FAILED", _ago_iso(25)),
            ]
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["failures_24h"] == 0

    @pytest.mark.asyncio
    async def test_queue_backing_up_error(self):
        plugin = self._make_plugin(
            {"url": "https://api.trigger.dev", "api_key": "tr_test", "project_ref": "p", "queue_warn_threshold": 2}
        )
        payload = {
            "data": [_make_run(f"task-{i}", "QUEUED") for i in range(3)]
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Queue" in result.summary
        assert result.detail["queued"] == 3

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin()
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    def test_has_frontend_js(self):
        plugin = self._make_plugin()
        js = plugin.frontend_js()
        assert js is not None
        assert "render_trigger_dev" in js
