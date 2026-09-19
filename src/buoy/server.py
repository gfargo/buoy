"""Buoy server — Starlette application with API routes and WebSocket support."""

from __future__ import annotations

import asyncio
import collections
import contextlib
import dataclasses
import hmac
import html as html_module
import json
import logging
import re
import secrets
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from buoy._version import VERSION
from buoy.subprocess_utils import communicate

if TYPE_CHECKING:
    from buoy.alerts import AlertEngine
    from buoy.config import BuoyConfig
    from buoy.plugins.loader import PluginManager
    from buoy.storage import MetricStore

logger = logging.getLogger("buoy.server")


@dataclass
class BuoyAppState:
    """Mutable runtime resources owned by one Starlette application."""

    config: BuoyConfig
    collectors: dict[str, Any] = field(default_factory=dict)
    ws_clients: set[WebSocket] = field(default_factory=set)
    plugin_manager: PluginManager | None = None
    metric_store: MetricStore | None = None
    alert_engine: AlertEngine | None = None
    image_update_cache: dict[str, dict[str, Any]] = field(default_factory=dict)
    background_tasks: list[asyncio.Task[None]] = field(default_factory=list)
    # Single-use WS log-stream tickets: ticket -> (container name, monotonic expiry).
    log_tickets: dict[str, tuple[str, float]] = field(default_factory=dict)
    log_stream_count: int = 0


# ── API Handlers ───────────────────────────────────────────────────────────────


def _is_tailscale(request: Request, config: BuoyConfig) -> bool:
    """Return True when the request's Host header indicates a Tailscale network."""
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    if host == "ts.net" or host.endswith(".ts.net"):
        return True

    tailnet_domain = config.network.tailnet_domain.strip(".").lower()
    return bool(tailnet_domain) and (host == tailnet_domain or host.endswith(f".{tailnet_domain}"))


async def api_health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    state: BuoyAppState = request.app.state.buoy
    return JSONResponse(
        {
            "status": "ok",
            "hostname": state.config.node.name,
            "version": VERSION,
        }
    )


async def api_config(request: Request) -> JSONResponse:
    """Public config subset — no secrets, just display/feature info."""
    state: BuoyAppState = request.app.state.buoy
    return JSONResponse(
        {
            "node": {
                "name": state.config.node.name,
                "tier": state.config.node.tier,
                "role": state.config.node.role,
            },
            "network": {
                "tailnet_domain": state.config.network.tailnet_domain,
                "base_path": state.config.network.base_path,
                "peers": [
                    {"name": p.name, "url": p.url, "tier": p.tier}
                    for p in state.config.network.peers
                ],
            },
            "theme": {
                "preset": state.config.theme.preset,
                "custom": state.config.theme.custom,
            },
            "auth": {
                "enabled": state.config.auth.enabled,
                "type": state.config.auth.type if state.config.auth.enabled else None,
            },
            "features": {
                "websocket": state.config.features.websocket,
                "history": state.config.features.history,
                "demo_mode": state.config.features.demo_mode,
                "night_mode": state.config.features.night_mode,
                "keyboard_shortcuts": state.config.features.keyboard_shortcuts,
                "image_updates": state.config.features.image_updates,
                "log_streaming": state.config.features.log_streaming,
            },
            "logs": {
                "default_tail": state.config.logs.default_tail,
                "max_tail": state.config.logs.max_tail,
            },
            "refresh": {
                "stats_interval": state.config.refresh.stats_interval,
                "services_interval": state.config.refresh.services_interval,
                "fleet_interval": state.config.refresh.fleet_interval,
                "plugins_interval": state.config.refresh.plugins_interval,
                "image_updates_interval": state.config.refresh.image_updates_interval,
            },
        }
    )


async def api_config_debug(request: Request) -> JSONResponse:
    """Auth-protected endpoint returning the full loaded config with secrets redacted.

    This endpoint is gated independently of ``auth.enabled`` so it cannot be
    accessed on a default (unauthenticated) install.

    Access rules:
    - If ``auth.token`` is set, require ``Authorization: Bearer <token>``.
    - Otherwise the endpoint is disabled (403) — callers must configure a token.

    This is intentionally token-only: installs using ``auth.type == "basic"``
    without also setting ``auth.token`` cannot reach this endpoint. Basic-auth
    credentials are not accepted here because they're checked by a separate
    code path (``AuthMiddleware._check_basic``) with different semantics;
    requiring a dedicated token keeps this handler's auth self-contained.
    Operators relying on ``auth.type == "basic"`` who also want access to this
    endpoint should additionally set ``auth.token``.

    Rate-limited by ``RateLimitMiddleware``, which is always mounted
    (independent of ``auth.enabled``) and covers every path in
    ``PROTECTED_PATHS``, including this one — so no separate check is needed
    here.
    """
    state: BuoyAppState = request.app.state.buoy
    token = state.config.auth.token
    if not token:
        # No token configured → refuse; don't expose topology to anonymous callers.
        return JSONResponse(
            {"error": "debug endpoint requires auth.token to be configured"},
            status_code=403,
        )

    auth_header = request.headers.get("Authorization", "")
    if not auth_header.startswith("Bearer "):
        return JSONResponse(
            {"error": "authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="buoy"'},
        )

    provided = auth_header[7:]
    # Compare as bytes — compare_digest raises TypeError on non-ASCII str, which
    # would otherwise turn an auth failure into a 500 instead of a 401.
    if not hmac.compare_digest(provided.encode(), token.encode()):
        return JSONResponse(
            {"error": "authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="buoy"'},
        )

    return JSONResponse(_redact_secrets(dataclasses.asdict(state.config)))


async def api_deploy_info(request: Request) -> JSONResponse:
    """Deployment metadata — version, build time, git SHA."""
    info: dict = {"version": VERSION}

    # Container creation time (image build date)
    try:
        proc = await asyncio.create_subprocess_exec(
            "stat",
            "-c",
            "%W",
            "/proc/1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await communicate(proc, timeout=3)
        if stdout and stdout.strip() != b"0":
            import datetime

            boot_ts = int(stdout.strip())
            info["container_started"] = datetime.datetime.fromtimestamp(
                boot_ts, tz=datetime.UTC
            ).isoformat()
    except Exception:
        logger.debug("api_deploy_info: container start time probe failed", exc_info=True)

    # Git HEAD from host strut repo (optional, best-effort)
    try:
        proc = await asyncio.create_subprocess_exec(
            "nsenter",
            "-t",
            "1",
            "-m",
            "--",
            "bash",
            "-c",
            "cd ~/strut 2>/dev/null && git log -1 --format='%h %s' 2>/dev/null",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await communicate(proc, timeout=5)
        if stdout and stdout.strip():
            info["git_head"] = stdout.decode().strip()
    except Exception:
        logger.debug("api_deploy_info: git HEAD probe failed", exc_info=True)

    return JSONResponse(info)


async def api_stats(request: Request) -> JSONResponse:
    """System vitals — CPU, RAM, disk, temp, containers, uptime."""
    state: BuoyAppState = request.app.state.buoy
    from buoy.services import top_services

    system_coll = state.collectors.get("system")
    docker_coll = state.collectors.get("docker")
    disk_coll = state.collectors.get("disk")

    is_tailscale = _is_tailscale(request, state.config)

    # Gather all stats concurrently
    results = await asyncio.gather(
        system_coll.collect() if system_coll else _empty_system(state.config),
        docker_coll.collect_summary() if docker_coll else _empty_docker(),
        disk_coll.collect_summary() if disk_coll else _empty_disk(),
        top_services(state.config, is_tailscale, collector=docker_coll),
        return_exceptions=True,
    )

    system_data = results[0] if not isinstance(results[0], Exception) else {}
    docker_data = results[1] if not isinstance(results[1], Exception) else {}
    disk_data = results[2] if not isinstance(results[2], Exception) else {}
    services = results[3] if not isinstance(results[3], Exception) else []

    # Decorate each container entry with update status from cache (pure dict lookup)
    if state.image_update_cache and "containers_list" in docker_data:
        for ctr in docker_data["containers_list"]:
            entry = state.image_update_cache.get(ctr["name"])
            if entry:
                ctr["update_status"] = entry["status"]

    alerts = [a.to_dict() for a in state.alert_engine.active_alerts] if state.alert_engine else []
    return JSONResponse(
        {**system_data, **docker_data, **disk_data, "top_services": services, "alerts": alerts}
    )


async def api_stats_detail(request: Request) -> JSONResponse:
    """Extended metrics — per-core CPU, top processes, mount details."""
    state: BuoyAppState = request.app.state.buoy

    system_coll = state.collectors.get("system")
    disk_coll = state.collectors.get("disk")

    results = await asyncio.gather(
        system_coll.collect_detail() if system_coll else _empty_detail(),
        disk_coll.collect_detail() if disk_coll else _empty_disk_detail(),
        return_exceptions=True,
    )

    system_detail = results[0] if not isinstance(results[0], Exception) else {}
    disk_detail = results[1] if not isinstance(results[1], Exception) else {}

    return JSONResponse(
        {
            "cpu": system_detail.get("cpu", {}),
            "memory": system_detail.get("memory", {}),
            "disk": disk_detail,
        }
    )


async def api_services(request: Request) -> JSONResponse:
    """Discovered local services + network links."""
    state: BuoyAppState = request.app.state.buoy
    from buoy.services import discover_services

    is_tailscale = _is_tailscale(request, state.config)
    data = await discover_services(
        state.config, is_tailscale, collector=state.collectors.get("docker")
    )
    return JSONResponse(data)


async def api_fleet(request: Request) -> JSONResponse:
    """Aggregated peer node stats."""
    state: BuoyAppState = request.app.state.buoy

    network_coll = state.collectors.get("network")
    if not network_coll:
        return JSONResponse({"peers": []})

    data = await network_coll.collect()
    return JSONResponse(data)


async def api_container_history(request: Request) -> JSONResponse:
    """24h up/down history for a single container (if history enabled)."""
    state: BuoyAppState = request.app.state.buoy
    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    if not state.config.features.history or not state.metric_store:
        return JSONResponse({"error": "history feature not enabled"}, status_code=404)

    hours_str = request.query_params.get("hours", "24")
    try:
        hours = max(1, min(int(hours_str), 24))
    except (ValueError, TypeError):
        hours = 24

    samples = await asyncio.to_thread(
        state.metric_store.query_container_history, name, hours * 3600
    )
    return JSONResponse(
        {
            "container": name,
            "hours": hours,
            "samples": [{"ts": ts, "status": st, "restart_count": rc} for ts, st, rc in samples],
        }
    )


async def api_container_detail(request: Request) -> JSONResponse:
    """Container inspect + resource usage."""
    state: BuoyAppState = request.app.state.buoy
    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    docker_coll = state.collectors.get("docker")
    if not docker_coll:
        return JSONResponse({"error": "docker not available"}, status_code=503)

    data = await docker_coll.inspect_container(name)
    return JSONResponse(data)


def _clamp_tail(raw: str | None, config: BuoyConfig) -> int:
    """Parse and clamp a `?tail=` query param to `[1, logs.max_tail]`.

    Falls back to `logs.default_tail` for a missing or unparsable value,
    keeping the one-shot REST endpoint and the WS stream consistent.
    """
    try:
        tail = int(raw) if raw is not None else config.logs.default_tail
    except (TypeError, ValueError):
        tail = config.logs.default_tail
    return max(1, min(tail, config.logs.max_tail))


async def api_container_logs(request: Request) -> JSONResponse:
    state: BuoyAppState = request.app.state.buoy
    """Last N lines of container stdout/stderr."""
    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    docker_coll = state.collectors.get("docker")
    if not docker_coll:
        return JSONResponse({"error": "docker not available"}, status_code=503)

    tail = _clamp_tail(request.query_params.get("tail"), state.config)
    data = await docker_coll.get_logs(name, tail=tail)
    return JSONResponse(data)


_LOG_TICKET_TTL = 30.0
_LOG_TICKET_MAX = 1000


async def api_container_logs_ticket(request: Request) -> JSONResponse:
    """Issue a single-use, container-scoped ticket for the WS log stream.

    Browsers cannot set an `Authorization` header on a WebSocket handshake,
    so `ws_container_logs` can't reuse `AuthMiddleware` directly — and
    `BaseHTTPMiddleware` (both `AuthMiddleware` and `RateLimitMiddleware`)
    never runs for `websocket` scopes in the first place. This endpoint is
    plain HTTP under the already-protected `/api/container/` prefix, so it
    inherits auth (when enabled) and the always-on rate limiter, and hands
    back a short-lived ticket that the WS handler consumes exactly once.
    """
    state: BuoyAppState = request.app.state.buoy
    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    now = time.monotonic()
    for stale in [t for t, (_, exp) in state.log_tickets.items() if exp <= now]:
        del state.log_tickets[stale]

    if len(state.log_tickets) >= _LOG_TICKET_MAX:
        return JSONResponse({"error": "too many pending tickets"}, status_code=429)

    ticket = secrets.token_urlsafe(32)
    state.log_tickets[ticket] = (name, now + _LOG_TICKET_TTL)
    return JSONResponse({"ticket": ticket, "expires_in": int(_LOG_TICKET_TTL)})


async def api_container_restart(request: Request) -> JSONResponse:
    state: BuoyAppState = request.app.state.buoy
    """Restart a Docker container.

    Requires ``Content-Type: application/json``. This isn't for parsing a
    body — it's because that content type isn't CORS-safelisted, so any
    cross-origin caller (browser fetch or HTML form) is forced through a
    preflight OPTIONS request. Since no CORS middleware is installed for
    unlisted origins (see create_app), that preflight gets no
    Access-Control-Allow-Origin back and the browser never issues the real
    POST — closing the "simple request" gap that a same-origin-only CORS
    policy alone leaves open on state-changing routes.
    """
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return JSONResponse({"error": "Content-Type must be application/json"}, status_code=415)

    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    docker_coll = state.collectors.get("docker")
    if not docker_coll:
        return JSONResponse({"error": "docker not available"}, status_code=503)

    data = await docker_coll.restart_container(name)
    return JSONResponse(data)


async def api_plugins(request: Request) -> JSONResponse:
    state: BuoyAppState = request.app.state.buoy
    """All plugin panel data."""
    if not state.plugin_manager:
        return JSONResponse({"plugins": []})
    data = await state.plugin_manager.collect_all_now()
    return JSONResponse({"plugins": list(data.values())})


async def api_plugin_js(request: Request) -> Response:
    state: BuoyAppState = request.app.state.buoy
    """Return custom frontend JS for all plugins that provide it."""
    if not state.plugin_manager:
        return Response("", media_type="application/javascript")
    js_map = state.plugin_manager.get_plugin_frontend_js()
    combined = "\n\n".join(js_map.values())
    return Response(combined, media_type="application/javascript")


def _prometheus_enabled(config: BuoyConfig) -> bool:
    """Return True only when the prometheus_exporter builtin plugin is enabled.

    Mirrors the gate used in PluginManager._load_builtins: both
    ``plugins.enabled`` (global toggle) and the per-plugin ``enabled`` flag
    must be true.
    """
    if config is None or not config.plugins.enabled:
        return False
    entry = config.plugins.builtin.get("prometheus_exporter")
    return bool(entry and entry.enabled)


async def api_metrics(request: Request) -> Response:
    state: BuoyAppState = request.app.state.buoy
    """Prometheus /metrics endpoint.

    Only reachable when the ``prometheus_exporter`` builtin plugin is enabled
    (``plugins.enabled=true`` AND ``plugins.builtin.prometheus_exporter.enabled=true``).
    The route is not registered at all when the plugin is disabled; this
    defensive guard handles the edge case of a test or direct call with a
    disabled config.
    """
    if not _prometheus_enabled(state.config):
        return JSONResponse({"error": "not found"}, status_code=404)

    from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

    # Collect current stats
    system_coll = state.collectors.get("system")
    docker_coll = state.collectors.get("docker")
    disk_coll = state.collectors.get("disk")

    results = await asyncio.gather(
        system_coll.collect() if system_coll else _empty_system(state.config),
        docker_coll.collect_summary() if docker_coll else _empty_docker(),
        disk_coll.collect_summary() if disk_coll else _empty_disk(),
        return_exceptions=True,
    )

    system_data = results[0] if not isinstance(results[0], Exception) else {}
    docker_data = results[1] if not isinstance(results[1], Exception) else {}
    disk_data = results[2] if not isinstance(results[2], Exception) else {}
    combined = {**system_data, **docker_data, **disk_data}

    body = PrometheusExporterPlugin.format_metrics(combined)
    return Response(body, media_type="text/plain; version=0.0.4; charset=utf-8")


async def api_fleet_latency_history(request: Request) -> JSONResponse:
    state: BuoyAppState = request.app.state.buoy
    """Per-peer latency history (if history enabled)."""
    if not state.config.features.history or not state.metric_store:
        return JSONResponse({"error": "history feature not enabled"}, status_code=404)

    peer = request.path_params["peer"]
    allowed = {p.name for p in state.config.network.peers}
    if peer not in allowed:
        return JSONResponse({"error": "unknown peer"}, status_code=404)

    try:
        hours = max(1, min(6, int(request.query_params.get("hours", "6"))))
    except (ValueError, TypeError):
        hours = 6

    data = await asyncio.to_thread(state.metric_store.query_latency, peer, hours * 3600)
    return JSONResponse({"peer": peer, "hours": hours, "data": data})


async def api_history(request: Request) -> JSONResponse:
    state: BuoyAppState = request.app.state.buoy
    """24h time-series for a metric (if history enabled)."""
    metric = request.path_params.get("metric", "cpu")
    if not state.config.features.history or not state.metric_store:
        return JSONResponse({"error": "history feature not enabled"}, status_code=404)

    # Parse period query param
    period_str = request.query_params.get("period", "1h")
    period_map = {"1h": 3600, "6h": 21600, "12h": 43200, "24h": 86400}
    period_seconds = period_map.get(period_str, 3600)

    valid_metrics = {"cpu", "mem", "temp", "disk", "containers"}
    if metric not in valid_metrics:
        return JSONResponse(
            {"error": f"invalid metric, must be one of: {valid_metrics}"}, status_code=400
        )

    data = await asyncio.to_thread(state.metric_store.query, metric, period_seconds)
    return JSONResponse({"metric": metric, "period": period_str, "data": data})


# ── WebSocket ──────────────────────────────────────────────────────────────────


async def ws_endpoint(websocket: WebSocket):
    """WebSocket for real-time stats push."""
    state: BuoyAppState = websocket.app.state.buoy
    await websocket.accept()
    state.ws_clients.add(websocket)
    try:
        while True:
            # Keep connection alive, handle client messages
            data = await websocket.receive_text()
            try:
                msg = json.loads(data)
                if msg.get("type") == "ping":
                    await websocket.send_json({"type": "pong"})
            except json.JSONDecodeError:
                pass
    except WebSocketDisconnect:
        pass
    except Exception:
        logger.debug("ws_endpoint: client connection failed", exc_info=True)
    finally:
        state.ws_clients.discard(websocket)


def _consume_log_ticket(state: BuoyAppState, ticket: str, name: str) -> bool:
    """Look up and immediately invalidate a log-stream ticket.

    Single-use (popped regardless of outcome), TTL-checked, and bound to
    the container name it was issued for.
    """
    if not ticket:
        return False
    entry = state.log_tickets.pop(ticket, None)
    if entry is None:
        return False
    ticket_name, expiry = entry
    return ticket_name == name and time.monotonic() <= expiry


class _DropOldestQueue:
    """Bounded FIFO buffer of pending log items; overflow drops the oldest entry.

    Decouples the `docker logs --follow` pump from a slow client: the
    producer never blocks on a stalled WebSocket send (which would otherwise
    also stall the docker CLI's stdout pipe), and the count of dropped lines
    is surfaced to the client explicitly via a `log_dropped` frame instead of
    silently losing data or growing memory without bound.
    """

    def __init__(self, maxsize: int):
        self._items: collections.deque = collections.deque()
        self._maxsize = maxsize
        self._dropped = 0
        self._event = asyncio.Event()

    def push(self, item: dict) -> None:
        if len(self._items) >= self._maxsize:
            self._items.popleft()
            self._dropped += 1
        self._items.append(item)
        self._event.set()

    def wake(self) -> None:
        self._event.set()

    async def wait_not_empty(self) -> None:
        if self._items:
            return
        self._event.clear()
        await self._event.wait()

    def drain(self) -> tuple[list[dict], int]:
        items = list(self._items)
        self._items.clear()
        dropped, self._dropped = self._dropped, 0
        return items, dropped

    def drain_up_to(self, n: int) -> tuple[list[dict], int]:
        """Like `drain()`, but leaves anything beyond `n` items queued —
        used to pace output to `logs.stream_rate_limit` without discarding
        the backlog (it stays subject to normal drop-oldest on overflow)."""
        items = [self._items.popleft() for _ in range(min(n, len(self._items)))]
        dropped, self._dropped = self._dropped, 0
        return items, dropped

    def has_pending(self) -> bool:
        return bool(self._items)


_LOG_STREAM_BATCH_INTERVAL = 0.1
_LOG_STREAM_BATCH_MAX_LINES = 100
_LOG_STREAM_QUEUE_MAX_LINES = 2000


async def ws_container_logs(websocket: WebSocket):
    """WebSocket for live `docker logs --follow` streaming.

    `BaseHTTPMiddleware` (both `AuthMiddleware` and `RateLimitMiddleware`)
    only wraps `http` scopes and never runs for `websocket` connections, so
    this handler re-implements the auth check explicitly via a short-lived
    ticket (see `api_container_logs_ticket`) rather than relying on the
    middleware stack — a naive route here would otherwise serve logs
    unauthenticated even when `auth.enabled` is true.
    """
    state: BuoyAppState = websocket.app.state.buoy
    config = state.config
    name = websocket.path_params.get("name", "")

    if not _validate_container_name(name):
        await websocket.close(code=4400, reason="invalid container name")
        return

    if not config.features.log_streaming:
        await websocket.close(code=4403, reason="log streaming disabled")
        return

    if config.auth.enabled:
        ticket = websocket.query_params.get("ticket", "")
        if not _consume_log_ticket(state, ticket, name):
            await websocket.close(code=4401, reason="authentication required")
            return

    docker_coll = state.collectors.get("docker")
    if not docker_coll:
        await websocket.close(code=4404, reason="docker not available")
        return

    if state.log_stream_count >= config.logs.max_streams:
        await websocket.close(code=4429, reason="too many concurrent log streams")
        return

    tail = _clamp_tail(websocket.query_params.get("tail"), config)

    await websocket.accept()
    state.log_stream_count += 1

    queue = _DropOldestQueue(_LOG_STREAM_QUEUE_MAX_LINES)
    stop = asyncio.Event()
    producer_error: Exception | None = None

    async def _produce() -> None:
        nonlocal producer_error
        try:
            async with contextlib.aclosing(
                docker_coll.stream_logs(name, tail=tail, max_line_bytes=config.logs.max_line_bytes)
            ) as stream:
                async for item in stream:
                    if stop.is_set():
                        break
                    queue.push(item)
        except Exception as exc:
            producer_error = exc
            logger.warning("ws_container_logs: producer failed for %s: %s", name, exc)
        finally:
            stop.set()
            queue.wake()

    producer = asyncio.ensure_future(_produce())
    recv_task = asyncio.ensure_future(websocket.receive_text())
    reason = "closed"

    # `stream_rate_limit` (lines/sec, <=0 disables it) is a soft cap enforced
    # here by pacing how many items we drain per rolling 1s window — items
    # beyond the budget stay queued (and remain subject to normal
    # drop-oldest) rather than being discarded outright.
    rate_limit = config.logs.stream_rate_limit
    rate_window_start = time.monotonic()
    rate_window_sent = 0

    try:
        await websocket.send_json({"type": "log_start", "container": name, "tail": tail})
        while True:
            now = time.monotonic()
            if now - rate_window_start >= 1.0:
                rate_window_start = now
                rate_window_sent = 0
            budget = None if rate_limit <= 0 else max(0, rate_limit - rate_window_sent)

            waitables = {recv_task}
            wait_task = None
            if budget != 0:
                wait_task = asyncio.ensure_future(queue.wait_not_empty())
                waitables.add(wait_task)
            done, _pending = await asyncio.wait(
                waitables,
                timeout=_LOG_STREAM_BATCH_INTERVAL,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if wait_task is not None and not wait_task.done():
                wait_task.cancel()

            if recv_task in done:
                with contextlib.suppress(Exception):
                    recv_task.result()
                reason = "disconnected"
                break

            items, dropped = queue.drain() if budget is None else queue.drain_up_to(budget)
            rate_window_sent += len(items)
            for i in range(0, len(items), _LOG_STREAM_BATCH_MAX_LINES):
                await websocket.send_json(
                    {"type": "log", "lines": items[i : i + _LOG_STREAM_BATCH_MAX_LINES]}
                )
            if dropped:
                await websocket.send_json({"type": "log_dropped", "count": dropped})

            if stop.is_set() and not items and not dropped and not queue.has_pending():
                reason = "stream error" if producer_error is not None else "container exited"
                break
    except WebSocketDisconnect:
        reason = "disconnected"
    except Exception:
        logger.debug("ws_container_logs: send loop failed for %s", name, exc_info=True)
        reason = "error"
    finally:
        stop.set()
        state.log_stream_count -= 1
        recv_task.cancel()
        producer.cancel()
        await asyncio.gather(recv_task, producer, return_exceptions=True)
        with contextlib.suppress(Exception):
            await websocket.send_json({"type": "log_end", "reason": reason})
        with contextlib.suppress(Exception):
            await websocket.close(code=1000, reason=reason)


async def broadcast_stats(state: BuoyAppState, data: dict):
    """Push stats update to this app's connected WebSocket clients."""
    if not state.ws_clients:
        return
    message = json.dumps({"type": "stats", "data": data})
    disconnected = set()
    for ws in list(state.ws_clients):
        try:
            await ws.send_text(message)
        except Exception:
            logger.debug("broadcast_stats: dropping dead client", exc_info=True)
            disconnected.add(ws)
    state.ws_clients.difference_update(disconnected)


async def broadcast_alert(state: BuoyAppState, alert_data: dict):
    """Push an alert notification to this app's connected WebSocket clients."""
    if not state.ws_clients:
        return
    message = json.dumps(alert_data)
    disconnected = set()
    for ws in list(state.ws_clients):
        try:
            await ws.send_text(message)
        except Exception:
            logger.debug("broadcast_alert: dropping dead client", exc_info=True)
            disconnected.add(ws)
    state.ws_clients.difference_update(disconnected)


# ── Background Tasks ───────────────────────────────────────────────────────────

PRUNE_EVERY_CYCLES = 100  # ~500s at the default 5s stats_interval


async def _stats_loop(state: BuoyAppState):
    """Periodically collect, broadcast, store, and evaluate alerts."""
    cycle = 0
    while True:
        await asyncio.sleep(state.config.refresh.stats_interval)
        cycle += 1
        try:
            system_coll = state.collectors.get("system")
            docker_coll = state.collectors.get("docker")
            disk_coll = state.collectors.get("disk")

            results = await asyncio.gather(
                system_coll.collect() if system_coll else _empty_system(state.config),
                docker_coll.collect_summary() if docker_coll else _empty_docker(),
                disk_coll.collect_summary() if disk_coll else _empty_disk(),
                return_exceptions=True,
            )

            system_data = results[0] if not isinstance(results[0], Exception) else {}
            docker_data = results[1] if not isinstance(results[1], Exception) else {}
            disk_data = results[2] if not isinstance(results[2], Exception) else {}

            combined = {**system_data, **docker_data, **disk_data}

            # Decorate containers with update status from cache (pure dict lookup)
            if state.image_update_cache and "containers_list" in combined:
                for ctr in combined["containers_list"]:
                    entry = state.image_update_cache.get(ctr["name"])
                    if entry:
                        ctr["update_status"] = entry["status"]

            # Same "alerts" field api_stats() already includes (BUG-12) — a
            # client relying on the WebSocket push (the common case once
            # connected) otherwise never saw active-alerts state at all
            # outside of the transient toast fired at the moment an alert
            # changes, so reconnecting or loading mid-incident showed nothing.
            combined["alerts"] = (
                [a.to_dict() for a in state.alert_engine.active_alerts]
                if state.alert_engine
                else []
            )

            # Broadcast to WebSocket clients (only when websocket feature enabled)
            if state.config.features.websocket:
                await broadcast_stats(state, combined)

            # Store in history (if enabled)
            if state.metric_store:
                await asyncio.to_thread(state.metric_store.record, "stats", combined)
                # Sample container states every ~30s (every 6th cycle at 5s interval)
                if docker_coll and cycle % 6 == 0:
                    try:
                        states = await docker_coll.list_container_states()
                        if states:
                            await asyncio.to_thread(
                                state.metric_store.record_container_states, states
                            )
                    except Exception:
                        logger.debug("stats loop: container state sampling failed", exc_info=True)
                # Prune on a fixed cycle cadence, never twice-in-a-row or skipped
                if cycle % PRUNE_EVERY_CYCLES == 0:
                    await asyncio.to_thread(state.metric_store.prune)

            # Evaluate alert thresholds
            if state.alert_engine:
                await state.alert_engine.evaluate(combined)
        except Exception:
            logger.warning("stats loop iteration failed", exc_info=True)


def _record_latency_batch(store: MetricStore, results: list[dict]):
    """Sync helper: persist a batch of latency readings in one thread hop and one commit."""
    store.record_latency_batch([(r["name"], r["latency_ms"]) for r in results])


async def _latency_loop(state: BuoyAppState):
    """Periodically measure and store per-peer latency."""
    while True:
        await asyncio.sleep(state.config.refresh.fleet_interval)
        try:
            network_coll = state.collectors.get("network")
            store = state.metric_store
            if network_coll and store:
                results = await network_coll.measure_latency()
                if results:
                    await asyncio.to_thread(_record_latency_batch, store, results)
        except Exception:
            logger.warning("latency loop iteration failed", exc_info=True)


async def _image_update_loop(state: BuoyAppState, checker: Any):
    """Periodically check running container images against their registries."""
    # Run initial check immediately on startup
    try:
        state.image_update_cache = await checker.check_all()
    except Exception:
        logger.warning("image update check failed", exc_info=True)
    while True:
        await asyncio.sleep(state.config.refresh.image_updates_interval)
        try:
            state.image_update_cache = await checker.check_all()
        except Exception:
            logger.warning("image update check failed", exc_info=True)


# ── Lifecycle ──────────────────────────────────────────────────────────────────


async def on_startup(state: BuoyAppState):
    """Initialize this app's collectors, plugins, storage, alerts, and loops."""
    if state.plugin_manager is not None or state.metric_store is not None:
        raise RuntimeError("cannot start app while previous shutdown cleanup is incomplete")

    if state.config.features.demo_mode:
        from buoy.demo import DemoDiskCollector, DemoDockerCollector, DemoSystemCollector

        state.collectors["system"] = DemoSystemCollector(state.config)
        state.collectors["docker"] = DemoDockerCollector(state.config)
        state.collectors["disk"] = DemoDiskCollector(state.config)
    else:
        from buoy.collectors.disk import DiskCollector
        from buoy.collectors.docker import DockerCollector
        from buoy.collectors.network import NetworkCollector
        from buoy.collectors.system import SystemCollector

        state.collectors["system"] = SystemCollector(state.config)
        state.collectors["docker"] = DockerCollector(state.config)
        state.collectors["disk"] = DiskCollector(state.config)
        state.collectors["network"] = NetworkCollector(state.config)

    # Initialize metric history store (if enabled)
    if state.config.features.history:
        from buoy.storage import MetricStore

        state.metric_store = MetricStore(state.config)
        state.metric_store.open()
        logger.info("History storage enabled (SQLite ring buffer)")

    # Initialize alert engine with a callback bound to this app's state.
    from buoy.alerts import AlertEngine

    async def broadcast_app_alert(alert_data: dict) -> None:
        await broadcast_alert(state, alert_data)

    state.alert_engine = AlertEngine(state.config, broadcast_fn=broadcast_app_alert)

    # Start stats collection loop (needed for history persistence, alerts, and websocket broadcast)
    if state.config.features.websocket or state.config.features.history:
        state.background_tasks.append(asyncio.create_task(_stats_loop(state)))
        logger.info(
            "Stats collection loop enabled (history=%s websocket=%s)",
            state.config.features.history,
            state.config.features.websocket,
        )

    # Start latency collection loop (only when network collector and history are both present)
    if state.collectors.get("network") and state.metric_store:
        state.background_tasks.append(asyncio.create_task(_latency_loop(state)))

    # Start image update checker (if enabled)
    if state.config.features.image_updates:
        if state.config.features.demo_mode:
            from buoy.demo import DemoImageUpdateChecker

            image_checker = DemoImageUpdateChecker(state.config)
        else:
            from buoy.collectors.image_updates import ImageUpdateChecker

            image_checker = ImageUpdateChecker(state.config)
        state.background_tasks.append(asyncio.create_task(_image_update_loop(state, image_checker)))
        logger.info(
            "Image update checker enabled (interval: %ss)",
            state.config.refresh.image_updates_interval,
        )

    # PluginManager owns its own plugin collection tasks; keep them separate
    # from the server loops tracked above.
    from buoy.plugins.loader import PluginManager

    state.plugin_manager = PluginManager(state.config)
    await state.plugin_manager.start()


async def on_shutdown(state: BuoyAppState):
    """Stop and reset only this app's runtime resources.

    Owners are cleared only after their cleanup succeeds. If plugin or storage
    cleanup fails, retain that owner so teardown can be retried and startup can
    refuse to overwrite a potentially live resource.
    """
    try:
        try:
            for task in state.background_tasks:
                task.cancel()
            if state.background_tasks:
                await asyncio.gather(*state.background_tasks, return_exceptions=True)
        finally:
            try:
                if state.plugin_manager:
                    await state.plugin_manager.stop()
                    state.plugin_manager = None
            finally:
                if state.metric_store:
                    state.metric_store.close()
                    state.metric_store = None
    finally:
        state.alert_engine = None
        state.collectors.clear()
        state.ws_clients.clear()
        state.image_update_cache.clear()
        state.background_tasks.clear()
        state.log_tickets.clear()
        state.log_stream_count = 0


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    """Run startup and guaranteed teardown for the given application only."""
    state: BuoyAppState = app.state.buoy
    try:
        await on_startup(state)
        yield
    finally:
        await on_shutdown(state)


# ── Helpers ────────────────────────────────────────────────────────────────────

_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")

_SECRET_KEY_FRAGMENTS = {"token", "password", "secret", "key"}


def _redact_secrets(obj):
    """Recursively replace secret-bearing string values with a redaction marker.

    Only string values are redacted (booleans/ints with "key" in the name are left alone).
    """
    if isinstance(obj, dict):
        return {
            k: "***REDACTED***"
            if isinstance(v, str) and v and any(frag in k.lower() for frag in _SECRET_KEY_FRAGMENTS)
            else _redact_secrets(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [_redact_secrets(item) for item in obj]
    return obj


def _resolve_static_dir() -> Path:
    """Resolve the static files directory.

    Checks (in order):
    1. /app/static — Docker container (Dockerfile copies static/ here)
    2. Adjacent to this module — installed wheel (force-include maps static/ → buoy/static)
    3. Repo root relative to source — local / editable dev install
    """
    # 1. Docker container path
    docker_path = Path("/app/static")
    if docker_path.exists():
        return docker_path
    # 2. Installed wheel: hatch force-include puts static/ at buoy/static (next to server.py)
    packaged = Path(__file__).parent / "static"
    if packaged.exists():
        return packaged
    # 3. Development / editable install: repo-root static/
    #    (src/buoy/server.py → src/buoy → src → project root)
    return Path(__file__).parent.parent.parent / "static"


def _validate_container_name(name: str) -> bool:
    """Validate container name to prevent injection."""
    return bool(_CONTAINER_NAME_RE.match(name)) and len(name) <= 128


async def _empty_system(config: BuoyConfig):
    return {
        "hostname": config.node.name,
        "cpu": 0,
        "mem_used": 0,
        "mem_total": 0,
        "temp": 0,
        "uptime_h": 0,
        "uptime_m": 0,
        "uptime_s": 0,
    }


async def _empty_docker():
    return {"containers": 0, "containers_list": []}


async def _empty_disk():
    return {"disk_pct": 0}


async def _empty_detail():
    return {"cpu": {}, "memory": {}}


async def _empty_disk_detail():
    return {"mounts": [], "io_read_gb": 0, "io_write_gb": 0}


# ── Index route (serves static/index.html) ────────────────────────────────────


async def index(request: Request) -> Response:
    """Serve the dashboard HTML, rewriting asset URLs for the configured base path."""
    state: BuoyAppState = request.app.state.buoy
    static_dir = _resolve_static_dir()
    index_path = static_dir / "index.html"
    if not index_path.exists():
        return Response("index.html not found", status_code=500)

    html = index_path.read_text()
    base = state.config.network.base_path
    if base:
        html = html.replace('="/static/', f'="{html_module.escape(base, quote=True)}/static/')
    html = html.replace(
        '<meta name="buoy-base-path" content="">',
        f'<meta name="buoy-base-path" content="{html_module.escape(base, quote=True)}">',
    )
    return Response(
        content=html,
        media_type="text/html",
    )


# ── App Factory ────────────────────────────────────────────────────────────────


# 'unsafe-eval' is required by the plugin custom-JS renderer (new Function(),
# static/js/plugins.js) and 'unsafe-inline' in style-src by the pervasive
# inline style="..." attributes across the dashboard templates. Both are
# tracked for removal under PP-5 (sandboxed plugin renderer), at which point
# this policy should tighten to drop them. The JetBrains Mono / Outfit
# webfonts are self-hosted (static/fonts/, BUG-48) rather than loaded from
# Google Fonts, so font-src/style-src don't need an external allowlist for
# them. connect-src includes configured fleet peer origins since the fleet
# grid fetches each peer's /api/stats directly from the browser
# (static/js/fleet.js).
_CSP_NETLOC_RE = re.compile(r"^[A-Za-z0-9.\-\[\]:]+$")


def _csp_origin(url: str) -> str | None:
    """Reduce a peer URL to a bare scheme://host[:port] origin for connect-src.

    Peer URLs come from operator-controlled config, not validated on input
    (PeerConfig.url is a free-form string), so this strips paths/queries and
    rejects non-http(s) schemes to prevent them from injecting extra CSP
    directives when interpolated. urlsplit() also silently drops embedded
    \\t\\r\\n from the netloc rather than rejecting the URL, so the netloc is
    further restricted to a strict host[:port]/IPv6-bracket charset — this
    blocks e.g. "https://evil.example\\nscript-src *" from smuggling a
    space-separated extra source token (like a "*" wildcard) into connect-src.
    """
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if not _CSP_NETLOC_RE.match(parts.netloc):
        return None
    return f"{parts.scheme}://{parts.netloc}"


def _build_csp_policy(peer_urls: list[str]) -> str:
    origins = [origin for url in peer_urls if (origin := _csp_origin(url)) is not None]
    connect_src = " ".join(["'self'"] + list(dict.fromkeys(origins)))
    return (
        "default-src 'self'; "
        "script-src 'self' 'unsafe-eval'; "
        "style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; "
        "img-src 'self' data:; "
        f"connect-src {connect_src}; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )


def _validate_auth_config(config: BuoyConfig) -> None:
    """Fail fast when enabled authentication is incomplete or invalid."""
    if not config.auth.enabled:
        return

    auth = config.auth
    if auth.type == "token":
        if not auth.token:
            raise RuntimeError(
                "auth.enabled is true but auth.token is not set "
                "(set BUOY_AUTH_TOKEN or auth.token in buoy.yaml). Refusing to start."
            )
    elif auth.type == "basic":
        if not auth.username or not auth.password:
            raise RuntimeError(
                "auth.enabled is true but auth.username/auth.password are not "
                "both set. Refusing to start."
            )
    else:
        raise RuntimeError(f"auth.enabled is true but auth.type is invalid: {auth.type!r}")


def create_app(config: BuoyConfig) -> Starlette:
    """Create the Starlette application."""
    _validate_auth_config(config)

    from buoy.logging_setup import setup_logging

    setup_logging(config.logging.level)

    static_dir = _resolve_static_dir()

    routes = [
        Route("/", index),
        Route("/api/health", api_health),
        Route("/api/config", api_config),
        Route("/api/config/debug", api_config_debug),
        Route("/api/deploy-info", api_deploy_info),
        Route("/api/stats", api_stats),
        Route("/api/stats/detail", api_stats_detail),
        Route("/api/services", api_services),
        Route("/api/fleet", api_fleet),
        Route("/api/fleet/{peer}/latency-history", api_fleet_latency_history),
        Route("/api/plugins", api_plugins),
        Route("/api/plugins/js", api_plugin_js),
        Route("/api/history/{metric}", api_history),
        Route("/api/container/{name}/history", api_container_history),
        Route("/api/container/{name}", api_container_detail),
        Route("/api/container/{name}/logs", api_container_logs),
        Route("/api/container/{name}/logs/ticket", api_container_logs_ticket),
        Route("/api/container/{name}/restart", api_container_restart, methods=["POST"]),
        WebSocketRoute("/ws", ws_endpoint),
        WebSocketRoute("/ws/logs/{name}", ws_container_logs),
        Mount("/static", StaticFiles(directory=str(static_dir)), name="static"),
    ]

    # /metrics is only registered when the prometheus_exporter plugin is enabled.
    # Inserting before the catch-all static mount keeps route ordering intact.
    if _prometheus_enabled(config):
        routes.insert(-1, Route("/metrics", api_metrics))

    # When base_path is set, mount the same routes under the prefix too, so
    # both proxy styles work: a non-stripping proxy delivers e.g.
    # "/buoy/api/stats" (matches the Mount below), while a stripping proxy
    # (Caddy handle_path, Traefik StripPrefix) delivers "/api/stats" (matches
    # the root routes). Route/Mount objects are pure matchers, safe to
    # reference from two places.
    if config.network.base_path:
        routes = [*routes, Mount(config.network.base_path, routes=routes)]

    # Same-origin by default (no CORS middleware = browsers block cross-origin
    # reads). Cross-origin access is opt-in via an explicit origin allowlist
    # (e.g. for fleet peers) — never a wildcard, per SPEC §7.2.
    middleware = []

    # ProxyHeadersMiddleware must be outermost so all downstream middleware
    # and handlers see the corrected scope["client"] and host header.
    # With trusted_proxies=[] (default) this is a no-op.
    from buoy.auth import ProxyHeadersMiddleware

    middleware.append(
        Middleware(ProxyHeadersMiddleware, trusted_proxies=config.network.trusted_proxies)
    )

    if config.network.allowed_origins:
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=config.network.allowed_origins,
                allow_methods=["GET", "POST", "OPTIONS"],
                allow_headers=["Authorization", "Content-Type"],
            )
        )

    # Security headers middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    csp_policy = _build_csp_policy([p.url for p in config.network.peers if p.url])

    from buoy.auth import strip_base_path

    base_path = config.network.base_path

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = csp_policy
            path = strip_base_path(request.url.path, base_path)
            if not path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

    middleware.append(Middleware(SecurityHeadersMiddleware))

    # Rate limiting is always active on protected endpoints (SPEC §7.2),
    # independent of whether auth is enabled.
    from buoy.auth import RateLimitMiddleware

    middleware.append(Middleware(RateLimitMiddleware, base_path=base_path))

    # Add auth middleware if enabled. Validation ran before runtime state was
    # constructed, so a failed factory call cannot affect another application.
    if config.auth.enabled:
        from buoy.auth import AuthMiddleware

        middleware.append(Middleware(AuthMiddleware, auth_config=config.auth, base_path=base_path))

    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )

    app.state.buoy = BuoyAppState(config=config)
    return app


def _factory() -> Starlette:
    """Zero-argument factory for uvicorn reload mode (``python -m buoy --dev``)."""
    import os

    from buoy.config import load_config

    path = os.environ.get("BUOY_CONFIG") or None
    demo = os.environ.get("BUOY_DEMO") == "1"
    return create_app(load_config(path=path, demo=demo))
