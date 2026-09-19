"""Tests for the Reverse Proxy plugin (Traefik / Caddy / Nginx Proxy Manager)."""

import json
import urllib.error
from unittest.mock import MagicMock, patch

import pytest


def _mock_response(data, status: int = 200, as_text: bool = False) -> MagicMock:
    """Return a context-manager mock yielding a single response."""
    payload = data.encode() if as_text else json.dumps(data).encode()
    mock_resp = MagicMock()
    mock_resp.read = lambda: payload
    mock_resp.status = status
    mock_cm = MagicMock()
    mock_cm.__enter__ = lambda s: mock_resp
    mock_cm.__exit__ = lambda s, *a: None
    return mock_cm


def _mock_sequence(responses: list) -> list[MagicMock]:
    """Build a side_effect list for sequential urlopen calls.

    Each item can be a dict (JSON, status 200), a str (raw text, status 200),
    or a (data, status, as_text) tuple.
    """
    result = []
    for item in responses:
        if isinstance(item, tuple):
            data, status, as_text = item
        elif isinstance(item, str):
            data, status, as_text = item, 200, True
        else:
            data, status, as_text = item, 200, False
        result.append(_mock_response(data, status, as_text))
    return result


def _http_error(url: str, code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(url=url, code=code, msg="error", hdrs=None, fp=None)


class TestReverseProxyPlugin:
    def _make_plugin(self, config: dict):
        from buoy.plugins.builtin.reverse_proxy import ReverseProxyPlugin

        plugin = ReverseProxyPlugin()
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

    @pytest.mark.asyncio
    async def test_unknown_type_returns_error(self):
        plugin = self._make_plugin({"type": "nope", "url": "http://proxy"})
        result = await plugin.collect()
        assert result.status == "error"
        assert "nope" in result.summary

    @pytest.mark.asyncio
    async def test_unreachable_returns_error(self):
        plugin = self._make_plugin({"type": "traefik", "url": "http://traefik:8080"})
        with patch("urllib.request.urlopen", side_effect=Exception("Connection refused")):
            result = await plugin.collect()
        assert result.status == "error"
        assert "Unreachable" in result.summary

    # ------------------------------------------------------------------
    # Traefik
    # ------------------------------------------------------------------

    def _traefik_plugin(self, **extra):
        return self._make_plugin({"type": "traefik", "url": "http://traefik:8080", **extra})

    @pytest.mark.asyncio
    async def test_traefik_healthy_returns_ok(self):
        plugin = self._traefik_plugin()
        overview = {"http": {"routers": {"total": 2, "warnings": 0, "errors": 0}}}
        routers = [
            {"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled", "tls": {}},
            {"name": "api@docker", "rule": "Host(`api.local`)", "status": "enabled", "tls": {}},
        ]
        side_effects = _mock_sequence([overview, routers])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "ok"
        assert "2 routers" in result.summary
        assert result.detail["router_count"] == 2
        assert len(result.detail["hosts"]) == 2

    @pytest.mark.asyncio
    async def test_traefik_warning_router_returns_warn(self):
        plugin = self._traefik_plugin()
        overview = {"http": {"routers": {"total": 2, "warnings": 1, "errors": 0}}}
        routers = [
            {"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled", "tls": {}},
            {"name": "bad@docker", "rule": "Host(`bad.local`)", "status": "warning"},
        ]
        side_effects = _mock_sequence([overview, routers])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "warn"
        bad_host = next(h for h in result.detail["hosts"] if h["name"] == "bad@docker")
        assert bad_host["status"] == "warn"

    @pytest.mark.asyncio
    async def test_traefik_disabled_router_returns_warn_not_error(self):
        plugin = self._traefik_plugin()
        overview = {"http": {"routers": {"total": 1, "warnings": 0, "errors": 0}}}
        routers = [{"name": "old@docker", "rule": "Host(`old.local`)", "status": "disabled"}]
        side_effects = _mock_sequence([overview, routers])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_traefik_overview_errors_returns_error(self):
        plugin = self._traefik_plugin()
        overview = {"http": {"routers": {"total": 1, "warnings": 0, "errors": 1}}}
        routers = [{"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled"}]
        side_effects = _mock_sequence([overview, routers])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "error"

    # ------------------------------------------------------------------
    # Caddy
    # ------------------------------------------------------------------

    def _caddy_plugin(self, **extra):
        return self._make_plugin({"type": "caddy", "url": "http://caddy:2019", **extra})

    @pytest.mark.asyncio
    async def test_caddy_counts_routes_across_servers(self):
        plugin = self._caddy_plugin()
        servers = {
            "srv0": {"routes": [{}, {}], "automatic_https": {}},
            "srv1": {"routes": [{}], "automatic_https": {"disable": True}},
        }
        upstreams: list = []
        side_effects = _mock_sequence([servers, upstreams])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["router_count"] == 3
        assert "3 routers" in result.summary
        names = {h["name"] for h in result.detail["hosts"]}
        assert names == {"srv0", "srv1"}

    @pytest.mark.asyncio
    async def test_caddy_upstream_fails_returns_warn(self):
        plugin = self._caddy_plugin()
        servers = {"srv0": {"routes": [{}], "automatic_https": {}}}
        upstreams = [{"address": "10.0.0.5:8080", "num_requests": 100, "fails": 3}]
        side_effects = _mock_sequence([servers, upstreams])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "upstream" in result.summary

    @pytest.mark.asyncio
    async def test_caddy_upstreams_endpoint_404_still_ok(self):
        plugin = self._caddy_plugin()
        servers = {"srv0": {"routes": [{}], "automatic_https": {}}}

        def side_effect(req, timeout=None, context=None):
            if "servers" in req.full_url:
                return _mock_response(servers)
            raise _http_error(req.full_url, 404)

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = await plugin.collect()

        assert result.status == "ok"

    # ------------------------------------------------------------------
    # NPM
    # ------------------------------------------------------------------

    def _npm_plugin(self, **extra):
        return self._make_plugin(
            {
                "type": "npm",
                "url": "http://npm:81",
                "username": "admin@example.com",
                "password": "secret",
                **extra,
            }
        )

    @pytest.mark.asyncio
    async def test_npm_makes_two_calls_and_attaches_bearer_token(self):
        plugin = self._npm_plugin()
        token_resp = {"token": "tok_123", "expires": "2099-01-01T00:00:00.000Z"}
        hosts_resp: list = []
        calls = []

        def side_effect(req, timeout=None, context=None):
            calls.append(req)
            if "tokens" in req.full_url:
                return _mock_response(token_resp)
            return _mock_response(hosts_resp)

        with patch("urllib.request.urlopen", side_effect=side_effect):
            result = await plugin.collect()

        assert len(calls) == 2
        assert calls[1].headers.get("Authorization") == "Bearer tok_123"
        assert result.status == "ok"

    @pytest.mark.asyncio
    async def test_npm_offline_host_returns_error(self):
        plugin = self._npm_plugin()
        token_resp = {"token": "tok_123", "expires": "2099-01-01T00:00:00.000Z"}
        hosts_resp = [
            {
                "domain_names": ["down.example.com"],
                "enabled": True,
                "meta": {"nginx_online": False},
            }
        ]
        side_effects = _mock_sequence([token_resp, hosts_resp])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_npm_disabled_host_returns_warn_not_error(self):
        plugin = self._npm_plugin()
        token_resp = {"token": "tok_123", "expires": "2099-01-01T00:00:00.000Z"}
        hosts_resp = [
            {
                "domain_names": ["off.example.com"],
                "enabled": False,
                "meta": {"nginx_online": True},
            }
        ]
        side_effects = _mock_sequence([token_resp, hosts_resp])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "warn"
        host = result.detail["hosts"][0]
        assert host["status"] == "warn"

    @pytest.mark.parametrize(
        "days_out,expected_status",
        [(3, "error"), (20, "warn"), (90, "ok")],
    )
    @pytest.mark.asyncio
    async def test_npm_cert_expiry_thresholds(self, days_out, expected_status):
        from datetime import UTC, datetime, timedelta

        plugin = self._npm_plugin()
        token_resp = {"token": "tok_123", "expires": "2099-01-01T00:00:00.000Z"}
        expires_on = (
            (datetime.now(UTC) + timedelta(days=days_out)).isoformat().replace("+00:00", "Z")
        )
        hosts_resp = [
            {
                "domain_names": ["app.example.com"],
                "enabled": True,
                "meta": {"nginx_online": True},
                "certificate": {"expires_on": expires_on},
            }
        ]
        side_effects = _mock_sequence([token_resp, hosts_resp])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == expected_status

    @pytest.mark.asyncio
    async def test_npm_token_request_401_returns_error_no_traceback(self):
        plugin = self._npm_plugin()
        with patch(
            "urllib.request.urlopen",
            side_effect=_http_error("http://npm:81/api/tokens", 401),
        ):
            result = await plugin.collect()

        assert result.status == "error"

    # ------------------------------------------------------------------
    # Metrics (5xx rate)
    # ------------------------------------------------------------------

    @pytest.mark.asyncio
    async def test_metrics_first_sample_reports_no_rate(self):
        plugin = self._traefik_plugin(metrics_url="http://traefik:8080/metrics")
        overview = {"http": {"routers": {"total": 1, "warnings": 0, "errors": 0}}}
        routers = [{"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled"}]
        metrics_text = (
            'traefik_service_requests_total{code="200"} 100\n'
            'traefik_service_requests_total{code="500"} 5\n'
        )
        side_effects = _mock_sequence([overview, routers, metrics_text])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.detail["error_rate"] is None
        assert "5xx" not in result.summary

    @pytest.mark.asyncio
    async def test_metrics_second_sample_reports_delta_rate(self):
        plugin = self._traefik_plugin(metrics_url="http://traefik:8080/metrics")
        overview = {"http": {"routers": {"total": 1, "warnings": 0, "errors": 0}}}
        routers = [{"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled"}]
        metrics_1 = (
            'traefik_service_requests_total{code="200"} 100\n'
            'traefik_service_requests_total{code="500"} 0\n'
        )
        metrics_2 = (
            'traefik_service_requests_total{code="200"} 150\n'
            'traefik_service_requests_total{code="500"} 10\n'
        )
        side_effects = _mock_sequence([overview, routers, metrics_1])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            await plugin.collect()

        side_effects = _mock_sequence([overview, routers, metrics_2])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        # 10 new 5xx out of 60 new total requests == 16.67%
        assert result.detail["error_rate"] == pytest.approx(16.666, rel=1e-2)
        assert "5xx" in result.summary

    @pytest.mark.asyncio
    async def test_metrics_counter_reset_treated_as_fresh_baseline(self):
        plugin = self._traefik_plugin(metrics_url="http://traefik:8080/metrics")
        overview = {"http": {"routers": {"total": 1, "warnings": 0, "errors": 0}}}
        routers = [{"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled"}]
        metrics_1 = 'traefik_service_requests_total{code="200"} 500\n'
        metrics_2 = 'traefik_service_requests_total{code="200"} 10\n'  # process restarted

        side_effects = _mock_sequence([overview, routers, metrics_1])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            await plugin.collect()

        side_effects = _mock_sequence([overview, routers, metrics_2])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.detail["error_rate"] is None

    @pytest.mark.asyncio
    async def test_metrics_ignores_counters_without_code_label(self):
        # Caddy's caddy_http_requests_total carries no code/status label (codes
        # live on a separate histogram) — those samples must not inflate the
        # denominator and silently dilute the rate toward 0%.
        plugin = self._traefik_plugin(metrics_url="http://traefik:8080/metrics")
        overview = {"http": {"routers": {"total": 1, "warnings": 0, "errors": 0}}}
        routers = [{"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled"}]
        metrics_1 = (
            'traefik_service_requests_total{code="200"} 100\n'
            'traefik_service_requests_total{code="500"} 0\n'
            'caddy_http_requests_total{server="srv0",handler="reverse_proxy"} 1000\n'
        )
        metrics_2 = (
            'traefik_service_requests_total{code="200"} 150\n'
            'traefik_service_requests_total{code="500"} 10\n'
            'caddy_http_requests_total{server="srv0",handler="reverse_proxy"} 5000\n'
        )
        side_effects = _mock_sequence([overview, routers, metrics_1])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            await plugin.collect()

        side_effects = _mock_sequence([overview, routers, metrics_2])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        # If the uncoded caddy_http_requests_total samples leaked into the
        # denominator, this would come out far below 16.67%.
        assert result.detail["error_rate"] == pytest.approx(16.666, rel=1e-2)

    @pytest.mark.asyncio
    async def test_malformed_metrics_omits_rate_but_stays_ok(self):
        plugin = self._traefik_plugin(metrics_url="http://traefik:8080/metrics")
        overview = {"http": {"routers": {"total": 1, "warnings": 0, "errors": 0}}}
        routers = [{"name": "app@docker", "rule": "Host(`app.local`)", "status": "enabled"}]
        side_effects = _mock_sequence([overview, routers, "not a valid metrics payload {{{"])
        with patch("urllib.request.urlopen", side_effect=side_effects):
            result = await plugin.collect()

        assert result.status == "ok"
        assert result.detail["error_rate"] is None

    # ------------------------------------------------------------------
    # render()
    # ------------------------------------------------------------------

    def test_render_empty_detail_returns_dim_text(self):
        from buoy.plugins.builtin.reverse_proxy import ReverseProxyPlugin
        from buoy.plugins.protocol import PanelData

        plugin = ReverseProxyPlugin()
        blocks = plugin.render(PanelData(status="ok", summary="", detail={}))
        assert blocks == [{"type": "text", "value": "No routes found", "status": "dim"}]

    def test_render_returns_expected_block_types(self):
        from buoy.plugins.builtin.reverse_proxy import ReverseProxyPlugin

        plugin = ReverseProxyPlugin()
        data = plugin.demo_data()
        blocks = plugin.render(data)
        assert [b["type"] for b in blocks] == ["keyvalue", "table"]

    def test_render_5xx_row_ok_below_warn_threshold(self):
        from buoy.plugins.builtin.reverse_proxy import ReverseProxyPlugin

        # demo_data()'s 0.4% rate is well under the 5.0% default threshold —
        # the row must not read "warn" just because the rate is nonzero.
        plugin = ReverseProxyPlugin()
        data = plugin.demo_data()
        blocks = plugin.render(data)
        rate_row = next(r for r in blocks[0]["rows"] if r["label"] == "5xx rate")
        assert rate_row["status"] == "ok"

    def test_render_5xx_row_warn_above_threshold(self):
        from buoy.plugins.builtin.reverse_proxy import ReverseProxyPlugin
        from buoy.plugins.protocol import PanelData

        plugin = ReverseProxyPlugin()
        plugin.configure({"type": "traefik", "url": "http://traefik:8080", "error_rate_warn": 5.0})
        hosts = [{"name": "app", "status": "ok", "cert_status": None, "cert_label": "—"}]
        data = PanelData(
            status="warn",
            summary="",
            detail={"backend": "traefik", "router_count": 1, "hosts": hosts, "error_rate": 12.0},
        )
        blocks = plugin.render(data)
        rate_row = next(r for r in blocks[0]["rows"] if r["label"] == "5xx rate")
        assert rate_row["status"] == "warn"

    def test_render_truncated_hosts_shows_more_line(self):
        from buoy.plugins.builtin.reverse_proxy import ReverseProxyPlugin
        from buoy.plugins.protocol import PanelData

        plugin = ReverseProxyPlugin()
        hosts = [{"name": f"host{i}", "status": "ok"} for i in range(10)]
        data = PanelData(
            status="ok",
            summary="",
            detail={
                "backend": "traefik",
                "router_count": 15,
                "hosts": hosts,
                "host_total": 15,
                "error_rate": None,
            },
        )
        blocks = plugin.render(data)
        assert blocks[-1] == {"type": "text", "value": "+5 more", "status": "dim"}

    def test_make_panel_caps_hosts_in_detail(self):
        from buoy.plugins.builtin.reverse_proxy import ReverseProxyPlugin

        plugin = ReverseProxyPlugin()
        hosts = [
            {
                "name": f"host{i}",
                "status": "ok",
                "detail": "",
                "cert_status": None,
                "cert_label": "—",
            }
            for i in range(25)
        ]
        data = plugin._make_panel("traefik", hosts, error_rate=None, router_count=25)
        assert len(data.detail["hosts"]) == 10
        assert data.detail["host_total"] == 25
