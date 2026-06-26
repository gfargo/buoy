"""Buoy server — Starlette application with API routes and WebSocket support."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import TYPE_CHECKING

from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

# ── Globals (set during app creation) ──────────────────────────────────────────

_config: BuoyConfig | None = None
_collectors: dict = {}
_ws_clients: set[WebSocket] = set()
_plugin_manager = None
_metric_store = None
_alert_engine = None


# ── API Handlers ───────────────────────────────────────────────────────────────


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
            },
            "refresh": {
                "stats_interval": _config.refresh.stats_interval,
                "services_interval": _config.refresh.services_interval,
                "fleet_interval": _config.refresh.fleet_interval,
                "plugins_interval": _config.refresh.plugins_interval,
            },
        }
    )


async def api_deploy_info(request: Request) -> JSONResponse:
    """Deployment metadata — version, build time, git SHA."""
    import buoy

    info: dict = {"version": buoy.__version__}

    # Container creation time (image build date)
    try:
        proc = await asyncio.create_subprocess_exec(
            "stat", "-c", "%W", "/proc/1",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=3)
        if stdout and stdout.strip() != b"0":
            import datetime

            boot_ts = int(stdout.strip())
            info["container_started"] = (
                datetime.datetime.fromtimestamp(boot_ts, tz=datetime.UTC).isoformat()
            )
    except Exception:
        pass

    # Git HEAD from host strut repo (optional, best-effort)
    try:
        proc = await asyncio.create_subprocess_exec(
            "nsenter", "-t", "1", "-m", "--",
            "bash", "-c", "cd ~/strut 2>/dev/null && git log -1 --format='%h %s' 2>/dev/null",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.DEVNULL,
        )
        stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=5)
        if stdout and stdout.strip():
            info["git_head"] = stdout.decode().strip()
    except Exception:
        pass

    return JSONResponse(info)


async def api_stats(request: Request) -> JSONResponse:
    """System vitals — CPU, RAM, disk, temp, containers, uptime."""

    system_coll = _collectors.get("system")
    docker_coll = _collectors.get("docker")
    disk_coll = _collectors.get("disk")

    # Gather all stats concurrently
    results = await asyncio.gather(
        system_coll.collect() if system_coll else _empty_system(),
        docker_coll.collect_summary() if docker_coll else _empty_docker(),
        disk_coll.collect_summary() if disk_coll else _empty_disk(),
        return_exceptions=True,
    )

    system_data = results[0] if not isinstance(results[0], Exception) else {}
    docker_data = results[1] if not isinstance(results[1], Exception) else {}
    disk_data = results[2] if not isinstance(results[2], Exception) else {}

    return JSONResponse({**system_data, **docker_data, **disk_data})


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

    is_tailscale = ".ts.net" in request.headers.get("host", "")
    data = await discover_services(_config, is_tailscale)
    return JSONResponse(data)


async def api_fleet(request: Request) -> JSONResponse:
    """Aggregated peer node stats."""

    network_coll = _collectors.get("network")
    if not network_coll:
        return JSONResponse({"peers": []})

    data = await network_coll.collect()
    return JSONResponse(data)


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
    """Restart a Docker container."""
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


async def api_metrics(request: Request) -> Response:
    """Prometheus /metrics endpoint (if prometheus_exporter plugin is enabled)."""
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

    data = _metric_store.query(metric, period_seconds)
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
            disconnected.add(ws)
    _ws_clients -= disconnected


# ── Background Tasks ───────────────────────────────────────────────────────────


async def _stats_loop():
    """Periodically collect, broadcast, store, and evaluate alerts."""
    while True:
        await asyncio.sleep(_config.refresh.stats_interval)
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

            # Broadcast to WebSocket clients
            await broadcast_stats(combined)

            # Store in history (if enabled)
            if _metric_store:
                _metric_store.record("stats", combined)
                # Prune every 100 cycles (~500s at 5s interval)
                if int(asyncio.get_event_loop().time()) % 500 < _config.refresh.stats_interval:
                    _metric_store.prune()

            # Evaluate alert thresholds
            if _alert_engine:
                await _alert_engine.evaluate(combined)
        except Exception:
            pass


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
        print("[buoy] History storage enabled (SQLite ring buffer)")

    # Initialize alert engine
    from buoy.alerts import AlertEngine

    _alert_engine = AlertEngine(_config, broadcast_fn=broadcast_alert)

    # Start WebSocket broadcast loop
    if _config.features.websocket:
        asyncio.create_task(_stats_loop())

    # Initialize plugin manager
    from buoy.plugins.loader import PluginManager

    _plugin_manager = PluginManager(_config)
    await _plugin_manager.start()


# ── Helpers ────────────────────────────────────────────────────────────────────

_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")


def _resolve_static_dir() -> Path:
    """Resolve the static files directory.

    Checks (in order):
    1. /app/static — Docker container (Dockerfile copies static/ here)
    2. Relative to source — local development (src/buoy/../../static)
    """
    docker_path = Path("/app/static")
    if docker_path.exists():
        return docker_path
    # Development: relative to this file (src/buoy/server.py → src/buoy → src → project root)
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


def create_app(config: BuoyConfig) -> Starlette:
    """Create the Starlette application."""
    global _config
    _config = config

    static_dir = _resolve_static_dir()

    routes = [
        Route("/", index),
        Route("/api/health", api_health),
        Route("/api/config", api_config),
        Route("/api/deploy-info", api_deploy_info),
        Route("/api/stats", api_stats),
        Route("/api/stats/detail", api_stats_detail),
        Route("/api/services", api_services),
        Route("/api/fleet", api_fleet),
        Route("/api/plugins", api_plugins),
        Route("/api/plugins/js", api_plugin_js),
        Route("/api/history/{metric}", api_history),
        Route("/api/container/{name}", api_container_detail),
        Route("/api/container/{name}/logs", api_container_logs),
        Route("/api/container/{name}/restart", api_container_restart, methods=["POST"]),
        Route("/metrics", api_metrics),
        WebSocketRoute("/ws", ws_endpoint),
        Mount("/static", StaticFiles(directory=str(static_dir)), name="static"),
    ]

    middleware = [
        Middleware(
            CORSMiddleware,
            allow_origins=["*"],
            allow_methods=["*"],
            allow_headers=["*"],
        ),
    ]

    # Security headers middleware
    from starlette.middleware.base import BaseHTTPMiddleware

    class SecurityHeadersMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            response = await call_next(request)
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["X-Frame-Options"] = "DENY"
            response.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
            if not request.url.path.startswith("/static/"):
                response.headers["Cache-Control"] = "no-cache"
            return response

    middleware.append(Middleware(SecurityHeadersMiddleware))

    # Add auth middleware if enabled
    if config.auth.enabled:
        from buoy.auth import AuthMiddleware

        middleware.append(Middleware(AuthMiddleware, auth_config=config.auth))

    app = Starlette(
        routes=routes,
        middleware=middleware,
        on_startup=[on_startup],
    )

    return app
