"""Tests for the Grafana / Alertmanager firing-alerts plugin."""

import json
import ssl
from unittest.mock import MagicMock, patch

import pytest


def _make_alert(
    alertname, severity=None, instance="node-1", state="active", starts_at="2026-01-01T00:00:00Z"
):
    labels = {"alertname": alertname, "instance": instance}
    if severity is not None:
        labels["severity"] = severity
    return {
        "labels": labels,
        "annotations": {"summary": f"{alertname} summary"},
        "startsAt": starts_at,
        "status": {"state": state},
        "generatorURL": f"http://example.com/{alertname}",
    }


def _mock_urlopen(payload):
    cm = MagicMock()
    cm.__enter__ = lambda s: MagicMock(read=lambda: json.dumps(payload).encode())
    cm.__exit__ = lambda s, *a: None
    return cm


class TestGrafanaAlertsPlugin:
    def _make_plugin(self, config=None):
        from buoy.plugins.builtin.grafana_alerts import GrafanaAlertsPlugin

        plugin = GrafanaAlertsPlugin()
        plugin.configure(
            config if config is not None else {"type": "alertmanager", "url": "http://am:9093"}
        )
        return plugin

    @pytest.mark.asyncio
    async def test_no_url_returns_disabled(self):
        plugin = self._make_plugin({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_critical_alert_is_error(self):
        plugin = self._make_plugin()
        payload = [_make_alert("HighCPU", severity="critical")]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "error"
        assert "critical" in result.summary
        assert result.detail["by_severity"]["critical"] == 1

    @pytest.mark.asyncio
    async def test_warning_only_is_warn(self):
        plugin = self._make_plugin()
        payload = [_make_alert("DiskLow", severity="warning")]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["by_severity"]["warning"] == 1

    @pytest.mark.asyncio
    async def test_no_alerts_is_ok(self):
        plugin = self._make_plugin()
        with patch("urllib.request.urlopen", return_value=_mock_urlopen([])):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "No firing alerts" in result.summary

    @pytest.mark.asyncio
    async def test_grafana_type_uses_grafana_alertmanager_path(self):
        plugin = self._make_plugin({"type": "grafana", "url": "http://grafana", "token": "tok"})
        captured = {}

        def fake_urlopen(req, timeout=8, context=None):
            captured["req"] = req
            return _mock_urlopen([])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            await plugin.collect()

        assert "/api/alertmanager/grafana/api/v2/alerts" in captured["req"].full_url

    @pytest.mark.asyncio
    async def test_unknown_type_returns_error(self):
        plugin = self._make_plugin({"type": "bogus", "url": "http://am:9093"})
        result = await plugin.collect()
        assert result.status == "error"
        assert "Unknown type" in result.summary

    @pytest.mark.asyncio
    async def test_token_sets_bearer_header(self):
        plugin = self._make_plugin(
            {"type": "alertmanager", "url": "http://am:9093", "token": "abc123"}
        )
        captured = {}

        def fake_urlopen(req, timeout=8, context=None):
            captured["req"] = req
            return _mock_urlopen([])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            await plugin.collect()

        assert captured["req"].headers["Authorization"] == "Bearer abc123"

    @pytest.mark.asyncio
    async def test_silenced_alerts_excluded_by_default(self):
        plugin = self._make_plugin()
        payload = [_make_alert("Silenced", severity="warning", state="suppressed")]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["total"] == 0

    @pytest.mark.asyncio
    async def test_include_silenced_counts_them(self):
        plugin = self._make_plugin(
            {"type": "alertmanager", "url": "http://am:9093", "include_silenced": True}
        )
        payload = [_make_alert("Silenced", severity="warning", state="suppressed")]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["total"] == 1

    @pytest.mark.asyncio
    async def test_missing_severity_label_buckets_as_unknown_and_warns(self):
        plugin = self._make_plugin()
        payload = [_make_alert("NoSeverity")]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["by_severity"]["unknown"] == 1

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin()
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()

        assert result.status == "error"
        assert "Unreachable" in result.summary

    @pytest.mark.asyncio
    async def test_verify_ssl_false_passes_permissive_context(self):
        plugin = self._make_plugin(
            {"type": "alertmanager", "url": "http://am:9093", "verify_ssl": False}
        )
        captured = {}

        def fake_urlopen(req, timeout=8, context=None):
            captured["context"] = context
            return _mock_urlopen([])

        with patch("urllib.request.urlopen", side_effect=fake_urlopen):
            await plugin.collect()

        assert captured["context"].verify_mode == ssl.CERT_NONE

    @pytest.mark.asyncio
    async def test_render_produces_badges_and_list(self):
        plugin = self._make_plugin()
        payload = [_make_alert("HighCPU", severity="critical")]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "badges"
        assert blocks[1]["type"] == "list"
        assert blocks[1]["items"][0]["primary"] == "HighCPU"

    def test_render_empty_shows_text(self):
        from buoy.plugins.protocol import PanelData

        plugin = self._make_plugin()
        data = PanelData(
            status="ok",
            summary="No firing alerts",
            detail={"total": 0, "by_severity": {}, "alerts": []},
        )
        blocks = plugin.render(data)
        assert blocks == [{"type": "text", "value": "No firing alerts", "status": "dim"}]

    @pytest.mark.asyncio
    async def test_alert_list_capped_at_max_listed_but_counts_are_total(self):
        plugin = self._make_plugin()
        payload = [_make_alert(f"Alert{i}", severity="warning") for i in range(15)]
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(payload)):
            result = await plugin.collect()

        assert result.detail["total"] == 15
        assert result.detail["by_severity"]["warning"] == 15
        assert len(result.detail["alerts"]) == 10

    def test_demo_data_is_not_error(self):
        plugin = self._make_plugin()
        data = plugin.demo_data()
        assert data.status != "error"
        assert data.summary
        assert plugin.render(data) is not None
