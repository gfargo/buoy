"""Authentication middleware for Buoy.

When auth.enabled is true in config, protects destructive endpoints
(container restart, stop, logs) while leaving read-only APIs public.
"""

from __future__ import annotations

import base64
import hmac
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


def _is_protected(path: str) -> bool:
    """Check if a path requires rate limiting / authentication."""
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


class RateLimitMiddleware(BaseHTTPMiddleware):
    """Always-on per-IP rate limiter for destructive/protected endpoints (SPEC §7.2)."""

    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not _is_protected(path):
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

    def __init__(self, app, auth_config: AuthConfig):
        super().__init__(app)
        self.auth_config = auth_config

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
        return _is_protected(path)

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
