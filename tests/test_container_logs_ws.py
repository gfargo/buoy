"""Tests for the live log-streaming WebSocket route (OSS-1551 / buoy#191).

The headline regression this guards against: `BaseHTTPMiddleware`
(`AuthMiddleware`, `RateLimitMiddleware`) only wraps `http` ASGI scopes and
never runs for `websocket` connections, so a naive `/ws/logs/{name}` route
would serve container logs — which may contain secrets (SPEC.md §7.1) —
completely unauthenticated even on an `auth.enabled` install, while the
sibling REST endpoint stays 401'd. The ticket handshake in
`api_container_logs_ticket` / `ws_container_logs` is the fix; the auth
tests below are the ones that must never go green by accident.
"""

from __future__ import annotations

import asyncio

import pytest
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from buoy.auth import _rate_limit
from buoy.config import (
    AuthConfig,
    BuoyConfig,
    FeaturesConfig,
    LogsConfig,
    NetworkConfig,
    NodeConfig,
)
from buoy.server import create_app


class _FakeDockerCollector:
    """Deterministic stand-in for DockerCollector.stream_logs, with cleanup tracking."""

    def __init__(self, lines=None, hang_after=False, raise_after=None):
        self._lines = (
            lines
            if lines is not None
            else [
                {"stream": "stdout", "line": "hello"},
                {"stream": "stdout", "line": "world"},
            ]
        )
        self.hang_after = hang_after
        self.raise_after = raise_after
        self.closed = False
        self.last_tail = None

    async def stream_logs(self, name, tail=100, max_line_bytes=8192):
        self.last_tail = tail
        try:
            for item in self._lines:
                yield item
            if self.raise_after is not None:
                raise self.raise_after
            if self.hang_after:
                await asyncio.sleep(3600)
        finally:
            self.closed = True

    async def get_logs(self, name, tail=30):
        self.last_tail = tail
        return {"container": name, "lines": [i["line"] for i in self._lines]}


def _make_config(auth_enabled=False, base_path="", **logs_overrides):
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig(base_path=base_path)
    config.features = FeaturesConfig(websocket=False, demo_mode=False)
    config.logs = LogsConfig(
        **{"default_tail": 100, "max_tail": 1000, "max_streams": 4, **logs_overrides}
    )
    if auth_enabled:
        config.auth = AuthConfig(enabled=True, type="token", token="s3cret")
    return config


@pytest.fixture(autouse=True)
def isolate_rate_limit():
    _rate_limit.clear()
    yield
    _rate_limit.clear()


def _install_fake_collector(client: TestClient, collector: _FakeDockerCollector) -> None:
    """Swap in a deterministic fake after startup (on_startup would otherwise
    construct a real DockerCollector and overwrite anything set beforehand)."""
    client.app.state.buoy.collectors["docker"] = collector


class TestHappyPath:
    def test_receives_start_log_and_end_frames(self):
        app = create_app(_make_config())
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/ws/logs/grafana?tail=5") as ws:
                start = ws.receive_json()
                assert start == {"type": "log_start", "container": "grafana", "tail": 5}

                log_msg = ws.receive_json()
                assert log_msg["type"] == "log"
                assert log_msg["lines"] == [
                    {"stream": "stdout", "line": "hello"},
                    {"stream": "stdout", "line": "world"},
                ]

                end_msg = ws.receive_json()
                assert end_msg == {"type": "log_end", "reason": "container exited"}

        assert collector.closed is True

    def test_producer_failure_is_reported_distinctly_from_a_clean_exit(self):
        """A broken producer (docker daemon unreachable, binary missing, ...)
        must not look like an ordinary `container exited` end-of-stream —
        the client needs to be able to tell the two apart."""
        app = create_app(_make_config())
        collector = _FakeDockerCollector(
            lines=[{"stream": "stdout", "line": "hello"}],
            raise_after=RuntimeError("docker daemon unreachable"),
        )
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/ws/logs/grafana") as ws:
                ws.receive_json()  # log_start
                ws.receive_json()  # log (hello)
                end_msg = ws.receive_json()

        assert end_msg == {"type": "log_end", "reason": "stream error"}

    def test_uses_configured_default_tail_when_unspecified(self):
        app = create_app(_make_config(default_tail=42))
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/ws/logs/grafana") as ws:
                start = ws.receive_json()
        assert start["tail"] == 42
        assert collector.last_tail == 42

    def test_tail_clamped_to_max_tail(self):
        app = create_app(_make_config(default_tail=100, max_tail=200))
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/ws/logs/grafana?tail=99999") as ws:
                start = ws.receive_json()
        assert start["tail"] == 200

    def test_tail_clamped_to_minimum_one(self):
        app = create_app(_make_config())
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/ws/logs/grafana?tail=-5") as ws:
                start = ws.receive_json()
        assert start["tail"] == 1

    def test_client_disconnect_stops_the_stream_and_cleans_up(self):
        app = create_app(_make_config())
        collector = _FakeDockerCollector(hang_after=True)
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/ws/logs/grafana?tail=5") as ws:
                ws.receive_json()  # log_start
                ws.receive_json()  # log (hello/world)
                # Exiting the `with` block sends a client-initiated close.

        assert collector.closed is True

    def test_works_under_base_path(self):
        app = create_app(_make_config(base_path="/buoy"))
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/buoy/ws/logs/grafana?tail=5") as ws:
                start = ws.receive_json()
        assert start["container"] == "grafana"


class TestRejections:
    def test_invalid_container_name_rejected(self):
        """A name failing _validate_container_name (mirrors
        TestDockerContainerNameValidation in test_collectors.py) — a
        multi-segment path like `../../etc/passwd` wouldn't even match the
        single-segment `{name}` route, so this uses a same-segment value
        that fails the regex instead (leading dash)."""
        app = create_app(_make_config())
        with TestClient(app) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/logs/-starts-with-dash"):
                    pass
        assert exc_info.value.code == 4400

    def test_log_streaming_disabled_rejected(self):
        config = _make_config()
        config.features.log_streaming = False
        app = create_app(config)
        with TestClient(app) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/logs/grafana"):
                    pass
        assert exc_info.value.code == 4403

    def test_docker_unavailable_rejected(self):
        app = create_app(_make_config())
        with TestClient(app) as client:
            client.app.state.buoy.collectors.pop("docker", None)
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/logs/grafana"):
                    pass
        assert exc_info.value.code == 4404

    def test_max_streams_exceeded_rejected(self):
        app = create_app(_make_config(max_streams=1))
        with TestClient(app) as client:
            _install_fake_collector(client, _FakeDockerCollector(hang_after=True))
            client.app.state.buoy.log_stream_count = 1
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/logs/grafana"):
                    pass
        assert exc_info.value.code == 4429


class TestAuthGate:
    """The headline regression tests (see module docstring)."""

    def test_no_ticket_rejected_when_auth_enabled(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/logs/grafana"):
                    pass
        assert exc_info.value.code == 4401

    def test_garbage_ticket_rejected(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect("/ws/logs/grafana?ticket=not-a-real-ticket"):
                    pass
        assert exc_info.value.code == 4401

    def test_valid_ticket_from_http_endpoint_is_accepted(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            _install_fake_collector(client, _FakeDockerCollector())

            ticket_resp = client.get(
                "/api/container/grafana/logs/ticket", headers={"Authorization": "Bearer s3cret"}
            )
            assert ticket_resp.status_code == 200
            ticket = ticket_resp.json()["ticket"]

            with client.websocket_connect(f"/ws/logs/grafana?ticket={ticket}") as ws:
                start = ws.receive_json()
            assert start["container"] == "grafana"

    def test_ticket_request_itself_requires_auth(self):
        """The HTTP ticket endpoint is the only thing standing between an
        auth-enabled install and unauthenticated log access — it must be
        401'd like any other /api/container/ path."""
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/api/container/grafana/logs/ticket")
        assert r.status_code == 401

    def test_ticket_is_single_use(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            ticket = client.get(
                "/api/container/grafana/logs/ticket", headers={"Authorization": "Bearer s3cret"}
            ).json()["ticket"]

            with client.websocket_connect(f"/ws/logs/grafana?ticket={ticket}") as ws:
                ws.receive_json()

            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(f"/ws/logs/grafana?ticket={ticket}"):
                    pass
        assert exc_info.value.code == 4401

    def test_ticket_scoped_to_its_container_name(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            ticket = client.get(
                "/api/container/grafana/logs/ticket", headers={"Authorization": "Bearer s3cret"}
            ).json()["ticket"]

            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(f"/ws/logs/other-container?ticket={ticket}"):
                    pass
        assert exc_info.value.code == 4401

    def test_expired_ticket_rejected(self):
        app = create_app(_make_config(auth_enabled=True))
        with TestClient(app, raise_server_exceptions=False) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            ticket = client.get(
                "/api/container/grafana/logs/ticket", headers={"Authorization": "Bearer s3cret"}
            ).json()["ticket"]

            state = client.app.state.buoy
            name, _expiry = state.log_tickets[ticket]
            state.log_tickets[ticket] = (name, 0.0)  # force expiry (monotonic time)

            with pytest.raises(WebSocketDisconnect) as exc_info:
                with client.websocket_connect(f"/ws/logs/grafana?ticket={ticket}"):
                    pass
        assert exc_info.value.code == 4401

    def test_no_ticket_needed_when_auth_disabled(self):
        app = create_app(_make_config(auth_enabled=False))
        with TestClient(app) as client:
            _install_fake_collector(client, _FakeDockerCollector())
            with client.websocket_connect("/ws/logs/grafana") as ws:
                start = ws.receive_json()
        assert start["container"] == "grafana"

    def test_prefixed_ticket_endpoint_still_requires_auth(self):
        """Mirrors tests/test_base_path.py's TestSecurityRegression: a
        base_path that a proxy has already stripped must not bypass auth."""
        app = create_app(_make_config(auth_enabled=True, base_path="/buoy"))
        with TestClient(app, raise_server_exceptions=False) as client:
            r = client.get("/buoy/api/container/grafana/logs/ticket")
        assert r.status_code == 401


class TestBackpressure:
    """The producer must never block on a slow client — overflow drops the
    oldest lines and surfaces an explicit `log_dropped` count instead."""

    def test_overflowing_the_queue_emits_a_dropped_count(self):
        from buoy.server import _LOG_STREAM_QUEUE_MAX_LINES

        overflow_by = 500
        total_lines = _LOG_STREAM_QUEUE_MAX_LINES + overflow_by
        lines = [{"stream": "stdout", "line": f"line {i}"} for i in range(total_lines)]
        # Isolate queue-overflow drop-oldest from `stream_rate_limit` (a
        # separate pacing mechanism, covered by TestRateLimit below).
        app = create_app(_make_config(stream_rate_limit=0))
        collector = _FakeDockerCollector(lines=lines)

        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            with client.websocket_connect("/ws/logs/grafana") as ws:
                ws.receive_json()  # log_start
                received = []
                dropped_total = 0
                msg = ws.receive_json()
                while msg["type"] != "log_end":
                    if msg["type"] == "log":
                        received.extend(msg["lines"])
                    elif msg["type"] == "log_dropped":
                        dropped_total += msg["count"]
                    msg = ws.receive_json()

        assert dropped_total == overflow_by
        assert len(received) == _LOG_STREAM_QUEUE_MAX_LINES
        # Drop-oldest: the surviving lines are the most recent ones.
        assert received[0]["line"] == f"line {overflow_by}"
        assert received[-1]["line"] == f"line {total_lines - 1}"


class TestRateLimit:
    """`logs.stream_rate_limit` paces delivery instead of dropping — it must
    actually be enforced (nothing prevents the send loop from ignoring it)."""

    def test_paces_delivery_without_dropping_anything(self):
        import time

        rate_limit = 3
        total_lines = 7
        lines = [{"stream": "stdout", "line": f"line {i}"} for i in range(total_lines)]
        app = create_app(_make_config(stream_rate_limit=rate_limit))
        collector = _FakeDockerCollector(lines=lines)

        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            start = time.monotonic()
            with client.websocket_connect("/ws/logs/grafana") as ws:
                ws.receive_json()  # log_start
                received = []
                dropped_total = 0
                msg = ws.receive_json()
                while msg["type"] != "log_end":
                    if msg["type"] == "log":
                        received.extend(msg["lines"])
                    elif msg["type"] == "log_dropped":
                        dropped_total += msg["count"]
                    msg = ws.receive_json()
            elapsed = time.monotonic() - start

        assert dropped_total == 0
        assert [item["line"] for item in received] == [line["line"] for line in lines]
        # 7 lines at 3/sec needs at least two window rollovers — if the
        # limit weren't enforced this would complete almost instantly.
        assert elapsed >= 1.0


class TestRestLogsTailParam:
    """`/api/container/{name}/logs?tail=` must clamp the same way the WS route does."""

    def test_tail_param_is_forwarded_and_clamped(self):
        app = create_app(_make_config(default_tail=100, max_tail=50))
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            r = client.get("/api/container/grafana/logs?tail=999")
        assert r.status_code == 200
        assert collector.last_tail == 50

    def test_missing_tail_param_uses_default(self):
        app = create_app(_make_config(default_tail=77))
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            r = client.get("/api/container/grafana/logs")
        assert r.status_code == 200
        assert collector.last_tail == 77

    def test_invalid_tail_param_falls_back_to_default(self):
        app = create_app(_make_config(default_tail=77))
        collector = _FakeDockerCollector()
        with TestClient(app) as client:
            _install_fake_collector(client, collector)
            r = client.get("/api/container/grafana/logs?tail=not-a-number")
        assert r.status_code == 200
        assert collector.last_tail == 77
