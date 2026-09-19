"""Tests for the ban_status built-in plugin (Fail2ban / CrowdSec)."""

import json
from unittest.mock import AsyncMock, patch

import pytest


class TestBanStatusPlugin:
    def _make_plugin(self, **config):
        from buoy.plugins.builtin.ban_status import BanStatusPlugin

        plugin = BanStatusPlugin()
        plugin.configure(config)
        return plugin

    @staticmethod
    def _make_proc(output: bytes, returncode: int = 0):
        mock = AsyncMock()
        mock.communicate = AsyncMock(return_value=(output, b""))
        mock.returncode = returncode
        return mock

    # ------------------------------------------------------------------
    # fail2ban
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_jails_and_bans_parsed(self):
        plugin = self._make_plugin(type="fail2ban")

        status_output = b"Status\n|- Number of jail:\t2\n`- Jail list:\tsshd, nginx-http-auth\n"
        sshd_output = (
            b"Status for the jail: sshd\n"
            b"|- Filter\n"
            b"|  |- Currently failed:\t2\n"
            b"|  `- Total failed:\t20\n"
            b"`- Actions\n"
            b"   |- Currently banned:\t2\n"
            b"   `- Total banned:\t10\n"
        )
        nginx_output = (
            b"Status for the jail: nginx-http-auth\n"
            b"|- Filter\n"
            b"|  |- Currently failed:\t1\n"
            b"|  `- Total failed:\t5\n"
            b"`- Actions\n"
            b"   |- Currently banned:\t3\n"
            b"   `- Total banned:\t8\n"
        )

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[
                self._make_proc(status_output),
                self._make_proc(sshd_output),
                self._make_proc(nginx_output),
            ],
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["total_banned"] == 5
        assert "5 banned · 2 jails" in result.summary
        assert len(result.detail["jails"]) == 2

    @pytest.mark.asyncio
    async def test_banned_ip_list_parsed(self):
        plugin = self._make_plugin(type="fail2ban")

        status_output = b"`- Jail list:\tsshd\n"
        sshd_output = (
            b"|- Currently failed:\t0\n"
            b"|- Total failed:\t3\n"
            b"|- Currently banned:\t2\n"
            b"|- Total banned:\t2\n"
            b"`- Banned IP list:\t203.0.113.42 198.51.100.7\n"
        )

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(status_output), self._make_proc(sshd_output)],
        ):
            result = await plugin.collect()

        offenders = {o["ip"] for o in result.detail["top_offenders"]}
        assert offenders == {"203.0.113.42", "198.51.100.7"}

    @pytest.mark.asyncio
    async def test_top_offenders_ranks_by_jail_count(self):
        plugin = self._make_plugin(type="fail2ban")

        status_output = b"`- Jail list:\tsshd, nginx-http-auth\n"
        sshd_output = (
            b"|- Currently banned:\t2\n"
            b"|- Total banned:\t2\n"
            b"`- Banned IP list:\t203.0.113.42 198.51.100.7\n"
        )
        nginx_output = (
            b"|- Currently banned:\t1\n|- Total banned:\t1\n`- Banned IP list:\t203.0.113.42\n"
        )

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[
                self._make_proc(status_output),
                self._make_proc(sshd_output),
                self._make_proc(nginx_output),
            ],
        ):
            result = await plugin.collect()

        top = result.detail["top_offenders"]
        assert top[0]["ip"] == "203.0.113.42"
        assert top[0]["count"] == 2
        assert top[1]["ip"] == "198.51.100.7"
        assert top[1]["count"] == 1

    @pytest.mark.asyncio
    async def test_no_banned_ip_list_line(self):
        plugin = self._make_plugin(type="fail2ban")

        status_output = b"`- Jail list:\tsshd\n"
        sshd_output = b"|- Currently failed:\t0\n|- Currently banned:\t3\n|- Total banned:\t3\n"

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(status_output), self._make_proc(sshd_output)],
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["total_banned"] == 3
        assert result.detail["top_offenders"] == []

    @pytest.mark.asyncio
    async def test_warn_threshold(self):
        plugin = self._make_plugin(type="fail2ban", warn_threshold=2)

        status_output = b"`- Jail list:\tsshd\n"
        sshd_output = b"|- Currently banned:\t5\n|- Total banned:\t5\n"

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(status_output), self._make_proc(sshd_output)],
        ):
            result = await plugin.collect()

        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_empty_jail_list(self):
        plugin = self._make_plugin(type="fail2ban")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(b"`- Jail list:\t\n")],
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["total_banned"] == 0
        assert result.detail["jails"] == []

    @pytest.mark.asyncio
    async def test_client_not_installed(self):
        plugin = self._make_plugin(type="fail2ban")

        with patch(
            "asyncio.create_subprocess_exec", side_effect=FileNotFoundError("fail2ban-client")
        ):
            result = await plugin.collect()

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_nonzero_returncode(self):
        plugin = self._make_plugin(type="fail2ban")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(b"", returncode=1)],
        ):
            result = await plugin.collect()

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_timeout(self):
        plugin = self._make_plugin(type="fail2ban")

        with patch("asyncio.create_subprocess_exec", side_effect=TimeoutError()):
            result = await plugin.collect()

        assert result.status == "error"

    # ------------------------------------------------------------------
    # CrowdSec
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_decisions_parsed(self):
        plugin = self._make_plugin(type="crowdsec")

        payload = [
            {
                "source": {"ip": "203.0.113.5"},
                "decisions": [
                    {
                        "value": "203.0.113.5",
                        "scenario": "crowdsecurity/ssh-bf",
                        "type": "ban",
                        "duration": "3h59m",
                    }
                ],
            }
        ]
        output = json.dumps(payload).encode()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(output)],
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["total_banned"] == 1
        assert result.detail["decisions"][0]["ip"] == "203.0.113.5"
        assert result.detail["top_offenders"][0]["ip"] == "203.0.113.5"

    @pytest.mark.asyncio
    async def test_flat_decision_list(self):
        plugin = self._make_plugin(type="crowdsec")

        payload = [
            {
                "value": "198.51.100.7",
                "scenario": "crowdsecurity/http-probing",
                "type": "ban",
                "duration": "4h",
            }
        ]
        output = json.dumps(payload).encode()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(output)],
        ):
            result = await plugin.collect()

        assert result.detail["total_banned"] == 1
        assert result.detail["decisions"][0]["ip"] == "198.51.100.7"

    @pytest.mark.asyncio
    async def test_null_json_output(self):
        plugin = self._make_plugin(type="crowdsec")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(b"null\n")],
        ):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["total_banned"] == 0

    @pytest.mark.asyncio
    async def test_malformed_json(self):
        plugin = self._make_plugin(type="crowdsec")

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(b"not json")],
        ):
            result = await plugin.collect()

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_max_decisions_cap(self):
        plugin = self._make_plugin(type="crowdsec", max_decisions=5)

        payload = [
            {"value": f"203.0.113.{i}", "scenario": "s", "type": "ban", "duration": "1h"}
            for i in range(50)
        ]
        output = json.dumps(payload).encode()

        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(output)],
        ):
            result = await plugin.collect()

        assert result.detail["total_banned"] == 50
        assert len(result.detail["decisions"]) == 5

    # ------------------------------------------------------------------
    # Render / shared
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_render_fail2ban_blocks(self):
        plugin = self._make_plugin(type="fail2ban")

        status_output = b"`- Jail list:\tsshd\n"
        sshd_output = (
            b"|- Currently banned:\t2\n"
            b"|- Total banned:\t2\n"
            b"`- Banned IP list:\t203.0.113.42 198.51.100.7\n"
        )
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=[self._make_proc(status_output), self._make_proc(sshd_output)],
        ):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "keyvalue"
        assert blocks[1]["type"] == "table"

    def test_render_crowdsec_ip_cells_mono(self):
        from buoy.plugins.builtin.ban_status import BanStatusPlugin
        from buoy.plugins.protocol import PanelData

        plugin = BanStatusPlugin()
        data = PanelData(
            status="ok",
            summary="1 active decisions",
            detail={
                "backend": "crowdsec",
                "total_banned": 1,
                "decisions": [
                    {"ip": "203.0.113.5", "scenario": "ssh-bf", "type": "ban", "duration": "3h"}
                ],
                "top_offenders": [{"ip": "203.0.113.5", "count": 1}],
            },
        )
        blocks = plugin.render(data)
        table = blocks[1]
        assert table["type"] == "table"
        assert table["rows"][0][0]["mono"] is True

    def test_render_empty_shows_text(self):
        from buoy.plugins.builtin.ban_status import BanStatusPlugin
        from buoy.plugins.protocol import PanelData

        plugin = BanStatusPlugin()
        data = PanelData(
            status="ok", summary="0 banned", detail={"backend": "fail2ban", "jails": []}
        )
        assert plugin.render(data) == [{"type": "text", "value": "No active bans", "status": "dim"}]

    @pytest.mark.asyncio
    async def test_unknown_type(self):
        plugin = self._make_plugin(type="bogus")
        result = await plugin.collect()
        assert result.status == "error"
        assert "bogus" in result.summary

    def test_demo_data_renders(self):
        from buoy.plugins.builtin.ban_status import BanStatusPlugin

        plugin = BanStatusPlugin()
        data = plugin.demo_data()
        assert data.status != "error"
        blocks = plugin.render(data)
        assert blocks is not None
