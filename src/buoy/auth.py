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
RATE_LIMIT_WINDOW = 60  # seconds
RATE_LIMIT_MAX = 60  # requests per window


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
    """Check if a path requires rate limiting / authentication."""
    path = strip_base_path(path, base_path)
    for prefix in PROTECTED_PATHS:
        if path.startswith(prefix):
            return True
    return False


def check_rate_limit(client_ip: str) -> bool:
    """Sliding-window rate limiter shared across auth-gated endpoints.

    Always active via ``RateLimitMiddleware`` regardless of ``auth.enabled``,
    so protected paths (including ``/api/config/debug``) can't be
    brute-forced even on installs with auth turned off (SPEC §7.2).
    """
    now = time.time()
    window_start = now - RATE_LIMIT_WINDOW

    if client_ip not in _rate_limit:
        _rate_limit[client_ip] = []

    # Remove old entries
    _rate_limit[client_ip] = [t for t in _rate_limit[client_ip] if t > window_start]

    if len(_rate_limit[client_ip]) >= RATE_LIMIT_MAX:
        return False

    _rate_limit[client_ip].append(now)
    return True


def _parse_trusted_networks(
    trusted_proxies: list[str],
) -> tuple[bool, list[ipaddress.IPv4Network | ipaddress.IPv6Network]]:
    """Parse a trusted_proxies list into a (trust_all, networks) tuple.

    - ``"*"`` sets the trust_all flag (every peer is trusted).
    - Each other entry is parsed as an IP network (CIDR or plain IP).
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
            networks.append(ipaddress.ip_network(entry, strict=False))
        except ValueError:
            pass  # ignore unparseable entries
    return trust_all, networks


def _extract_forwarded_ip(xff_value: str) -> str | None:
    """Return the left-most (original client) IP from an X-Forwarded-For value.

    Handles bracketed IPv6, ``ip:port`` forms, and whitespace.
    Returns None if the value is empty or unparseable.
    """
    if not xff_value:
        return None
    # Take the leftmost entry (original client)
    raw = xff_value.split(",")[0].strip()
    # Strip brackets for IPv6: [::1] → ::1
    if raw.startswith("["):
        raw = raw.lstrip("[").split("]")[0]
    elif ":" in raw:
        # Could be ip:port (IPv4) — try to detect by counting colons
        parts = raw.rsplit(":", 1)
        if len(parts) == 2:
            try:
                ipaddress.ip_address(parts[0])
                raw = parts[0]  # valid ip before colon, strip port
            except ValueError:
                pass  # keep raw as-is (might be plain IPv6 without brackets)
    try:
        ipaddress.ip_address(raw)
        return raw
    except ValueError:
        return None


class ProxyHeadersMiddleware:
    """Pure-ASGI middleware that rewrites ``scope["client"]`` and the ``host``
    header when the immediate TCP peer is a trusted proxy.

    With ``trusted_proxies=[]`` (the default) the middleware is a no-op: every
    request passes through unchanged and X-Forwarded-* headers from clients are
    completely ignored (safe default — clients cannot spoof their IP or Host).

    When the peer IP matches the trusted list:
    - ``scope["client"]`` is replaced with the left-most ``X-Forwarded-For``
      entry so each real client gets its own rate-limit bucket.
    - The ``host`` header is replaced with ``X-Forwarded-Host`` (when present)
      so tailnet URL detection in handlers works correctly.
    - The ``x-forwarded-proto`` header (when present) is used to set
      ``scope["scheme"]`` so any scheme-aware logic sees the correct protocol.

    **Security notes:**
    - ``trusted_proxies=["*"]`` trusts ALL peers — convenient for development
      or fully-isolated networks, but allows any client to forge their IP/Host.
    - CIDR ranges like ``172.16.0.0/12`` cover Docker bridge subnets.
    - Taking the *left-most* X-Forwarded-For entry assumes a single trusted
      proxy; with chained proxies the left-most entry can be client-controlled.
      Acceptable for a private-network dashboard.
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
            addr = ipaddress.ip_address(peer_ip)
        except ValueError:
            return False
        return any(addr in net for net in self._networks)

    async def __call__(self, scope, receive, send):
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        peer_ip = client[0] if client else None

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

            # Rewrite client from X-Forwarded-For
            xff = headers_lower.get(b"x-forwarded-for", b"").decode("latin-1")
            real_ip = _extract_forwarded_ip(xff)
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
                headers={"WWW-Authenticate": 'Bearer realm="buoy"'},
            )

        return await call_next(request)

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
