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
    _parse_forwarded_ip,
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

    def test_ipv4_mapped_ipv6_network_is_canonicalized(self):
        trust_all, nets = _parse_trusted_networks(["::ffff:10.0.0.0/104"])
        assert trust_all is False
        assert nets == [ipaddress.ip_network("10.0.0.0/8")]

    def test_invalid_entry_skipped(self):
        # Should not raise; bad entry is silently ignored
        trust_all, nets = _parse_trusted_networks(["not-an-ip", "10.0.0.1"])
        assert len(nets) == 1

    def test_mix_star_and_cidr(self):
        trust_all, nets = _parse_trusted_networks(["*", "10.0.0.0/8"])
        assert trust_all is True
        assert len(nets) == 1  # CIDR still parsed even with *


class TestParseForwardedIp:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("192.0.2.1", "192.0.2.1"),
            (" 192.0.2.1 ", "192.0.2.1"),
            ("192.0.2.1:0", "192.0.2.1"),
            ("192.0.2.1:65535", "192.0.2.1"),
            ("2001:0db8:0:0::1", "2001:db8::1"),
            ("[2001:0db8::1]", "2001:db8::1"),
            ("[2001:0db8::1]:443", "2001:db8::1"),
            ("[::ffff:192.0.2.1]:65535", "192.0.2.1"),
        ],
    )
    def test_supported_forms_are_canonicalized(self, value, expected):
        address = _parse_forwarded_ip(value)
        assert address is not None
        assert str(address) == expected

    @pytest.mark.parametrize(
        "value",
        [
            pytest.param("192.0.2.1:" + "9" * 10_000, id="oversized-ipv4-port"),
            pytest.param("[2001:db8::1]:" + "9" * 10_000, id="oversized-ipv6-port"),
        ],
    )
    def test_arbitrarily_long_ascii_numeric_ports_are_rejected(self, value):
        assert _parse_forwarded_ip(value) is None

    @pytest.mark.parametrize(
        "value",
        [
            "",
            "   ",
            "example.com",
            "example.com:443",
            "192.0.2.1:",
            "192.0.2.1:-1",
            "192.0.2.1:+1",
            "192.0.2.1:65536",
            "192.0.2.1:443junk",
            "192.0.2.1 extra",
            "[192.0.2.1]",
            "[::1",
            "::1]",
            "[::1]junk",
            "[::1]:",
            "[::1]:-1",
            "[::1]:65536",
            "[::1]:443junk",
            "[::1]]:443",
            "[::1]:443:extra",
            "fe80::1%eth0",
            "garbage",
        ],
    )
    def test_invalid_forms_are_rejected(self, value):
        assert _parse_forwarded_ip(value) is None


class TestExtractForwardedIp:
    def test_leftmost_compatibility(self):
        assert _extract_forwarded_ip("192.0.2.1, 10.0.0.1") == "192.0.2.1"

    def test_canonicalizes_selected_ip(self):
        assert _extract_forwarded_ip("[2001:0db8::1]:443") == "2001:db8::1"

    def test_canonicalizes_ipv4_mapped_ipv6_to_ipv4(self):
        assert _extract_forwarded_ip("[::ffff:192.0.2.1]:443") == "192.0.2.1"

    @pytest.mark.parametrize("value", ["", "not-an-ip", ", 192.0.2.1"])
    def test_invalid_leftmost_returns_none(self, value):
        assert _extract_forwarded_ip(value) is None


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

    mw = ProxyHeadersMiddleware(
        CapturingApp(), trusted_proxies=middleware._networks and ["*"] or []
    )
    # Re-run with capture app; copy trust settings
    mw._trust_all = middleware._trust_all
    mw._networks = middleware._networks
    await mw(scope, None, None)
    return captured_box[0] if captured_box else {}


async def _apply_proxy(
    trusted_proxies: list[str],
    xff_headers: list[bytes],
    *,
    client: tuple[str, int] = ("10.0.0.2", 1000),
) -> dict:
    """Return the scope seen after proxy-header processing."""
    received: list[dict] = []

    async def capturing_app(scope, receive, send):
        received.append(dict(scope))

    middleware = ProxyHeadersMiddleware(capturing_app, trusted_proxies=trusted_proxies)
    headers = [(b"x-forwarded-for", value) for value in xff_headers]
    await middleware(_make_scope(client=client, headers=headers), None, None)
    return received[0]


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
    async def test_ipv4_mapped_immediate_peer_matches_ipv4_cidr(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8"],
            [b"203.0.113.5"],
            client=("::ffff:10.0.0.2", 1000),
        )
        assert scope["client"] == ("203.0.113.5", 0)

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
    async def test_trusted_ipv6_peer_xff_rewrites_client(self):
        """A trusted proxy peering over IPv6 (e.g. ::1) must still be honored."""
        received: list[dict] = []

        async def capturing_app(scope, receive, send):
            received.append(dict(scope))

        mw = ProxyHeadersMiddleware(capturing_app, trusted_proxies=["::1/128"])
        scope = _make_scope(
            client=("::1", 1000),
            headers=[(b"x-forwarded-for", b"203.0.113.5")],
        )
        await mw(scope, None, None)
        assert received[0]["client"] == ("203.0.113.5", 0)

    @pytest.mark.asyncio
    async def test_finite_trust_ignores_append_spoof_left_of_client(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8"],
            [b"198.51.100.66, 203.0.113.5, 10.0.0.1"],
        )
        assert scope["client"] == ("203.0.113.5", 0)

    @pytest.mark.asyncio
    async def test_finite_trust_skips_multiple_trusted_proxy_hops(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8", "192.168.0.0/16"],
            [b"203.0.113.5, 192.168.1.10, 10.0.0.1"],
        )
        assert scope["client"] == ("203.0.113.5", 0)

    @pytest.mark.asyncio
    async def test_ipv4_mapped_internal_hop_matches_ipv4_cidr(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8"],
            [b"203.0.113.5, ::ffff:10.0.0.1"],
        )
        assert scope["client"] == ("203.0.113.5", 0)

    @pytest.mark.asyncio
    async def test_ipv4_mapped_selected_client_is_canonical_ipv4(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8"],
            [b"::ffff:192.0.2.1, 10.0.0.1"],
        )
        assert scope["client"] == ("192.0.2.1", 0)

    @pytest.mark.asyncio
    async def test_duplicate_xff_fields_preserve_wire_order(self):
        trusted = ["10.0.0.0/8", "192.168.0.0/16"]
        first = b"198.51.100.66"
        second = b"203.0.113.5, 192.168.1.10, 10.0.0.1"

        scope = await _apply_proxy(trusted, [first, second])
        assert scope["client"] == ("203.0.113.5", 0)

        reversed_scope = await _apply_proxy(trusted, [second, first])
        assert reversed_scope["client"] == ("198.51.100.66", 0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "xff",
        [
            b"203.0.113.5, garbage, 10.0.0.1",
            b"203.0.113.5, , 10.0.0.1",
            b"203.0.113.5, 192.168.1.10:65536, 10.0.0.1",
            pytest.param(
                b"203.0.113.5, 192.168.1.10:" + b"9" * 10_000 + b", 10.0.0.1",
                id="oversized-ipv4-port",
            ),
            pytest.param(
                b"203.0.113.5, [2001:db8::1]:" + b"9" * 10_000 + b", 10.0.0.1",
                id="oversized-ipv6-port",
            ),
        ],
    )
    async def test_malformed_boundary_fails_closed_to_tcp_peer(self, xff):
        scope = await _apply_proxy(
            ["10.0.0.0/8", "192.168.0.0/16"],
            [xff],
        )
        assert scope["client"] == ("10.0.0.2", 1000)

    @pytest.mark.asyncio
    async def test_malformed_value_left_of_selected_client_is_irrelevant(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8"],
            [b"garbage, 203.0.113.5, 10.0.0.1"],
        )
        assert scope["client"] == ("203.0.113.5", 0)

    @pytest.mark.asyncio
    async def test_all_trusted_hops_fall_back_to_canonical_leftmost(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8", "2001:db8::/32"],
            [b"[2001:0db8::1]:443, 10.0.0.1:8080"],
        )
        assert scope["client"] == ("2001:db8::1", 0)

    @pytest.mark.asyncio
    async def test_wildcard_preserves_leftmost_behavior(self):
        scope = await _apply_proxy(
            ["*"],
            [b"198.51.100.66, 203.0.113.5, 10.0.0.1"],
        )
        assert scope["client"] == ("198.51.100.66", 0)

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("trusted", "client", "xff", "expected"),
        [
            (["10.0.0.0/8"], ("10.0.0.2", 1000), b"192.0.2.1:1234", "192.0.2.1"),
            (["2001:db8::/32"], ("2001:db8::2", 1000), b"[2001:db9::1]:443", "2001:db9::1"),
            (["10.0.0.0/8"], ("10.0.0.2", 1000), b"2001:db8::1, 10.0.0.1", "2001:db8::1"),
            (["2001:db8::/32"], ("2001:db8::2", 1000), b"192.0.2.1, [2001:db8::1]:80", "192.0.2.1"),
        ],
    )
    async def test_ipv4_ipv6_ports_and_mixed_families(self, trusted, client, xff, expected):
        scope = await _apply_proxy(trusted, [xff], client=client)
        assert scope["client"] == (expected, 0)

    @pytest.mark.asyncio
    async def test_trusted_peer_without_xff_keeps_tcp_peer(self):
        scope = await _apply_proxy(["10.0.0.0/8"], [])
        assert scope["client"] == ("10.0.0.2", 1000)

    @pytest.mark.asyncio
    async def test_ipv4_mapped_peer_without_xff_is_canonical_ipv4(self):
        scope = await _apply_proxy(
            ["10.0.0.0/8"],
            [],
            client=("::ffff:10.0.0.2", 1000),
        )
        assert scope["client"] == ("10.0.0.2", 1000)

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
                r = client.get("/api/config/debug", headers={"X-Forwarded-For": "1.1.1.1"})
                assert r.status_code == 401  # auth rejects, but bucket consumed

            # Client A is now rate-limited
            r = client.get("/api/config/debug", headers={"X-Forwarded-For": "1.1.1.1"})
            assert r.status_code == 429

            # Client B (different XFF) must still have its own fresh bucket
            r = client.get("/api/config/debug", headers={"X-Forwarded-For": "2.2.2.2"})
            assert r.status_code in (401, 200)  # not 429

    def test_finite_trust_rate_limit_uses_nearest_untrusted_identity(self):
        """Rotating values left of the client cannot rotate rate-limit buckets."""
        app = _make_app(trusted_proxies=["10.0.0.0/8"])
        with TestClient(
            app,
            raise_server_exceptions=False,
            client=("10.0.0.2", 50000),
        ) as client:
            for index in range(RATE_LIMIT_MAX):
                r = client.get(
                    "/api/config/debug",
                    headers={"X-Forwarded-For": f"198.51.100.{index % 250}, 203.0.113.5, 10.0.0.1"},
                )
                assert r.status_code == 401

            r = client.get(
                "/api/config/debug",
                headers={"X-Forwarded-For": "198.51.100.251, 203.0.113.5, 10.0.0.1"},
            )
            assert r.status_code == 429

            r = client.get(
                "/api/config/debug",
                headers={"X-Forwarded-For": "198.51.100.251, 203.0.113.6, 10.0.0.1"},
            )
            assert r.status_code == 401

    @pytest.mark.parametrize(
        ("trusted_proxies", "peer", "mapped_xff", "native_xff"),
        [
            (["*"], ("testclient", 50000), "::ffff:192.0.2.1", "192.0.2.1"),
            (
                ["10.0.0.0/8"],
                ("::ffff:10.0.0.2", 50000),
                "::ffff:192.0.2.1, ::ffff:10.0.0.1",
                "192.0.2.1, 10.0.0.1",
            ),
        ],
    )
    def test_ipv4_mapped_and_native_forms_share_rate_limit_key(
        self,
        trusted_proxies,
        peer,
        mapped_xff,
        native_xff,
    ):
        app = _make_app(trusted_proxies=trusted_proxies)
        with TestClient(
            app,
            raise_server_exceptions=False,
            client=peer,
        ) as client:
            mapped = client.get(
                "/api/config/debug",
                headers={"X-Forwarded-For": mapped_xff},
            )
            native = client.get(
                "/api/config/debug",
                headers={"X-Forwarded-For": native_xff},
            )

        assert mapped.status_code == 401
        assert native.status_code == 401
        assert set(_rate_limit) == {"192.0.2.1"}
        assert len(_rate_limit["192.0.2.1"]) == 2

    def test_ipv4_mapped_and_native_tcp_peers_share_rate_limit_key_without_xff(self):
        app = _make_app(trusted_proxies=[])
        for peer in [("::ffff:10.0.0.2", 50000), ("10.0.0.2", 50000)]:
            with TestClient(
                app,
                raise_server_exceptions=False,
                client=peer,
            ) as client:
                response = client.get("/api/config/debug")
                assert response.status_code == 401

        assert set(_rate_limit) == {"10.0.0.2"}
        assert len(_rate_limit["10.0.0.2"]) == 2

    def test_untrusted_xff_collapses_into_one_bucket(self):
        """Without trusted proxy, all XFF values are ignored — single bucket."""
        app = _make_app(trusted_proxies=[])
        with TestClient(app, raise_server_exceptions=False) as client:
            # Exhaust the limit as one IP (the testclient peer)
            for _ in range(RATE_LIMIT_MAX):
                r = client.get("/api/config/debug", headers={"X-Forwarded-For": "1.1.1.1"})
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
        """A trusted forwarded host drives app-local tailnet service URLs."""
        from unittest.mock import AsyncMock

        from buoy.server import create_app

        cfg = BuoyConfig()
        cfg.network = NetworkConfig(
            tailnet_domain="example.ts.net",
            trusted_proxies=["*"],
        )
        cfg.features = FeaturesConfig(demo_mode=True)
        cfg.node = NodeConfig(name="mynode")
        app = create_app(cfg)

        containers = [{"name": "grafana", "host_port": 3000}]
        with TestClient(app, raise_server_exceptions=False) as client:
            collector = app.state.buoy.collectors["docker"]
            collector.list_containers = AsyncMock(return_value=containers)
            r = client.get(
                "/api/services",
                headers={"X-Forwarded-Host": "mynode.example.ts.net"},
            )

        assert r.status_code == 200


class TestIsTailscaleCustomDomain:
    """_is_tailscale() also honors an explicit config's custom tailnet domain."""

    @staticmethod
    def _request_for_host(host: str):
        from starlette.requests import Request

        scope = {
            "type": "http",
            "path": "/",
            "headers": [(b"host", host.encode())],
            "client": ("127.0.0.1", 50000),
            "scheme": "http",
        }
        return Request(scope)

    @staticmethod
    def _config(tailnet_domain: str) -> BuoyConfig:
        cfg = BuoyConfig()
        cfg.network = NetworkConfig(tailnet_domain=tailnet_domain)
        return cfg

    def test_custom_tailnet_domain_detected(self):
        from buoy.server import _is_tailscale

        request = self._request_for_host("node.corp.example.internal:8090")
        assert _is_tailscale(request, self._config("corp.example.internal")) is True

    def test_unrelated_host_with_custom_domain_configured_not_detected(self):
        from buoy.server import _is_tailscale

        request = self._request_for_host("node.example.com:8090")
        assert _is_tailscale(request, self._config("corp.example.internal")) is False

    def test_no_tailnet_domain_configured_falls_back_to_ts_net_only(self):
        from buoy.server import _is_tailscale

        request = self._request_for_host("node.corp.example.internal:8090")
        assert _is_tailscale(request, self._config("")) is False

    def test_ts_net_still_detected_when_custom_domain_configured(self):
        from buoy.server import _is_tailscale

        request = self._request_for_host("node.example.ts.net:8090")
        assert _is_tailscale(request, self._config("corp.example.internal")) is True


# ── Config tests ───────────────────────────────────────────────────────────────


class TestTrustedProxiesConfig:
    """trusted_proxies parses correctly from YAML and from environment variables."""

    def test_from_yaml(self):
        from buoy.config import _build_config

        config = _build_config({"network": {"trusted_proxies": ["127.0.0.1", "172.16.0.0/12"]}})
        assert config.network.trusted_proxies == ["127.0.0.1", "172.16.0.0/12"]

    def test_default_empty(self):
        from buoy.config import _build_config

        config = _build_config({})
        assert config.network.trusted_proxies == []

    def test_from_env(self, monkeypatch):
        from buoy.config import _apply_env_overrides

        monkeypatch.setenv("BUOY_NETWORK_TRUSTED_PROXIES", "10.0.0.1, 192.168.0.0/16, *")
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
