"""Tests for the Proxmox VE node + guest status plugin."""

import json
from unittest.mock import MagicMock, patch

import pytest


class TestProxmoxPlugin:
    def _make_plugin(self, **kwargs):
        from buoy.plugins.builtin.proxmox import ProxmoxPlugin

        plugin = ProxmoxPlugin()
        config = {
            "url": "https://pve.example.com:8006",
            "token_id": "user@pam!mytoken",
            "token_secret": "secret",
            "node": "pve",
        }
        config.update(kwargs)
        plugin.configure(config)
        return plugin

    def _mock_urlopen(self, node_status_data, vms_data, cts_data):
        responses = [
            json.dumps({"data": node_status_data}).encode(),
            json.dumps({"data": vms_data}).encode(),
            json.dumps({"data": cts_data}).encode(),
        ]
        call_count = [0]

        def mock_urlopen(req, timeout=None, context=None, **kwargs):
            cm = MagicMock()
            payload = responses[call_count[0]]
            cm.__enter__ = lambda s: MagicMock(read=lambda: payload)
            cm.__exit__ = lambda s, *a: None
            call_count[0] += 1
            return cm

        return mock_urlopen

    @pytest.mark.asyncio
    async def test_missing_config_returns_disabled(self):
        from buoy.plugins.builtin.proxmox import ProxmoxPlugin

        plugin = ProxmoxPlugin()
        plugin.configure({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_successful_collect_ok(self):
        plugin = self._make_plugin()

        node_status = {
            "cpu": 0.12,
            "memory": {"used": 4 * 2**30, "total": 32 * 2**30},
            "uptime": 3600,
        }
        vms = [
            {"vmid": 100, "name": "vm-web", "status": "running"},
            {"vmid": 101, "name": "vm-db", "status": "running"},
        ]
        cts = [{"vmid": 200, "name": "ct-proxy", "status": "running"}]

        with patch(
            "urllib.request.urlopen",
            side_effect=self._mock_urlopen(node_status, vms, cts),
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "2/2 VMs" in result.summary
        assert "1/1 CTs" in result.summary
        assert "CPU 12%" in result.summary
        assert result.detail["node"]["cpu_pct"] == 12
        assert len(result.detail["vms"]) == 2
        assert len(result.detail["cts"]) == 1

    @pytest.mark.asyncio
    async def test_stopped_guest_returns_warn(self):
        plugin = self._make_plugin()

        node_status = {
            "cpu": 0.05,
            "memory": {"used": 2 * 2**30, "total": 16 * 2**30},
            "uptime": 7200,
        }
        vms = [
            {"vmid": 100, "name": "vm-web", "status": "running"},
            {"vmid": 101, "name": "vm-db", "status": "stopped"},
        ]
        cts = []

        with patch(
            "urllib.request.urlopen",
            side_effect=self._mock_urlopen(node_status, vms, cts),
        ):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "1/2 VMs" in result.summary

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
        assert "render_proxmox" in js
