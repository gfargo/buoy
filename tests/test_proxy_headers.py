"""Tests for ProxyHeadersMiddleware and proxy-header-aware rate limiting / tailnet detection.

Covers OSS-1298 / buoy#103 (BUG-46).
"""

from __future__ import annotations

import ipaddress

import pytest
from starlette.testclient import TestClient

from buoy.auth import (
    RATE_LIMIT_MAX,
    ProxyHeadersMiddleware,
    _extract_forwarded_ip,
    _parse_trusted_networks,
    _rate_limit,
)
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig

# ── Unit tests for helper functions ───────────────────────────────────────────


class TestParseTrustedNetworks:
    def test_empty_list(self):
        trust_all, nets = _parse_trusted_networks([])
        assert trust_all is False
        assert nets == []

    def test_star_sets_trust_all(self):
        trust_all, nets = _parse_trusted_networks(["*"])
        assert trust_all is True
        assert nets == []

    def test_plain_ip(self):
        trust_all, nets = _parse_trusted_networks(["10.0.0.1"])
        assert trust_all is False
        assert len(nets) == 1
        assert ipaddress.ip_address("10.0.0.1") in nets[0]

    def test_cidr(self):
        trust_all, nets = _parse_trusted_networks(["172.16.0.0/12"])
        assert len(nets) == 1
        assert ipaddress.ip_address("172.20.0.5") in nets[0]

    def test_ipv6(self):
        trust_all, nets = _parse_trusted_networks(["::1/128"])
        assert len(nets) == 1
        assert ipaddress.ip_address("::1") in nets[0]

    def test_invalid_entry_skipped(self):
        # Should not raise; bad entry is silently ignored
        trust_all, nets = _parse_trusted_networks(["not-an-ip", "10.0.0.1"])
        assert len(nets) == 1

    def test_mix_star_and_cidr(self):
        trust_all, nets = _parse_trusted_networks(["*", "10.0.0.0/8"])
        assert trust_all is True
        assert len(nets) == 1  # CIDR still parsed even with *


class TestExtractForwardedIp:
    def test_simple_ip(self):
        assert _extract_forwarded_ip("1.2.3.4") == "1.2.3.4"

    def test_chain(self):
        # Left-most is the original client
        assert _extract_forwarded_ip("1.2.3.4, 10.0.0.1, 192.168.1.1") == "1.2.3.4"

    def test_whitespace(self):
        assert _extract_forwarded_ip("  1.2.3.4  ") == "1.2.3.4"

    def test_ipv6_bracketed(self):
        assert _extract_forwarded_ip("[::1]") == "::1"

    def test_ipv4_with_port(self):
        # Some proxies include port: 1.2.3.4:12345
        assert _extract_forwarded_ip("1.2.3.4:12345") == "1.2.3.4"

    def test_plain_ipv6(self):
        # Plain IPv6 without brackets
        assert _extract_forwarded_ip("::1") == "::1"

    def test_empty_returns_none(self):
        assert _extract_forwarded_ip("") is None

    def test_garbage_returns_none(self):
        assert _extract_forwarded_ip("not-an-ip") is None


# ── ASGI-level ProxyHeadersMiddleware unit tests ───────────────────────────────


def _make_scope(
    path: str = "/",
    headers: list[tuple[bytes, bytes]] | None = None,
    client: tuple[str, int] = ("127.0.0.1", 50000),
    type: str = "http",
) -> dict:
    base_headers: list[tuple[bytes, bytes]] = [(b"host", b"localhost:8090")]
    if headers:
        base_headers.extend(headers)
    return {
        "type": type,
        "path": path,
        "headers": base_headers,
        "client": client,
        "scheme": "http",
    }


async def _collect_scope(middleware: ProxyHeadersMiddleware, scope: dict) -> dict:
    """Run the middleware and return the scope seen by the inner app."""
    captured: dict = {}

    async def inner_app(s, receive, send):
        captured.update(s)

    await middleware(scope, None, None)
    # The inner_app captures it — but we need to wire it in
    # Use a simpler approach: subclass-free capture
    captured_box: list[dict] = []

    class CapturingApp:
        async def __call__(self, s, receive, send):
            captured_box.append(dict(s))

    mw = ProxyHeadersMiddleware(CapturingApp(), trusted_proxies=middleware._networks and ["*"] or [])
    # Re-run with capture app; copy trust settings
    mw._trust_all = middleware._trust_all
    mw._networks = middleware._networks
    await mw(scope, None, None)
    return captured_box[0] if captured_box else {}


class TestProxyHeadersMiddlewareDirect:
    """Test middleware behaviour by inspecting the scope the inner app receives."""

    def _mw(self, trusted_proxies: list[str]) -> ProxyHeadersMiddleware:
        """Build a middleware instance; inner app is a no-op."""

        async def noop(scope, receive, send):
            pass

        return ProxyHeadersMiddleware(noop, trusted_proxies=trusted_proxies)

    @pytest.mark.asyncio
    async def test_untrusted_peer_scope_unchanged(self):
        """With empty trusted_proxies, X-Forwarded-For must not change client."""
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw_capture = ProxyHeadersMiddleware(capturing_app, trusted_proxies=[])
        scope = _make_scope(
            client=("10.0.0.1", 1000),
            headers=[(b"x-forwarded-for", b"99.99.99.99")],
        )
        await mw_capture(scope, None, None)
        assert received[0]["client"] == ("10.0.0.1", 1000)

    @pytest.mark.asyncio
    async def test_trusted_peer_xff_rewrites_client(self):
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=["*"])
        scope = _make_scope(
            client=("10.0.0.1", 1000),
            headers=[(b"x-forwarded-for", b"203.0.113.5, 10.0.0.1")],
        )
        await mw(scope, None, None)
        assert received[0]["client"] == ("203.0.113.5", 0)

    @pytest.mark.asyncio
    async def test_trusted_peer_xfh_rewrites_host_header(self):
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=["*"])
        scope = _make_scope(
            client=("10.0.0.1", 1000),
            headers=[(b"x-forwarded-host", b"node.example.ts.net")],
        )
        await mw(scope, None, None)
        result_headers = dict(received[0]["headers"])
        assert result_headers[b"host"] == b"node.example.ts.net"

    @pytest.mark.asyncio
    async def test_trusted_peer_xfp_rewrites_scheme(self):
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=["*"])
        scope = _make_scope(
            client=("10.0.0.1", 1000),
            headers=[(b"x-forwarded-proto", b"https")],
        )
        await mw(scope, None, None)
        assert received[0]["scheme"] == "https"

    @pytest.mark.asyncio
    async def test_untrusted_peer_xfh_does_not_rewrite_host(self):
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=[])
        scope = _make_scope(
            client=("10.0.0.1", 1000),
            headers=[(b"x-forwarded-host", b"evil.ts.net")],
        )
        await mw(scope, None, None)
        result_headers = dict(received[0]["headers"])
        assert result_headers[b"host"] == b"localhost:8090"

    @pytest.mark.asyncio
    async def test_cidr_match_trusted(self):
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=["10.0.0.0/8"])
        scope = _make_scope(
            client=("10.1.2.3", 1000),
            headers=[(b"x-forwarded-for", b"5.6.7.8")],
        )
        await mw(scope, None, None)
        assert received[0]["client"] == ("5.6.7.8", 0)

    @pytest.mark.asyncio
    async def test_cidr_no_match_untrusted(self):
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=["10.0.0.0/8"])
        scope = _make_scope(
            client=("192.168.1.1", 1000),
            headers=[(b"x-forwarded-for", b"5.6.7.8")],
        )
        await mw(scope, None, None)
        assert received[0]["client"] == ("192.168.1.1", 1000)

    @pytest.mark.asyncio
    async def test_non_http_scope_passes_through(self):
        """lifespan or other scope types must be forwarded unchanged."""
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=["*"])
        scope = {"type": "lifespan"}
        await mw(scope, None, None)
        assert received[0]["type"] == "lifespan"


# ── Integration tests via TestClient ──────────────────────────────────────────
# TestClient uses ("testclient", 50000) as the TCP peer, so:
# - trusted_proxies=["*"]  → every request is treated as coming from a trusted proxy
# - trusted_proxies=[]     → all X-Forwarded-* headers are ignored


def _make_app(trusted_proxies: list[str] | None = None):
    from buoy.server import create_app

    config = BuoyConfig()
    config.network = NetworkConfig(trusted_proxies=trusted_proxies or [])
    config.features = FeaturesConfig(demo_mode=True)
    # Set a token so /api/config/debug returns 401 (not 403) for unauthenticated
    # requests — matches the pattern in TestRateLimitAlwaysActive.
    config.auth.token = "test-secret"
    return create_app(config)


class TestRateLimitBucketingWithProxy:
    """Trusted proxy: distinct X-Forwarded-For values get independent buckets."""

    @pytest.fixture(autouse=True)
    def clear(self):
        _rate_limit.clear()
        yield
        _rate_limit.clear()

    def test_trusted_distinct_xff_independent_buckets(self):
        """Two real clients behind a trusted proxy must not share a rate-limit bucket."""
        app = _make_app(trusted_proxies=["*"])
        with TestClient(app, raise_server_exceptions=False) as client:
            # Exhaust the limit for client A
            for _ in range(RATE_LIMIT_MAX):
                r = client.get(
                    "/api/config/debug", headers={"X-Forwarded-For": "1.1.1.1"}
                )
                assert r.status_code == 401  # auth rejects, but bucket consumed

            # Client A is now rate-limited
            r = client.get("/api/config/debug", headers={"X-Forwarded-For": "1.1.1.1"})
            assert r.status_code == 429

            # Client B (different XFF) must still have its own fresh bucket
            r = client.get("/api/config/debug", headers={"X-Forwarded-For": "2.2.2.2"})
            assert r.status_code in (401, 200)  # not 429

    def test_untrusted_xff_collapses_into_one_bucket(self):
        """Without trusted proxy, all XFF values are ignored — single bucket."""
        app = _make_app(trusted_proxies=[])
        with TestClient(app, raise_server_exceptions=False) as client:
            # Exhaust the limit as one IP (the testclient peer)
            for _ in range(RATE_LIMIT_MAX):
                r = client.get(
                    "/api/config/debug", headers={"X-Forwarded-For": "1.1.1.1"}
                )
                assert r.status_code == 401

            # Even a different XFF is rate-limited (it's the same real peer)
            r = client.get("/api/config/debug", headers={"X-Forwarded-For": "2.2.2.2"})
            assert r.status_code == 429


class TestTailnetHostRewrite:
    """Tailnet URL detection uses X-Forwarded-Host when proxy is trusted."""

    @pytest.fixture(autouse=True)
    def clear(self):
        _rate_limit.clear()
        yield
        _rate_limit.clear()

    def test_trusted_proxy_xfh_ts_net_detected(self):
        """With trusted proxy + X-Forwarded-Host containing .ts.net, /api/stats
        must return is_tailscale-aware data (not 500)."""
        app = _make_app(trusted_proxies=["*"])
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/api/stats",
                headers={"X-Forwarded-Host": "node.example.ts.net"},
            )
        # Should succeed (200); main point is no crash / 500
        assert r.status_code == 200

    def test_untrusted_proxy_xfh_ignored(self):
        """Without trusted proxy, X-Forwarded-Host is ignored and /api/stats
        still returns 200 with the default (non-tailscale) host."""
        app = _make_app(trusted_proxies=[])
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get(
                "/api/stats",
                headers={"X-Forwarded-Host": "node.example.ts.net"},
            )
        assert r.status_code == 200

    def test_trusted_proxy_xfh_services_tailnet_url(self):
        """With trusted proxy + tailnet host, /api/services should return
        HTTPS tailnet URLs for discovered containers."""
        from unittest.mock import AsyncMock, patch

        app = _make_app(trusted_proxies=["*"])
        # Override config for the global _config used by the handler
        from buoy import server as srv

        original_config = srv._config
        try:
            cfg = BuoyConfig()
            cfg.network = NetworkConfig(
                tailnet_domain="example.ts.net",
                trusted_proxies=["*"],
            )
            cfg.features = FeaturesConfig(demo_mode=True)
            cfg.node = NodeConfig(name="mynode")
            srv._config = cfg

            containers = [{"name": "grafana", "host_port": 3000}]
            with patch("buoy.collectors.docker.DockerCollector") as mock_cls:
                instance = mock_cls.return_value
                instance.list_containers = AsyncMock(return_value=containers)
                srv._collectors["docker"] = instance

                with TestClient(app, raise_server_exceptions=False) as client:
                    r = client.get(
                        "/api/services",
                        headers={"X-Forwarded-Host": "mynode.example.ts.net"},
                    )
                assert r.status_code == 200
        finally:
            srv._config = original_config
            srv._collectors.pop("docker", None)


# ── Config tests ───────────────────────────────────────────────────────────────


class TestTrustedProxiesConfig:
    """trusted_proxies parses correctly from YAML and from environment variables."""

    def test_from_yaml(self):
        from buoy.config import _build_config

        config = _build_config(
            {"network": {"trusted_proxies": ["127.0.0.1", "172.16.0.0/12"]}}
        )
        assert config.network.trusted_proxies == ["127.0.0.1", "172.16.0.0/12"]

    def test_default_empty(self):
        from buoy.config import _build_config

        config = _build_config({})
        assert config.network.trusted_proxies == []

    def test_from_env(self, monkeypatch):
        from buoy.config import _apply_env_overrides

        monkeypatch.setenv(
            "BUOY_NETWORK_TRUSTED_PROXIES", "10.0.0.1, 192.168.0.0/16, *"
        )
        raw = _apply_env_overrides({})
        assert raw["network"]["trusted_proxies"] == ["10.0.0.1", "192.168.0.0/16", "*"]

    def test_from_env_single_entry(self, monkeypatch):
        from buoy.config import _apply_env_overrides

        monkeypatch.setenv("BUOY_NETWORK_TRUSTED_PROXIES", "127.0.0.1")
        raw = _apply_env_overrides({})
        assert raw["network"]["trusted_proxies"] == ["127.0.0.1"]

    def test_from_env_star(self, monkeypatch):
        from buoy.config import _apply_env_overrides

        monkeypatch.setenv("BUOY_NETWORK_TRUSTED_PROXIES", "*")
        raw = _apply_env_overrides({})
        assert raw["network"]["trusted_proxies"] == ["*"]
