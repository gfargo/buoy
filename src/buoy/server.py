"""Buoy server — Starlette application with API routes and WebSocket support."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import hmac
import json
import logging
import re
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

from buoy.subprocess_utils import communicate

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.server")

# ── Globals (set during app creation) ──────────────────────────────────────────

_config: BuoyConfig | None = None
_collectors: dict = {}
_ws_clients: set[WebSocket] = set()
_plugin_manager = None
_metric_store = None
_alert_engine = None
_image_update_cache: dict = {}  # {container_name: {"status": ..., "image": ..., "checked_at": ts}}
_background_tasks: list[asyncio.Task] = []


# ── API Handlers ───────────────────────────────────────────────────────────────


def _is_tailscale(request: Request) -> bool:
    """Return True when the request's Host header indicates a Tailscale network."""
    host = request.headers.get("host", "").split(":", 1)[0].lower()
    if host == "ts.net" or host.endswith(".ts.net"):
        return True

    tailnet_domain = (_config.network.tailnet_domain if _config else "").strip(".").lower()
    return bool(tailnet_domain) and (host == tailnet_domain or host.endswith(f".{tailnet_domain}"))


async def api_health(request: Request) -> JSONResponse:
    """Health check endpoint."""
    return JSONResponse(
        {
            "status": "ok",
            "hostname": _config.node.name,
            "version": "2.0.0-alpha.1",
        }
    )


async def api_config(request: Request) -> JSONResponse:
    """Public config subset — no secrets, just display/feature info."""
    return JSONResponse(
        {
            "node": {
                "name": _config.node.name,
                "tier": _config.node.tier,
                "role": _config.node.role,
            },
            "network": {
                "tailnet_domain": _config.network.tailnet_domain,
                "peers": [
                    {"name": p.name, "url": p.url, "tier": p.tier} for p in _config.network.peers
                ],
            },
            "theme": {
                "preset": _config.theme.preset,
                "custom": _config.theme.custom,
            },
            "features": {
                "websocket": _config.features.websocket,
                "history": _config.features.history,
                "demo_mode": _config.features.demo_mode,
                "night_mode": _config.features.night_mode,
                "keyboard_shortcuts": _config.features.keyboard_shortcuts,
                "image_updates": _config.features.image_updates,
            },
            "refresh": {
                "stats_interval": _config.refresh.stats_interval,
                "services_interval": _config.refresh.services_interval,
                "fleet_interval": _config.refresh.fleet_interval,
                "plugins_interval": _config.refresh.plugins_interval,
                "image_updates_interval": _config.refresh.image_updates_interval,
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
    token = _config.auth.token
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
    if not hmac.compare_digest(provided, token):
        return JSONResponse(
            {"error": "authentication required"},
            status_code=401,
            headers={"WWW-Authenticate": 'Bearer realm="buoy"'},
        )

    return JSONResponse(_redact_secrets(dataclasses.asdict(_config)))


async def api_deploy_info(request: Request) -> JSONResponse:
    """Deployment metadata — version, build time, git SHA."""
    import buoy

    info: dict = {"version": buoy.__version__}

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
    from buoy.services import top_services

    system_coll = _collectors.get("system")
    docker_coll = _collectors.get("docker")
    disk_coll = _collectors.get("disk")

    is_tailscale = _is_tailscale(request)

    # Gather all stats concurrently
    results = await asyncio.gather(
        system_coll.collect() if system_coll else _empty_system(),
        docker_coll.collect_summary() if docker_coll else _empty_docker(),
        disk_coll.collect_summary() if disk_coll else _empty_disk(),
        top_services(_config, is_tailscale, collector=docker_coll),
        return_exceptions=True,
    )

    system_data = results[0] if not isinstance(results[0], Exception) else {}
    docker_data = results[1] if not isinstance(results[1], Exception) else {}
    disk_data = results[2] if not isinstance(results[2], Exception) else {}
    services = results[3] if not isinstance(results[3], Exception) else []

    # Decorate each container entry with update status from cache (pure dict lookup)
    if _image_update_cache and "containers_list" in docker_data:
        for ctr in docker_data["containers_list"]:
            entry = _image_update_cache.get(ctr["name"])
            if entry:
                ctr["update_status"] = entry["status"]

    alerts = [a.to_dict() for a in _alert_engine.active_alerts] if _alert_engine else []
    return JSONResponse(
        {**system_data, **docker_data, **disk_data, "top_services": services, "alerts": alerts}
    )


async def api_stats_detail(request: Request) -> JSONResponse:
    """Extended metrics — per-core CPU, top processes, mount details."""

    system_coll = _collectors.get("system")
    disk_coll = _collectors.get("disk")

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
    from buoy.services import discover_services

    is_tailscale = _is_tailscale(request)
    data = await discover_services(_config, is_tailscale, collector=_collectors.get("docker"))
    return JSONResponse(data)


async def api_fleet(request: Request) -> JSONResponse:
    """Aggregated peer node stats."""

    network_coll = _collectors.get("network")
    if not network_coll:
        return JSONResponse({"peers": []})

    data = await network_coll.collect()
    return JSONResponse(data)


async def api_container_history(request: Request) -> JSONResponse:
    """24h up/down history for a single container (if history enabled)."""
    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    if not _config.features.history or not _metric_store:
        return JSONResponse({"error": "history feature not enabled"}, status_code=404)

    hours_str = request.query_params.get("hours", "24")
    try:
        hours = max(1, min(int(hours_str), 24))
    except (ValueError, TypeError):
        hours = 24

    samples = await asyncio.to_thread(_metric_store.query_container_history, name, hours * 3600)
    return JSONResponse(
        {
            "container": name,
            "hours": hours,
            "samples": [{"ts": ts, "status": st, "restart_count": rc} for ts, st, rc in samples],
        }
    )


async def api_container_detail(request: Request) -> JSONResponse:
    """Container inspect + resource usage."""
    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    docker_coll = _collectors.get("docker")
    if not docker_coll:
        return JSONResponse({"error": "docker not available"}, status_code=503)

    data = await docker_coll.inspect_container(name)
    return JSONResponse(data)


async def api_container_logs(request: Request) -> JSONResponse:
    """Last N lines of container stdout/stderr."""
    name = request.path_params["name"]
    if not _validate_container_name(name):
        return JSONResponse({"error": "invalid container name"}, status_code=400)

    docker_coll = _collectors.get("docker")
    if not docker_coll:
        return JSONResponse({"error": "docker not available"}, status_code=503)

    data = await docker_coll.get_logs(name, tail=30)
    return JSONResponse(data)


async def api_container_restart(request: Request) -> JSONResponse:
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

    docker_coll = _collectors.get("docker")
    if not docker_coll:
        return JSONResponse({"error": "docker not available"}, status_code=503)

    data = await docker_coll.restart_container(name)
    return JSONResponse(data)


async def api_plugins(request: Request) -> JSONResponse:
    """All plugin panel data."""
    if not _plugin_manager:
        return JSONResponse({"plugins": []})
    data = await _plugin_manager.collect_all_now()
    return JSONResponse({"plugins": list(data.values())})


async def api_plugin_js(request: Request) -> Response:
    """Return custom frontend JS for all plugins that provide it."""
    if not _plugin_manager:
        return Response("", media_type="application/javascript")
    js_map = _plugin_manager.get_plugin_frontend_js()
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
    """Prometheus /metrics endpoint.

    Only reachable when the ``prometheus_exporter`` builtin plugin is enabled
    (``plugins.enabled=true`` AND ``plugins.builtin.prometheus_exporter.enabled=true``).
    The route is not registered at all when the plugin is disabled; this
    defensive guard handles the edge case of a test or direct call with a
    disabled config.
    """
    if not _prometheus_enabled(_config):
        return JSONResponse({"error": "not found"}, status_code=404)

    from buoy.plugins.builtin.prometheus_exporter import PrometheusExporterPlugin

    # Collect current stats
    system_coll = _collectors.get("system")
    docker_coll = _collectors.get("docker")
    disk_coll = _collectors.get("disk")

    results = await asyncio.gather(
        system_coll.collect() if system_coll else _empty_system(),
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
    """Per-peer latency history (if history enabled)."""
    if not _config.features.history or not _metric_store:
        return JSONResponse({"error": "history feature not enabled"}, status_code=404)

    peer = request.path_params["peer"]
    allowed = {p.name for p in _config.network.peers}
    if peer not in allowed:
        return JSONResponse({"error": "unknown peer"}, status_code=404)

    try:
        hours = max(1, min(6, int(request.query_params.get("hours", "6"))))
    except (ValueError, TypeError):
        hours = 6

    data = await asyncio.to_thread(_metric_store.query_latency, peer, hours * 3600)
    return JSONResponse({"peer": peer, "hours": hours, "data": data})


async def api_history(request: Request) -> JSONResponse:
    """24h time-series for a metric (if history enabled)."""
    metric = request.path_params.get("metric", "cpu")
    if not _config.features.history or not _metric_store:
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

    data = await asyncio.to_thread(_metric_store.query, metric, period_seconds)
    return JSONResponse({"metric": metric, "period": period_str, "data": data})


# ── WebSocket ──────────────────────────────────────────────────────────────────


async def ws_endpoint(websocket: WebSocket):
    """WebSocket for real-time stats push."""
    await websocket.accept()
    _ws_clients.add(websocket)
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
        _ws_clients.discard(websocket)
    except Exception:
        logger.debug("ws_endpoint: client connection failed", exc_info=True)
        _ws_clients.discard(websocket)


async def broadcast_stats(data: dict):
    """Push stats update to all connected WebSocket clients."""
    if not _ws_clients:
        return
    message = json.dumps({"type": "stats", "data": data})
    disconnected = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            logger.debug("broadcast_stats: dropping dead client", exc_info=True)
            disconnected.add(ws)
    _ws_clients -= disconnected


async def broadcast_alert(alert_data: dict):
    """Push alert notification to all connected WebSocket clients."""
    if not _ws_clients:
        return
    message = json.dumps(alert_data)
    disconnected = set()
    for ws in _ws_clients:
        try:
            await ws.send_text(message)
        except Exception:
            logger.debug("broadcast_alert: dropping dead client", exc_info=True)
            disconnected.add(ws)
    _ws_clients -= disconnected


# ── Background Tasks ───────────────────────────────────────────────────────────

PRUNE_EVERY_CYCLES = 100  # ~500s at the default 5s stats_interval


async def _stats_loop():
    """Periodically collect, broadcast, store, and evaluate alerts."""
    _cycle = 0
    while True:
        await asyncio.sleep(_config.refresh.stats_interval)
        _cycle += 1
        try:
            system_coll = _collectors.get("system")
            docker_coll = _collectors.get("docker")
            disk_coll = _collectors.get("disk")

            results = await asyncio.gather(
                system_coll.collect() if system_coll else _empty_system(),
                docker_coll.collect_summary() if docker_coll else _empty_docker(),
                disk_coll.collect_summary() if disk_coll else _empty_disk(),
                return_exceptions=True,
            )

            system_data = results[0] if not isinstance(results[0], Exception) else {}
            docker_data = results[1] if not isinstance(results[1], Exception) else {}
            disk_data = results[2] if not isinstance(results[2], Exception) else {}

            combined = {**system_data, **docker_data, **disk_data}

            # Decorate containers with update status from cache (pure dict lookup)
            if _image_update_cache and "containers_list" in combined:
                for ctr in combined["containers_list"]:
                    entry = _image_update_cache.get(ctr["name"])
                    if entry:
                        ctr["update_status"] = entry["status"]

            # Broadcast to WebSocket clients (only when websocket feature enabled)
            if _config.features.websocket:
                await broadcast_stats(combined)

            # Store in history (if enabled)
            if _metric_store:
                await asyncio.to_thread(_metric_store.record, "stats", combined)
                # Sample container states every ~30s (every 6th cycle at 5s interval)
                if docker_coll and _cycle % 6 == 0:
                    try:
                        states = await docker_coll.list_container_states()
                        if states:
                            await asyncio.to_thread(_metric_store.record_container_states, states)
                    except Exception:
                        logger.debug("stats loop: container state sampling failed", exc_info=True)
                # Prune on a fixed cycle cadence, never twice-in-a-row or skipped
                if _cycle % PRUNE_EVERY_CYCLES == 0:
                    await asyncio.to_thread(_metric_store.prune)

            # Evaluate alert thresholds
            if _alert_engine:
                await _alert_engine.evaluate(combined)
        except Exception:
            logger.warning("stats loop iteration failed", exc_info=True)


def _record_latency_batch(results: list[dict]):
    """Sync helper: persist a batch of latency readings in one thread hop and one commit."""
    _metric_store.record_latency_batch([(r["name"], r["latency_ms"]) for r in results])


async def _latency_loop():
    """Periodically measure and store per-peer latency."""
    while True:
        await asyncio.sleep(_config.refresh.fleet_interval)
        try:
            network_coll = _collectors.get("network")
            if network_coll and _metric_store:
                results = await network_coll.measure_latency()
                if results:
                    await asyncio.to_thread(_record_latency_batch, results)
        except Exception:
            logger.warning("latency loop iteration failed", exc_info=True)


async def _image_update_loop(checker):
    """Periodically check running container images against their registries."""
    global _image_update_cache
    # Run initial check immediately on startup
    try:
        _image_update_cache = await checker.check_all()
    except Exception:
        logger.warning("image update check failed", exc_info=True)
    while True:
        await asyncio.sleep(_config.refresh.image_updates_interval)
        try:
            _image_update_cache = await checker.check_all()
        except Exception:
            logger.warning("image update check failed", exc_info=True)


# ── Lifecycle ──────────────────────────────────────────────────────────────────


async def on_startup():
    """Initialize collectors, plugins, storage, alerts, and start background loops."""
    global _plugin_manager, _metric_store, _alert_engine

    if _config.features.demo_mode:
        from buoy.demo import DemoDiskCollector, DemoDockerCollector, DemoSystemCollector

        _collectors["system"] = DemoSystemCollector(_config)
        _collectors["docker"] = DemoDockerCollector(_config)
        _collectors["disk"] = DemoDiskCollector(_config)
    else:
        from buoy.collectors.disk import DiskCollector
        from buoy.collectors.docker import DockerCollector
        from buoy.collectors.network import NetworkCollector
        from buoy.collectors.system import SystemCollector

        _collectors["system"] = SystemCollector(_config)
        _collectors["docker"] = DockerCollector(_config)
        _collectors["disk"] = DiskCollector(_config)
        _collectors["network"] = NetworkCollector(_config)

    # Initialize metric history store (if enabled)
    if _config.features.history:
        from buoy.storage import MetricStore

        _metric_store = MetricStore(_config)
        _metric_store.open()
        logger.info("History storage enabled (SQLite ring buffer)")

    # Initialize alert engine
    from buoy.alerts import AlertEngine

    _alert_engine = AlertEngine(_config, broadcast_fn=broadcast_alert)

    # Start stats collection loop (needed for history persistence, alerts, and websocket broadcast)
    if _config.features.websocket or _config.features.history:
        _background_tasks.append(asyncio.create_task(_stats_loop()))
        logger.info(
            "Stats collection loop enabled (history=%s websocket=%s)",
            _config.features.history,
            _config.features.websocket,
        )

    # Start latency collection loop (only when network collector and history are both present)
    if _collectors.get("network") and _metric_store:
        _background_tasks.append(asyncio.create_task(_latency_loop()))

    # Start image update checker (if enabled)
    if _config.features.image_updates:
        if _config.features.demo_mode:
            from buoy.demo import DemoImageUpdateChecker

            _image_checker = DemoImageUpdateChecker(_config)
        else:
            from buoy.collectors.image_updates import ImageUpdateChecker

            _image_checker = ImageUpdateChecker(_config)
        _background_tasks.append(asyncio.create_task(_image_update_loop(_image_checker)))
        logger.info(
            "Image update checker enabled (interval: %ss)",
            _config.refresh.image_updates_interval,
        )

    # Initialize plugin manager
    from buoy.plugins.loader import PluginManager

    _plugin_manager = PluginManager(_config)
    await _plugin_manager.start()


async def on_shutdown():
    """Cancel background loops, tear down plugins, and close storage."""
    global _plugin_manager, _metric_store, _alert_engine

    for task in _background_tasks:
        task.cancel()
    if _background_tasks:
        await asyncio.gather(*_background_tasks, return_exceptions=True)
    _background_tasks.clear()

    try:
        if _plugin_manager:
            await _plugin_manager.stop()
    finally:
        if _metric_store:
            _metric_store.close()

        _plugin_manager = None
        _metric_store = None
        _alert_engine = None
        _collectors.clear()
        _ws_clients.clear()
        _image_update_cache.clear()


@contextlib.asynccontextmanager
async def lifespan(app: Starlette):
    """Starlette lifespan: run startup, then guarantee shutdown teardown.

    ``on_startup`` runs inside the ``try`` so a partial failure (e.g. the
    metric store opens but a later step raises) still triggers
    ``on_shutdown`` to release whatever was already acquired.
    """
    try:
        await on_startup()
        yield
    finally:
        await on_shutdown()


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


async def _empty_system():
    return {
        "hostname": _config.node.name,
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
    """Serve the dashboard HTML."""
    static_dir = _resolve_static_dir()
    index_path = static_dir / "index.html"
    if not index_path.exists():
        return Response("index.html not found", status_code=500)
    return Response(
        content=index_path.read_text(),
        media_type="text/html",
    )


# ── App Factory ────────────────────────────────────────────────────────────────


# 'unsafe-eval' is required by the plugin custom-JS renderer (new Function(),
# static/js/plugins.js) and 'unsafe-inline' in style-src by the pervasive
# inline style="..." attributes across the dashboard templates. Both are
# tracked for removal under PP-5 (sandboxed plugin renderer), at which point
# this policy should tighten to drop them. fonts.googleapis.com/gstatic.com
# are allowlisted because index.html loads the JetBrains Mono / Outfit
# webfonts from Google Fonts. connect-src includes configured fleet peer
# origins since the fleet grid fetches each peer's /api/stats directly from
# the browser (static/js/fleet.js).
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
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src 'self' https://fonts.gstatic.com; "
        "img-src 'self' data:; "
        f"connect-src {connect_src}; "
        "object-src 'none'; "
        "base-uri 'none'; "
        "frame-ancestors 'none'; "
        "form-action 'self'"
    )


def create_app(config: BuoyConfig) -> Starlette:
    """Create the Starlette application."""
    global _config
    _config = config

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
        Route("/api/container/{name}/restart", api_container_restart, methods=["POST"]),
        WebSocketRoute("/ws", ws_endpoint),
        Mount("/static", StaticFiles(directory=str(static_dir)), name="static"),
    ]

    # /metrics is only registered when the prometheus_exporter plugin is enabled.
    # Inserting before the catch-all static mount keeps route ordering intact.
    if _prometheus_enabled(config):
        routes.insert(-1, Route("/metrics", api_metrics))

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

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            response.headers["Content-Security-Policy"] = csp_policy
            if not request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

    middleware.append(Middleware(SecurityHeadersMiddleware))

    # Rate limiting is always active on protected endpoints (SPEC §7.2),
    # independent of whether auth is enabled.
    from buoy.auth import RateLimitMiddleware

    middleware.append(Middleware(RateLimitMiddleware))

    # Add auth middleware if enabled. Fail fast rather than silently serving
    # a "protected" dashboard that isn't (SEC-1): a misconfigured install
    # must refuse to start, not fall back to pass-through auth.
    if config.auth.enabled:
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

        from buoy.auth import AuthMiddleware

        middleware.append(Middleware(AuthMiddleware, auth_config=config.auth))

    app = Starlette(
        routes=routes,
        middleware=middleware,
        lifespan=lifespan,
    )

    return app


def _factory() -> Starlette:
    """Zero-argument factory for uvicorn reload mode (``python -m buoy --dev``)."""
    import os

    from buoy.config import load_config

    path = os.environ.get("BUOY_CONFIG") or None
    demo = os.environ.get("BUOY_DEMO") == "1"
    return create_app(load_config(path=path, demo=demo))
