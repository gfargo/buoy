"""Authentication middleware for Buoy.

When auth.enabled is true in config, protects destructive endpoints
(container restart, stop, logs) while leaving read-only APIs public.
"""

from __future__ import annotations

import base64
import hmac
import ipaddress
import time
from typing import TYPE_CHECKING

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse

if TYPE_CHECKING:
    from buoy.config import AuthConfig

# Endpoints that require authentication when auth is enabled
PROTECTED_PATHS = {
    "/api/container/",  # prefix match — covers /restart, /logs, detail
    "/api/config/debug",
    "/metrics",  # Prometheus scrape endpoint — rate-limited always; auth-gated when auth.enabled
}

# Rate limiting: track requests per IP for protected endpoints
_rate_limit: dict[str, list[float]] = {}
_rate_limit_last_cleanup = 0.0
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 60  # requests per window
RATE_LIMIT_MAX_CLIENTS = 10_000


def strip_base_path(path: str, base_path: str) -> str:
    """Strip a configured reverse-proxy base path prefix from a request path.

    Mirrors Starlette's ``Mount`` prefix-matching semantics exactly (the
    prefix is only consumed at a ``/`` boundary or end-of-path) so this
    middleware-level view of the path can never diverge from what the
    router actually matched — any divergence here would be a routing
    bypass for the protected paths below.
    """
    if not base_path or not path.startswith(base_path):
        return path
    rest = path[len(base_path) :]
    return rest if rest.startswith("/") else (path if rest else "/")


def _is_protected(path: str, base_path: str = "") -> bool:
    """Check if a path requires rate limiting / authentication.

    Matches against both the raw path and the base-path-stripped path.
    A request that arrives via the prefixed Mount is only protected once
    stripped, but if ``base_path`` itself happens to be a prefix of a
    protected path (e.g. ``base_path=/api``), stripping could otherwise
    turn a protected path into something that no longer matches while the
    root routes still resolve and execute it. Checking both forms closes
    that gap without weakening the check for the normal case.
    """
    stripped = strip_base_path(path, base_path)
    for prefix in PROTECTED_PATHS:
        if path.startswith(prefix) or stripped.startswith(prefix):
            return True
    return False


def check_rate_limit(client_ip: str) -> bool:
    """Sliding-window rate limiter shared across auth-gated endpoints.

    Always active via ``RateLimitMiddleware`` regardless of ``auth.enabled``,
    so protected paths (including ``/api/config/debug``) can't be
    brute-forced even on installs with auth turned off (SPEC §7.2).
    """
    global _rate_limit_last_cleanup

    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    # Expire inactive buckets at most once per window. Capacity handling below
    # rejects new buckets rather than repeatedly scanning or evicting active
    # enforcement state.
    if now - _rate_limit_last_cleanup >= RATE_LIMIT_WINDOW:
        for ip, timestamps in list(_rate_limit.items()):
            active = [timestamp for timestamp in timestamps if timestamp > window_start]
            if active:
                _rate_limit[ip] = active
            else:
                del _rate_limit[ip]
        _rate_limit_last_cleanup = now

    if client_ip not in _rate_limit:
        # Refuse previously unseen addresses until a periodic expiry frees
        # capacity. Evicting an active bucket would reset that client's quota.
        if len(_rate_limit) >= RATE_LIMIT_MAX_CLIENTS:
            return False
        _rate_limit[client_ip] = []

    # Remove old entries from the active client's bucket on every request.
    _rate_limit[client_ip] = [t for t in _rate_limit[client_ip] if t > window_start]

    if len(_rate_limit[client_ip]) >= RATE_LIMIT_MAX:
        return False

    _rate_limit[client_ip].append(now)
    return True


def _canonicalize_ip_address(
    address: ipaddress.IPv4Address | ipaddress.IPv6Address,
) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
    """Collapse IPv4-mapped IPv6 addresses into one canonical IPv4 identity."""
    if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped is not None:
        return address.ipv4_mapped
    return address


def _canonicalize_ip_network(
    network: ipaddress.IPv4Network | ipaddress.IPv6Network,
) -> ipaddress.IPv4Network | ipaddress.IPv6Network:
    """Collapse a wholly IPv4-mapped IPv6 network into its IPv4 equivalent."""
    mapped_space = ipaddress.IPv6Network("::ffff:0:0/96")
    if isinstance(network, ipaddress.IPv6Network) and network.subnet_of(mapped_space):
        mapped_address = network.network_address.ipv4_mapped
        if mapped_address is not None:
            return ipaddress.IPv4Network(
                (mapped_address, network.prefixlen - mapped_space.prefixlen),
                strict=False,
            )
    return network


def _parse_trusted_networks(
    trusted_proxies: list[str],
) -> tuple[bool, list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    """Parse a trusted_proxies list into a (trust_all, networks) tuple.

    - ``"*"`` sets the trust_all flag (every peer is trusted).
    - Each other entry is parsed as an IP network (CIDR or plain IP).
      IPv4-mapped IPv6 networks are normalized to their IPv4 equivalents.
      Unparseable entries are silently skipped (operator misconfiguration
      should not crash the server).
    """
    trust_all = False
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for entry in trusted_proxies:
        if entry == "*":
            trust_all = True
            continue
        try:
            network = ipaddress.ip_network(entry, strict=False)
            networks.append(_canonicalize_ip_network(network))
        except ValueError:
            pass  # ignore unparseable entries
    return trust_all, networks


def _is_valid_forwarded_port(port: str) -> bool:
    """Validate an ASCII decimal port without unbounded integer conversion."""
    if not port or len(port) > 5 or not port.isascii() or not port.isdigit():
        return False
    return len(port) < 5 or port <= "65535"


def _parse_forwarded_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    """Parse one X-Forwarded-For item as a strict IP endpoint.

    Accepts plain IPv4/IPv6, IPv4 with a numeric port, and bracketed IPv6
    with an optional numeric port. Ports must be in the range 0-65535.
    IPv4-mapped IPv6 values are returned as their canonical IPv4 address.
    Hostnames, scoped IPv6, malformed brackets/ports, trailing data, and empty
    values are rejected.
    """
    raw = value.strip()
    if not raw or "%" in raw:
        return None

    host = raw
    port: str | None = None

    if raw.startswith("["):
        close = raw.find("]")
        if close <= 1:
            return None
        host = raw[1:close]
        suffix = raw[close + 1 :]
        if suffix:
            if not suffix.startswith(":"):
                return None
            port = suffix[1:]
            if not _is_valid_forwarded_port(port):
                return None
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            return None
        if address.version != 6:
            return None
    else:
        try:
            address = ipaddress.ip_address(raw)
        except ValueError:
            if raw.count(":") != 1:
                return None
            host, port = raw.rsplit(":", 1)
            if not _is_valid_forwarded_port(port):
                return None
            try:
                address = ipaddress.ip_address(host)
            except ValueError:
                return None
            if address.version != 4:
                return None

    return _canonicalize_ip_address(address)


def _extract_forwarded_ip(xff_value: str) -> str | None:
    """Return the canonical left-most IP from a combined X-Forwarded-For value.

    This preserves wildcard-proxy compatibility. Finite trusted proxy lists use
    :meth:`ProxyHeadersMiddleware._resolve_forwarded_ip` to evaluate the chain.
    """
    if not xff_value:
        return None
    address = _parse_forwarded_ip(xff_value.split(",", 1)[0])
    return str(address) if address is not None else None


class ProxyHeadersMiddleware:
    """Apply forwarded request metadata only for a trusted immediate TCP peer.

    With ``trusted_proxies=[]`` (the safe default), every forwarded header is
    ignored. For a finite trusted list, X-Forwarded-For fields are combined in
    wire order and scanned right-to-left: trusted proxy hops are skipped and
    the nearest untrusted address becomes ``scope["client"]``. A malformed
    value before that boundary fails closed to the original TCP peer; values
    farther left than the selected client are irrelevant. If every valid hop
    is trusted, the left-most address is used for compatibility.

    ``trusted_proxies=["*"]`` intentionally preserves legacy behavior by using
    the strict, canonicalized left-most X-Forwarded-For address. It is suitable
    only for fully isolated networks because any peer can claim its identity.

    For trusted immediate peers, X-Forwarded-Host still replaces ``host`` for
    tailnet URL detection and X-Forwarded-Proto still sets the request scheme.
    CIDRs such as ``172.16.0.0/12`` can cover known Docker proxy networks.
    """

    def __init__(self, app, trusted_proxies: list[str] | None = None):
        self.app = app
        self._trust_all, self._networks = _parse_trusted_networks(trusted_proxies or [])

    def _is_trusted(self, peer_ip: str) -> bool:
        if self._trust_all:
            return True
        if not self._networks:
            return False
        try:
            addr = _canonicalize_ip_address(ipaddress.ip_address(peer_ip))
        except ValueError:
            return False
        return any(addr.version == net.version and addr in net for net in self._networks)

    def _resolve_forwarded_ip(self, xff_value: str) -> str | None:
        """Resolve a combined XFF chain, or return None to keep the TCP peer."""
        if self._trust_all:
            return _extract_forwarded_ip(xff_value)

        items = xff_value.split(",") if xff_value else []
        leftmost: ipaddress.IPv4Address | ipaddress.IPv6Address | None = None
        for item in reversed(items):
            address = _parse_forwarded_ip(item)
            if address is None:
                return None
            leftmost = address
            if not self._is_trusted(str(address)):
                return str(address)
        return str(leftmost) if leftmost is not None else None

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer_ip = client[0] if client else None

        # Canonicalize the transport peer even when XFF is absent, malformed,
        # or ignored so every downstream consumer (including rate limiting)
        # sees one identity for native and IPv4-mapped forms.
        if peer_ip:
            try:
                canonical_peer = str(_canonicalize_ip_address(ipaddress.ip_address(peer_ip)))
            except ValueError:
                canonical_peer = peer_ip
            if canonical_peer != peer_ip:
                scope = dict(scope)
                scope["client"] = (canonical_peer, client[1])
                peer_ip = canonical_peer

        if peer_ip and self._is_trusted(peer_ip):
            headers: list[tuple[bytes, bytes]] = list(scope.get("headers", []))
            # Duplicate header fields with the same name are equivalent to a
            # single comma-joined value (RFC 7230 §3.2.2) — join them instead
            # of silently keeping only the last one.
            headers_lower: dict[bytes, bytes] = {}
            for k, v in headers:
                key = k.lower()
                if key in headers_lower:
                    headers_lower[key] = headers_lower[key] + b", " + v
                else:
                    headers_lower[key] = v

            # Make a mutable copy of scope once for all rewrites
            scope = dict(scope)

            # Resolve client from the complete X-Forwarded-For chain.
            xff = headers_lower.get(b"x-forwarded-for", b"").decode("latin-1")
            real_ip = self._resolve_forwarded_ip(xff)
            if real_ip:
                scope["client"] = (real_ip, 0)

            # Rewrite host header from X-Forwarded-Host
            xfh = headers_lower.get(b"x-forwarded-host", b"").decode("latin-1").strip()
            if xfh:
                # Remove existing host entries and append the forwarded host
                new_headers = [(k, v) for k, v in headers if k.lower() != b"host"]
                new_headers.append((b"host", xfh.encode("latin-1")))
                scope["headers"] = new_headers

            # Update scheme from X-Forwarded-Proto
            xfp = headers_lower.get(b"x-forwarded-proto", b"").decode("latin-1").strip()
            if xfp and xfp in ("http", "https"):
                scope["scheme"] = xfp

        await self.app(scope, receive, send)


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Always-on per-IP rate limiter for destructive/protected endpoints (SPEC §7.2)."""

    def __init__(self, app, base_path: str = ""):
        super().__init__(app)
        self.base_path = base_path

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not _is_protected(path, self.base_path):
            return await call_next(request)

        client_ip = request.client.host if request.client else "unknown"
        if not check_rate_limit(client_ip):
            return JSONResponse(
                {"error": "rate limit exceeded", "retry_after": RATE_LIMIT_WINDOW},
                status_code=429,
            )

        return await call_next(request)


class AuthMiddleware(BaseHTTPMiddleware):
    """Optional auth middleware — only active when auth.enabled is True."""

    def __init__(self, app, auth_config: AuthConfig, base_path: str = ""):
        super().__init__(app)
        self.auth_config = auth_config
        self.base_path = base_path

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        # Only protect specific paths
        if not self._is_protected(path):
            return await call_next(request)

        # Auth check
        if not self._authenticate(request):
            return JSONResponse(
                {"error": "authentication required"},
                status_code=401,
                headers={"WWW-Authenticate": self._challenge()},
            )

        return await call_next(request)

    def _challenge(self) -> str:
        """Return the authentication challenge for the configured mode."""
        if self.auth_config.type == "basic":
            return 'Basic realm="buoy", charset="UTF-8"'
        return 'Bearer realm="buoy"'

    def _is_protected(self, path: str) -> bool:
        """Check if a path requires authentication."""
        return _is_protected(path, self.base_path)

    def _authenticate(self, request: Request) -> bool:
        """Validate the request's auth credentials."""
        auth_header = request.headers.get("Authorization", "")

        if self.auth_config.type == "token":
            return self._check_token(auth_header)
        elif self.auth_config.type == "basic":
            return self._check_basic(auth_header)
        return False

    def _check_token(self, auth_header: str) -> bool:
        """Verify Bearer token."""
        expected = self.auth_config.token
        if not expected:
            return False  # No token configured = fail closed, deny all

        if not auth_header.startswith("Bearer "):
            return False

        provided = auth_header[7:]
        # Constant-time comparison to prevent timing attacks
        return hmac.compare_digest(provided, expected)

    def _check_basic(self, auth_header: str) -> bool:
        """Verify Basic auth credentials."""
        expected_user = self.auth_config.username
        expected_pass = self.auth_config.password

        if not expected_user or not expected_pass:
            return False  # Not configured = fail closed, deny all

        if not auth_header.startswith("Basic "):
            return False

        try:
            decoded = base64.b64decode(auth_header[6:]).decode()
            user, password = decoded.split(":", 1)
            return hmac.compare_digest(user, expected_user) and hmac.compare_digest(
                password, expected_pass
            )
        except Exception:
            return False

    def _check_rate_limit(self, client_ip: str) -> bool:
        """Simple sliding window rate limiter."""
        return check_rate_limit(client_ip)
