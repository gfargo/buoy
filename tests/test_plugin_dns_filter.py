"""Tests for the DNS Filter plugin (Pi-hole v5/v6 / AdGuard Home)."""

import json
from unittest.mock import MagicMock, patch

import pytest


def _mock_urlopen(response_data: dict, status: int = 200) -> MagicMock:
    """Return a context-manager mock yielding a single JSON response."""
    payload = json.dumps(response_data).encode()
    mock_resp = MagicMock()
    mock_resp.read = lambda: payload
    mock_resp.status = status
    mock_cm = MagicMock()
    mock_cm.__enter__ = lambda s: mock_resp
    mock_cm.__exit__ = lambda s, *a: None
    return mock_cm


def _mock_urlopen_sequence(responses: list[dict | tuple]) -> list[MagicMock]:
    """Build a side_effect list for sequential urlopen calls.

    Each item in `responses` can be:
    - a dict → JSON response with status 200
    - a (dict, int) tuple → JSON response with given status
    """
    result = []
    for item in responses:
        if isinstance(item, tuple):
            data, status = item
        else:
            data, status = item, 200
        result.append(_mock_urlopen(data, status))
    return result


class TestDnsFilterPlugin:
    def _make_plugin(self, config: dict):
        from buoy.plugins.builtin.dns_filter import DnsFilterPlugin

        plugin = DnsFilterPlugin()
        plugin.configure(config)
        return plugin

    # ------------------------------------------------------------------
    # Basic / shared
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_not_configured_returns_disabled(self):
        plugin = self._make_plugin({})
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    # ------------------------------------------------------------------
    # Pi-hole v5 (legacy API)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pihole_v5_explicit_ok(self):
        """type=pihole, version=5 skips detection and hits legacy API."""
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole", "version": "5"})
        data = {
            "dns_queries_today": 10000,
            "ads_blocked_today": 1200,
            "ads_percentage_today": 12.0,
            "status": "enabled",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "ok"
        assert "10,000" in result.summary
        assert "12.0%" in result.summary
        assert result.detail["queries"] == 10000
        assert result.detail["blocked"] == 1200

    @pytest.mark.asyncio
    async def test_pihole_warn_when_blocked_over_threshold(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole", "version": "5"})
        data = {
            "dns_queries_today": 1000,
            "ads_blocked_today": 300,
            "ads_percentage_today": 30.0,
            "status": "enabled",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "warn"
        assert result.detail["pct"] == 30.0

    @pytest.mark.asyncio
    async def test_pihole_disabled_returns_error(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole", "version": "5"})
        data = {
            "dns_queries_today": 0,
            "ads_blocked_today": 0,
            "ads_percentage_today": 0.0,
            "status": "disabled",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "error"
        assert "disabled" in result.summary.lower()

    # ------------------------------------------------------------------
    # Pi-hole v6 (REST API with session auth)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pihole_v6_explicit_ok(self):
        """version=6 skips detection; auth + summary + top_domains succeed."""
        plugin = self._make_plugin(
            {"type": "pihole", "url": "http://pi.hole", "version": "6", "password": "secret"}
        )
        auth_resp = {"session": {"valid": True, "sid": "abc123", "csrf": "x", "validity": 300}}
        summary_resp = {"queries": {"total": 20000, "blocked": 4000, "percent_blocked": 20.0}}
        top_resp = {
            "domains": [
                {"domain": "ads.example.com", "count": 500},
                {"domain": "track.io", "count": 300},
            ],
            "total_queries": 20000,
            "blocked_queries": 4000,
        }
        blocking_resp = {"blocking": "enabled"}
        # logout DELETE is best-effort (returns mock cm too)
        logout_resp = {}
        side_effects = _mock_urlopen_sequence(
            [auth_resp, summary_resp, top_resp, blocking_resp, logout_resp]
        )
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "20,000" in result.summary
        assert "20.0%" in result.summary
        assert result.detail["queries"] == 20000
        assert result.detail["blocked"] == 4000
        assert result.detail["pct"] == 20.0
        assert len(result.detail["top_blocked"]) == 2
        assert result.detail["top_blocked"][0] == {"domain": "ads.example.com", "count": 500}

    @pytest.mark.asyncio
    async def test_pihole_v6_no_password(self):
        """Empty password is valid for open Pi-hole v6 instances."""
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole", "version": "6"})
        auth_resp = {"session": {"valid": True, "sid": "sid_open", "csrf": "x", "validity": 300}}
        summary_resp = {"queries": {"total": 5000, "blocked": 500, "percent_blocked": 10.0}}
        top_resp = {"domains": [], "total_queries": 5000, "blocked_queries": 500}
        blocking_resp = {"blocking": "enabled"}
        logout_resp = {}
        side_effects = _mock_urlopen_sequence(
            [auth_resp, summary_resp, top_resp, blocking_resp, logout_resp]
        )
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["queries"] == 5000

    @pytest.mark.asyncio
    async def test_pihole_v6_auth_failure_invalid_session(self):
        """session.valid=false → error status."""
        plugin = self._make_plugin(
            {"type": "pihole", "url": "http://pi.hole", "version": "6", "password": "wrong"}
        )
        auth_resp = {"session": {"valid": False, "sid": None}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(auth_resp)):
            result = await plugin.collect()

        assert result.status == "error"
        assert "auth failed" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_pihole_v6_auth_failure_401(self):
        """HTTP 401 on /api/auth → error status."""
        import urllib.error

        plugin = self._make_plugin(
            {"type": "pihole", "url": "http://pi.hole", "version": "6", "password": "bad"}
        )
        err_401 = urllib.error.HTTPError(
            url="http://pi.hole/api/auth", code=401, msg="Unauthorized", hdrs=None, fp=None
        )
        with patch("urllib.request.urlopen", side_effect=err_401):
            result = await plugin.collect()

        assert result.status == "error"
        assert "auth failed" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_pihole_v6_warn_threshold(self):
        """pct > 25 → warn for v6."""
        plugin = self._make_plugin(
            {"type": "pihole", "url": "http://pi.hole", "version": "6", "password": "pw"}
        )
        auth_resp = {"session": {"valid": True, "sid": "s1", "csrf": "x", "validity": 300}}
        summary_resp = {"queries": {"total": 1000, "blocked": 300, "percent_blocked": 30.0}}
        top_resp = {"domains": [], "total_queries": 1000, "blocked_queries": 300}
        blocking_resp = {"blocking": "enabled"}
        logout_resp = {}
        side_effects = _mock_urlopen_sequence(
            [auth_resp, summary_resp, top_resp, blocking_resp, logout_resp]
        )
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "warn"
        assert result.detail["pct"] == 30.0

    @pytest.mark.asyncio
    async def test_pihole_v6_disabled_returns_error(self):
        """blocking != "enabled" from /api/dns/blocking → error status."""
        plugin = self._make_plugin(
            {"type": "pihole", "url": "http://pi.hole", "version": "6", "password": "pw"}
        )
        auth_resp = {"session": {"valid": True, "sid": "s_disabled", "csrf": "x", "validity": 300}}
        summary_resp = {"queries": {"total": 1000, "blocked": 0, "percent_blocked": 0.0}}
        top_resp = {"domains": [], "total_queries": 1000, "blocked_queries": 0}
        blocking_resp = {"blocking": "disabled"}
        logout_resp = {}
        side_effects = _mock_urlopen_sequence(
            [auth_resp, summary_resp, top_resp, blocking_resp, logout_resp]
        )
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "error"
        assert "disabled" in result.summary.lower()

    @pytest.mark.asyncio
    async def test_pihole_v6_top_domains_best_effort(self):
        """top_domains call failing does not break collect."""
        plugin = self._make_plugin(
            {"type": "pihole", "url": "http://pi.hole", "version": "6", "password": "pw"}
        )
        auth_resp = {"session": {"valid": True, "sid": "s2", "csrf": "x", "validity": 300}}
        summary_resp = {"queries": {"total": 8000, "blocked": 800, "percent_blocked": 10.0}}
        blocking_resp = {"blocking": "enabled"}
        logout_resp = {}

        def _side_effect(req, *args, **kwargs):
            # auth call → summary call → top_domains raises → blocking → logout
            call_count = getattr(_side_effect, "_n", 0)
            _side_effect._n = call_count + 1
            if call_count == 0:
                return _mock_urlopen(auth_resp)
            elif call_count == 1:
                return _mock_urlopen(summary_resp)
            elif call_count == 2:
                raise Exception("top_domains unavailable")
            elif call_count == 3:
                return _mock_urlopen(blocking_resp)
            else:
                return _mock_urlopen(logout_resp)

        with patch("urllib.request.urlopen", side_effect=_side_effect):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["top_blocked"] == []

    # ------------------------------------------------------------------
    # Auto-detection
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_pihole_autodetect_v6_when_probe_returns_200(self):
        """Version probe returns 200 → v6 path used."""
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole"})
        probe_resp = {"version": "6.0"}
        auth_resp = {
            "session": {"valid": True, "sid": "sid_detected", "csrf": "x", "validity": 300}
        }
        summary_resp = {"queries": {"total": 12000, "blocked": 2400, "percent_blocked": 20.0}}
        top_resp = {"domains": [], "total_queries": 12000, "blocked_queries": 2400}
        blocking_resp = {"blocking": "enabled"}
        logout_resp = {}
        side_effects = _mock_urlopen_sequence(
            [probe_resp, auth_resp, summary_resp, top_resp, blocking_resp, logout_resp]
        )
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["queries"] == 12000

    @pytest.mark.asyncio
    async def test_pihole_autodetect_falls_back_to_v5_on_404(self):
        """Version probe raises 404 → v5 legacy path used."""
        import urllib.error

        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole"})
        probe_404 = urllib.error.HTTPError(
            url="http://pi.hole/api/info/version", code=404, msg="Not Found", hdrs=None, fp=None
        )
        v5_data = {
            "dns_queries_today": 10000,
            "ads_blocked_today": 1200,
            "ads_percentage_today": 12.0,
            "status": "enabled",
        }
        with patch("urllib.request.urlopen", side_effect=[probe_404, _mock_urlopen(v5_data)]):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["queries"] == 10000

    @pytest.mark.asyncio
    async def test_pihole_autodetect_v6_on_401_probe(self):
        """Version probe returning 401 (auth required) is treated as v6."""
        import urllib.error

        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole", "password": "pw"})
        probe_401 = urllib.error.HTTPError(
            url="http://pi.hole/api/info/version", code=401, msg="Unauthorized", hdrs=None, fp=None
        )
        auth_resp = {"session": {"valid": True, "sid": "sid_pw", "csrf": "x", "validity": 300}}
        summary_resp = {"queries": {"total": 7000, "blocked": 700, "percent_blocked": 10.0}}
        top_resp = {"domains": [], "total_queries": 7000, "blocked_queries": 700}
        blocking_resp = {"blocking": "enabled"}
        logout_resp = {}
        side_effects = [probe_401] + _mock_urlopen_sequence(
            [auth_resp, summary_resp, top_resp, blocking_resp, logout_resp]
        )
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["queries"] == 7000

    # ------------------------------------------------------------------
    # AdGuard Home
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_adguard_ok(self):
        plugin = self._make_plugin(
            {
                "type": "adguard",
                "url": "http://adguard:3000",
                "username": "admin",
                "password": "secret",
            }
        )
        data = {
            "num_dns_queries": 5000,
            "num_blocked_filtering": 500,
            "top_blocked_domains": {"ads.example.com": 120, "tracker.io": 80},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "ok"
        assert result.detail["queries"] == 5000
        assert result.detail["pct"] == 10.0
        assert len(result.detail["top_blocked"]) == 2

    @pytest.mark.asyncio
    async def test_adguard_zero_queries_no_division_error(self):
        plugin = self._make_plugin({"type": "adguard", "url": "http://adguard:3000"})
        data = {"num_dns_queries": 0, "num_blocked_filtering": 0, "top_blocked_domains": {}}
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()
        assert result.status == "ok"
        assert result.detail["pct"] == 0.0

    # ------------------------------------------------------------------
    # Error / edge cases
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole", "version": "5"})
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()
        assert result.status == "error"
        assert "Unreachable" in result.summary

    # ------------------------------------------------------------------
    # Rendering
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_render_produces_keyvalue_and_top_blocked_list(self):
        plugin = self._make_plugin({"type": "adguard", "url": "http://adguard:3000"})
        data = {
            "num_dns_queries": 5000,
            "num_blocked_filtering": 500,
            "top_blocked_domains": {"ads.example.com": 120},
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert blocks[0]["type"] == "keyvalue"
        assert blocks[1]["type"] == "list"
        assert blocks[1]["items"][0]["primary"] == "ads.example.com"
        assert blocks[1]["items"][0]["secondary"] == "120"

    @pytest.mark.asyncio
    async def test_render_omits_list_block_when_no_top_domains(self):
        plugin = self._make_plugin({"type": "pihole", "url": "http://pi.hole", "version": "5"})
        data = {
            "dns_queries_today": 10000,
            "ads_blocked_today": 1200,
            "ads_percentage_today": 12.0,
            "status": "enabled",
        }
        with patch("urllib.request.urlopen", return_value=_mock_urlopen(data)):
            result = await plugin.collect()

        blocks = plugin.render(result)
        assert len(blocks) == 1
        assert blocks[0]["type"] == "keyvalue"
