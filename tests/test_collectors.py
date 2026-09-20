"""Tests for Buoy collectors (using mocked data / demo collectors)."""

import asyncio

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig
from buoy.demo import DemoDiskCollector, DemoDockerCollector, DemoSystemCollector


def _make_config(name="test-node"):
    config = BuoyConfig()
    config.node = NodeConfig(name=name)
    config.network = NetworkConfig()
    config.features = FeaturesConfig()
    return config


class TestDemoSystemCollector:
    """Tests for the demo system collector (mock data)."""

    @pytest.mark.asyncio
    async def test_collect_returns_hostname(self):
        config = _make_config("demo-pi")
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert data["hostname"] == "demo-pi"

    @pytest.mark.asyncio
    async def test_collect_has_required_fields(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()

        required = [
            "hostname",
            "cpu",
            "mem_used",
            "mem_total",
            "temp",
            "uptime_h",
            "uptime_m",
            "uptime_s",
        ]
        for field in required:
            assert field in data, f"Missing field: {field}"

    @pytest.mark.asyncio
    async def test_cpu_in_range(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert 0 <= data["cpu"] <= 100

    @pytest.mark.asyncio
    async def test_temp_in_range(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert 0 <= data["temp"] <= 100

    @pytest.mark.asyncio
    async def test_nvme_data_present(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect()
        assert "nvme" in data
        assert data["nvme"]["wear_pct"] >= 0

    @pytest.mark.asyncio
    async def test_collect_detail_structure(self):
        config = _make_config()
        coll = DemoSystemCollector(config)
        data = await coll.collect_detail()
        assert "cpu" in data
        assert "memory" in data
        assert "cores" in data["cpu"]
        assert "top_processes" in data["cpu"]
        assert len(data["cpu"]["top_processes"]) == 5


class TestDemoDockerCollector:
    """Tests for the demo Docker collector."""

    @pytest.mark.asyncio
    async def test_list_containers(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        containers = await coll.list_containers()
        assert len(containers) > 0
        assert "name" in containers[0]

    @pytest.mark.asyncio
    async def test_collect_summary(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.collect_summary()
        assert data["containers"] > 0
        assert len(data["containers_list"]) > 0

        by_name = {c["name"]: c for c in data["containers_list"]}
        # `containers` (the running count) must be strictly less than the
        # length of containers_list, since demo data includes an exited entry.
        assert data["containers"] < len(data["containers_list"])
        assert any(c["health"] == "unhealthy" for c in data["containers_list"])
        assert any(c["state"] != "running" for c in data["containers_list"])
        running = [c for c in data["containers_list"] if c["state"] == "running"]
        assert running, "expected at least one running demo container"
        assert running[0]["cpu_pct"].endswith("%"), (
            "cpu_pct must already include the '%' suffix so the frontend can "
            "render it directly without appending another '%'"
        )
        # Non-running containers report no live cpu/mem.
        non_running = [c for c in data["containers_list"] if c["state"] != "running"]
        assert non_running[0]["cpu_pct"] is None
        assert "status" in by_name["grafana"]

    @pytest.mark.asyncio
    async def test_inspect_container(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.inspect_container("grafana")
        assert data["name"] == "grafana"
        assert data["status"] == "running"
        assert "resources" in data
        # cpu_pct carries its own % suffix (from docker stats / demo); the
        # frontend must NOT append another one — otherwise the UI shows "1.23%%".
        assert data["resources"]["cpu_pct"].endswith("%"), (
            "cpu_pct must already include the '%' suffix so the frontend can "
            "render it directly without appending another '%'"
        )

    @pytest.mark.asyncio
    async def test_is_available_always_true(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        assert await coll.is_available() is True
        assert await coll.is_available(force=True) is True

    @pytest.mark.asyncio
    async def test_get_logs(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.get_logs("grafana")
        assert data["container"] == "grafana"
        assert len(data["lines"]) > 0

    @pytest.mark.asyncio
    async def test_restart_container(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        data = await coll.restart_container("grafana")
        assert data["success"] is True


class TestDemoDiskCollector:
    """Tests for the demo disk collector."""

    @pytest.mark.asyncio
    async def test_collect_summary(self):
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_summary()
        assert "disk_pct" in data
        assert 0 <= data["disk_pct"] <= 100

    @pytest.mark.asyncio
    async def test_collect_detail(self):
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_detail()
        assert "mounts" in data
        assert len(data["mounts"]) > 0
        assert "pct" in data["mounts"][0]


class TestDockerContainerNameValidation:
    """Test container name validation in the real Docker collector."""

    def test_valid_names(self):
        from buoy.collectors.docker import _valid_name

        assert _valid_name("grafana") is True
        assert _valid_name("my-container_1.0") is True
        assert _valid_name("plane-api-1") is True

    def test_invalid_names(self):
        from buoy.collectors.docker import _valid_name

        assert _valid_name("") is False
        assert _valid_name("-starts-with-dash") is False
        assert _valid_name("../../etc/passwd") is False
        assert _valid_name("a" * 200) is False
        assert _valid_name("has spaces") is False
        assert _valid_name("has;semicolon") is False


class TestDockerListContainersCache:
    """Tests for DockerCollector.list_containers() 5s TTL cache (SPEC §8.2)."""

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_uses_cache(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._fetch_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])

        first = await coll.list_containers()
        second = await coll.list_containers()

        assert first == second
        coll._fetch_containers.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._fetch_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])

        await coll.list_containers()
        coll._containers_cache_ts -= 6  # simulate TTL expiry
        await coll.list_containers()

        assert coll._fetch_containers.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_calls_only_fetch_once(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._fetch_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])

        results = await asyncio.gather(
            coll.list_containers(), coll.list_containers(), coll.list_containers()
        )

        assert all(r == results[0] for r in results)
        coll._fetch_containers.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_waiter_finds_cache_set_by_first_without_refetching(self):
        """When two callers both miss the outer (lock-free) cache check and
        race for the lock, the loser's inner double-check inside the lock
        must see the cache the winner just populated and return it directly
        — not fetch a second time."""
        import time as time_module
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._fetch_containers = AsyncMock(
            side_effect=AssertionError("second waiter must not re-fetch")
        )

        await coll._list_lock.acquire()
        task = asyncio.create_task(coll.list_containers())
        await asyncio.sleep(0)  # let the task start and block on the held lock

        coll._containers_cache = [{"name": "already-cached", "host_port": None}]
        coll._containers_cache_ts = time_module.monotonic()
        coll._list_lock.release()

        result = await task

        assert result == [{"name": "already-cached", "host_port": None}]


class TestDockerCollectSummary:
    """Tests for DockerCollector.collect_summary() state/health/cpu/mem merge
    (OSS-1550 / buoy#192)."""

    @staticmethod
    def _run_side_effect(ps_a_output="", stats_output="", ps_code=0, stats_code=0):
        async def _run(*args, **kwargs):
            if args and args[0] == "ps":
                return (ps_code, ps_a_output, "")
            if args and args[0] == "stats":
                return (stats_code, stats_output, "")
            return (1, "", "unexpected command")

        return _run

    @pytest.mark.asyncio
    async def test_merges_state_health_and_stats_by_name(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])
        coll._run = self._run_side_effect(
            ps_a_output=(
                "grafana\trunning\tUp 2 hours (healthy)\nold-job\texited\tExited (0) 3 hours ago"
            ),
            stats_output="grafana\t1.23%\t45MiB / 8GiB\t0.55%",
        )

        data = await coll.collect_summary()
        await coll._stats_task

        # `containers` stays the running count from list_containers(), while
        # containers_list includes the exited one too.
        assert data["containers"] == 1
        assert len(data["containers_list"]) == 2

        by_name = {c["name"]: c for c in data["containers_list"]}
        assert by_name["grafana"]["state"] == "running"
        assert by_name["grafana"]["health"] == "healthy"
        assert by_name["old-job"]["state"] == "exited"
        assert by_name["old-job"]["health"] is None
        assert by_name["old-job"]["cpu_pct"] is None

    @pytest.mark.asyncio
    async def test_health_starting_and_unhealthy_parsed(self):
        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._run = self._run_side_effect(
            ps_a_output=(
                "a\trunning\tUp 1 minute (health: starting)\n"
                "b\trunning\tUp 1 hour (unhealthy)\n"
                "c\trunning\tUp 1 hour"
            )
        )

        states = await coll._fetch_container_states()
        by_name = {s["name"]: s for s in states}
        assert by_name["a"]["health"] == "starting"
        assert by_name["b"]["health"] == "unhealthy"
        assert by_name["c"]["health"] is None

    @pytest.mark.asyncio
    async def test_fetch_container_stats_failure_returns_empty(self):
        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._run = self._run_side_effect(stats_code=1)

        stats = await coll._fetch_container_stats()
        assert stats == {}

    @pytest.mark.asyncio
    async def test_collect_summary_survives_stats_failure(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])
        coll._run = self._run_side_effect(
            ps_a_output="grafana\trunning\tUp 2 hours",
            stats_code=1,
        )

        data = await coll.collect_summary()
        await coll._stats_task

        assert data["containers_list"][0]["cpu_pct"] is None

    @pytest.mark.asyncio
    async def test_cold_cache_returns_without_awaiting_stats(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])
        coll._fetch_container_states = AsyncMock(
            return_value=[{"name": "grafana", "state": "running", "status": "Up", "health": None}]
        )
        fetch_stats = AsyncMock(
            return_value={"grafana": {"cpu_pct": "1%", "mem_usage": "1MiB", "mem_pct": "1%"}}
        )
        coll._fetch_container_stats = fetch_stats

        data = await coll.collect_summary()

        # Cold cache: no cpu/mem yet, and the stats subprocess was only
        # scheduled, not awaited inline.
        assert data["containers_list"][0]["cpu_pct"] is None
        fetch_stats.assert_not_called()
        assert coll._stats_task is not None

    @pytest.mark.asyncio
    async def test_stats_cache_reused_within_ttl_then_refreshes(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])
        coll._fetch_container_states = AsyncMock(
            return_value=[{"name": "grafana", "state": "running", "status": "Up", "health": None}]
        )
        fetch_stats = AsyncMock(
            return_value={"grafana": {"cpu_pct": "1%", "mem_usage": "1MiB", "mem_pct": "1%"}}
        )
        coll._fetch_container_stats = fetch_stats

        await coll.collect_summary()
        await coll._stats_task
        fetch_stats.assert_called_once()

        # Second call within TTL: cache is warm, no second subprocess call.
        data2 = await coll.collect_summary()
        assert data2["containers_list"][0]["cpu_pct"] == "1%"
        fetch_stats.assert_called_once()

        # Simulate TTL expiry.
        coll._stats_cache_ts -= config.refresh.container_stats_interval + 1
        await coll.collect_summary()
        await coll._stats_task
        assert fetch_stats.call_count == 2

    @pytest.mark.asyncio
    async def test_ps_a_failure_falls_back_to_running_set(self):
        """A transient `docker ps -a` failure/timeout must not empty
        `containers_list` while `containers` still reports a count — fall
        back to the already-fetched running set instead."""
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(
            return_value=[
                {"name": "grafana", "host_port": 3000},
                {"name": "redis", "host_port": None},
            ]
        )
        coll._fetch_container_states = AsyncMock(return_value=[])
        coll._fetch_container_stats = AsyncMock(return_value={})

        data = await coll.collect_summary()
        await coll._stats_task

        assert data["containers"] == 2
        assert len(data["containers_list"]) == 2
        names = {c["name"] for c in data["containers_list"]}
        assert names == {"grafana", "redis"}
        assert all(c["state"] == "running" for c in data["containers_list"])

    @pytest.mark.asyncio
    async def test_ps_a_and_ps_both_empty_stays_empty(self):
        """No containers running is not a failure — must not be confused
        with the ps -a fallback path."""
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(return_value=[])
        coll._fetch_container_states = AsyncMock(return_value=[])
        coll._fetch_container_stats = AsyncMock(return_value={})

        data = await coll.collect_summary()
        await coll._stats_task

        assert data["containers"] == 0
        assert data["containers_list"] == []

    @pytest.mark.asyncio
    async def test_aclose_cancels_in_flight_stats_refresh(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])
        coll._fetch_container_states = AsyncMock(
            return_value=[{"name": "grafana", "state": "running", "status": "Up", "health": None}]
        )

        async def _slow_fetch():
            await asyncio.sleep(10)
            return {}

        coll._fetch_container_stats = _slow_fetch

        await coll.collect_summary()
        assert coll._stats_task is not None
        assert not coll._stats_task.done()

        await coll.aclose()

        assert coll._stats_task.cancelled() or coll._stats_task.done()

    @pytest.mark.asyncio
    async def test_aclose_is_a_noop_with_no_task(self):
        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        await coll.aclose()  # must not raise

    @pytest.mark.asyncio
    async def test_container_stats_feature_flag_disabled_skips_stats(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        config.features.container_stats = False
        coll = DockerCollector(config)
        coll.list_containers = AsyncMock(return_value=[{"name": "grafana", "host_port": 3000}])
        coll._fetch_container_states = AsyncMock(
            return_value=[{"name": "grafana", "state": "running", "status": "Up", "health": None}]
        )
        fetch_stats = AsyncMock(return_value={"grafana": {"cpu_pct": "1%"}})
        coll._fetch_container_stats = fetch_stats

        data = await coll.collect_summary()

        fetch_stats.assert_not_called()
        assert coll._stats_task is None
        assert data["containers_list"][0]["cpu_pct"] is None


class TestDockerGetLogs:
    """Tests for DockerCollector.get_logs() stdout/stderr interleaving (BUG-49).

    Concatenating stdout then stderr wholesale destroys chronological
    interleaving, and a chatty stderr can push all of stdout out of the
    requested tail. --timestamps was already being requested but never
    parsed for sorting.
    """

    @staticmethod
    def _ts(second, msg):
        """A --timestamps-style log line; `second` (0-59) controls ordering."""
        return f"2024-01-15T10:20:{second:02d}.000000000Z {msg}"

    @pytest.mark.asyncio
    async def test_interleaves_stdout_and_stderr_chronologically(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        stdout = "\n".join([self._ts(0, "stdout: starting"), self._ts(2, "stdout: ready")])
        stderr = self._ts(1, "stderr: warning")
        coll._run = AsyncMock(return_value=(0, stdout, stderr))

        result = await coll.get_logs("grafana")

        assert result["lines"] == [
            self._ts(0, "stdout: starting"),
            self._ts(1, "stderr: warning"),
            self._ts(2, "stdout: ready"),
        ]

    @pytest.mark.asyncio
    async def test_chatty_stderr_does_not_push_out_older_stdout(self):
        """Regression for the literal bug report: a chatty stderr must not
        crowd stdout entirely out of the tail once properly interleaved."""
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        stdout = self._ts(0, "stdout: the one thing that matters")
        stderr = "\n".join(self._ts(i, f"stderr: noise {i}") for i in range(1, 6))
        coll._run = AsyncMock(return_value=(0, stdout, stderr))

        result = await coll.get_logs("grafana", tail=3)

        # The oldest (stdout) line sorts first and gets trimmed by tail=3,
        # same as it would from any other over-the-limit source — the fix
        # is that it's the *chronologically* oldest line trimmed, not
        # "everything from one stream" trimmed regardless of age.
        assert result["lines"] == [
            self._ts(3, "stderr: noise 3"),
            self._ts(4, "stderr: noise 4"),
            self._ts(5, "stderr: noise 5"),
        ]

    @pytest.mark.asyncio
    async def test_only_stdout(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        stdout = "\n".join([self._ts(0, "line1"), self._ts(1, "line2")])
        coll._run = AsyncMock(return_value=(0, stdout, ""))

        result = await coll.get_logs("grafana")

        assert result["lines"] == [self._ts(0, "line1"), self._ts(1, "line2")]

    @pytest.mark.asyncio
    async def test_no_output_returns_empty_list(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._run = AsyncMock(return_value=(0, "", ""))

        result = await coll.get_logs("grafana")

        assert result == {"container": "grafana", "lines": []}

    @pytest.mark.asyncio
    async def test_invalid_container_name_returns_error(self):
        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)

        result = await coll.get_logs("../../etc/passwd")

        assert result == {"error": "invalid container name"}


class _FakeStream:
    """Minimal stand-in for asyncio.StreamReader — yields queued lines, then hangs."""

    def __init__(self, lines):
        self._lines = list(lines)

    async def readline(self):
        if self._lines:
            return self._lines.pop(0)
        await asyncio.sleep(3600)  # simulate a still-following, quiet container


class TestDockerStreamLogs:
    """Tests for DockerCollector.stream_logs() — the WS-backed `docker logs -f` follow."""

    def _make_proc(self, stdout_lines=(), stderr_lines=()):
        from unittest.mock import AsyncMock, MagicMock

        proc = MagicMock()
        proc.stdout = _FakeStream(stdout_lines)
        proc.stderr = _FakeStream(stderr_lines)
        proc.kill = MagicMock()
        proc.wait = AsyncMock()
        return proc

    @pytest.mark.asyncio
    async def test_invalid_container_name_raises(self):
        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)

        with pytest.raises(ValueError):
            async for _ in coll.stream_logs("../../etc/passwd"):
                pass

    @pytest.mark.asyncio
    async def test_yields_interleaved_stdout_and_stderr(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        proc = self._make_proc(
            stdout_lines=[b"2024-01-01T00:00:00Z out1\n"],
            stderr_lines=[b"2024-01-01T00:00:01Z err1\n"],
        )

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            gen = coll.stream_logs("grafana", tail=10)
            items = []
            async for item in gen:
                items.append(item)
                if len(items) == 2:
                    break
            await gen.aclose()

        assert {"stream": "stdout", "line": "2024-01-01T00:00:00Z out1"} in items
        assert {"stream": "stderr", "line": "2024-01-01T00:00:01Z err1"} in items

    @pytest.mark.asyncio
    async def test_truncates_over_long_lines(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        long_line = ("x" * 50 + "\n").encode()
        proc = self._make_proc(stdout_lines=[long_line])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            gen = coll.stream_logs("grafana", tail=10, max_line_bytes=10)
            item = await gen.__anext__()
            await gen.aclose()

        assert item["line"].endswith("…[truncated]")
        assert len(item["line"]) <= 10 + len("…[truncated]")

    @pytest.mark.asyncio
    async def test_truncates_multibyte_utf8_by_byte_budget_not_char_count(self):
        """`max_line_bytes` is documented (and named) as a byte budget — a
        line of multi-byte UTF-8 chars must be capped by encoded byte length,
        not `len()` of the decoded string, or it silently exceeds the cap."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        # Each "é" is 2 bytes in UTF-8 — 20 of them is 40 bytes but len() == 20.
        long_line = ("é" * 20 + "\n").encode("utf-8")
        proc = self._make_proc(stdout_lines=[long_line])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            gen = coll.stream_logs("grafana", tail=10, max_line_bytes=10)
            item = await gen.__anext__()
            await gen.aclose()

        assert item["line"].endswith("…[truncated]")
        kept = item["line"].removesuffix("…[truncated]")
        assert len(kept.encode("utf-8")) <= 10

    @pytest.mark.asyncio
    async def test_kills_and_reaps_process_on_early_close(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        proc = self._make_proc(stdout_lines=[b"2024-01-01T00:00:00Z hello\n"])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            gen = coll.stream_logs("grafana", tail=10)
            await gen.__anext__()
            await gen.aclose()

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()

    @pytest.mark.asyncio
    async def test_kills_process_on_task_cancellation(self):
        """Regression: cancellation from the WS handler side (not an explicit
        aclose()) must still reach the kill/reap `finally` block."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        proc = self._make_proc(stdout_lines=[b"2024-01-01T00:00:00Z hello\n"])

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            gen = coll.stream_logs("grafana", tail=10)
            await gen.__anext__()

            task = asyncio.ensure_future(gen.__anext__())
            await asyncio.sleep(0)  # let the task start awaiting the next item
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task

        proc.kill.assert_called_once()
        proc.wait.assert_awaited()


class TestDockerRun:
    """Tests for DockerCollector._run()'s own exception-handling branches."""

    @pytest.mark.asyncio
    async def test_timeout_error_returns_failure_tuple(self):
        from unittest.mock import patch

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        with patch("asyncio.create_subprocess_exec", side_effect=TimeoutError):
            result = await coll._run("info")

        assert result == (1, "", "timeout")

    @pytest.mark.asyncio
    async def test_generic_exception_returns_failure_tuple_with_message(self):
        from unittest.mock import patch

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        with patch("asyncio.create_subprocess_exec", side_effect=RuntimeError("boom")):
            code, stdout, stderr = await coll._run("info")

        assert code == 1
        assert stdout == ""
        assert stderr == "boom"

    @pytest.mark.asyncio
    async def test_docker_binary_missing_returns_docker_not_found(self):
        from unittest.mock import patch

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError("no docker")):
            result = await coll._run("info")

        assert result == (1, "", "docker not found")


class TestDockerCollectSummary:
    @pytest.mark.asyncio
    async def test_collect_summary_reports_count_and_names(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll.list_containers = AsyncMock(
            return_value=[
                {"name": "grafana", "host_port": 3000, "service": "grafana"},
                {"name": "redis", "host_port": None, "service": "redis"},
            ]
        )

        summary = await coll.collect_summary()

        assert summary == {
            "containers": 2,
            "containers_list": [{"name": "grafana"}, {"name": "redis"}],
        }


class TestDockerIsAvailable:
    """is_available() had no direct tests at all — only exercised
    indirectly (and inconsistently) via other tests that stub it out."""

    @pytest.mark.asyncio
    async def test_available_when_run_succeeds(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "abc123", ""))

        assert await coll.is_available() is True
        coll._run.assert_awaited_once_with("info", "--format", "{{.ID}}", timeout=5)

    @pytest.mark.asyncio
    async def test_unavailable_when_run_fails(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(1, "", "docker not found"))

        assert await coll.is_available() is False

    @pytest.mark.asyncio
    async def test_result_is_cached_after_first_call(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "abc123", ""))

        first = await coll.is_available()
        second = await coll.is_available()

        assert first is True
        assert second is True
        coll._run.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_force_resets_the_cache_and_reprobes(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(side_effect=[(1, "", "docker not found"), (0, "abc123", "")])

        first = await coll.is_available()
        second = await coll.is_available(force=True)

        assert first is False
        assert second is True
        assert coll._run.await_count == 2


class TestDockerFetchContainers:
    """_fetch_containers() was only ever exercised through a stub — the
    real tab-splitting/parsing implementation had no coverage."""

    @pytest.mark.asyncio
    async def test_parses_name_ports_and_service_columns(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "grafana\t0.0.0.0:3000->3000/tcp\tgrafana\n", ""))

        containers = await coll._fetch_containers()

        assert containers == [{"name": "grafana", "host_port": 3000, "service": "grafana"}]

    @pytest.mark.asyncio
    async def test_missing_ports_and_service_columns_default_to_empty(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "grafana\n", ""))

        containers = await coll._fetch_containers()

        assert containers == [{"name": "grafana", "host_port": None, "service": ""}]

    @pytest.mark.asyncio
    async def test_blank_lines_are_skipped(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "grafana\t\t\n\nredis\t\t\n", ""))

        containers = await coll._fetch_containers()

        assert [c["name"] for c in containers] == ["grafana", "redis"]

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_empty_list(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(1, "", "docker daemon not running"))

        assert await coll._fetch_containers() == []

    @pytest.mark.asyncio
    async def test_empty_stdout_returns_empty_list(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "", ""))

        assert await coll._fetch_containers() == []


class TestDockerParseFirstPort:
    """_parse_first_port() had no direct tests — only reachable (never
    exercised) through the never-called-for-real _fetch_containers()."""

    @pytest.mark.parametrize(
        "ports_str, expected",
        [
            ("0.0.0.0:8080->80/tcp", 8080),
            (":::3000->3000/tcp", 3000),
            ("8080->80/tcp", 8080),  # no host IP/colon prefix on the left side
            ("80/tcp", None),  # no "->" at all
            ("0.0.0.0:notaport->80/tcp", None),  # non-numeric, exhausted
            ("", None),
        ],
    )
    def test_parse_first_port_table(self, ports_str, expected):
        from buoy.collectors.docker import DockerCollector

        assert DockerCollector._parse_first_port(ports_str) == expected

    def test_mapping_without_arrow_is_skipped_for_a_later_one(self):
        from buoy.collectors.docker import DockerCollector

        result = DockerCollector._parse_first_port("80/tcp, 0.0.0.0:8080->80/tcp")
        assert result == 8080

    def test_non_numeric_port_falls_through_to_next_mapping(self):
        from buoy.collectors.docker import DockerCollector

        result = DockerCollector._parse_first_port("0.0.0.0:abc->80/tcp, 0.0.0.0:9090->90/tcp")
        assert result == 9090


class TestDockerInspect:
    """inspect_container() had no coverage at all beyond the demo collector."""

    BASE_INSPECT_JSON = (
        '{"status":"running","started":"2024-01-01T00:00:00Z",'
        '"image":"grafana/grafana:10","restart_count":0,"pid":1234,'
        '"image_created":"2023-12-01T00:00:00Z"}'
    )

    @pytest.mark.asyncio
    async def test_invalid_name_returns_error_without_running_docker(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock()

        result = await coll.inspect_container("../etc/passwd")

        assert result == {"error": "invalid container name"}
        coll._run.assert_not_called()

    @pytest.mark.asyncio
    async def test_inspect_failure_returns_stderr(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(1, "", "No such container: grafana"))

        result = await coll.inspect_container("grafana")

        assert result == {"error": "No such container: grafana"}

    @pytest.mark.asyncio
    async def test_inspect_failure_without_stderr_uses_default_message(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(1, "", ""))

        result = await coll.inspect_container("grafana")

        assert result == {"error": "container not found"}

    @pytest.mark.asyncio
    async def test_unparseable_inspect_output_returns_error(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "not json", ""))

        result = await coll.inspect_container("grafana")

        assert result == {"error": "failed to parse inspect output"}

    @pytest.mark.asyncio
    async def test_stats_merged_under_resources_and_ports_appended(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        stats_json = (
            '{"cpu_pct":"1.23%","mem_usage":"100MiB / 2GiB","mem_pct":"5.0%",'
            '"net_io":"1kB / 2kB","block_io":"0B / 0B"}'
        )
        coll._run = AsyncMock(
            side_effect=[
                (0, self.BASE_INSPECT_JSON, ""),
                (0, stats_json, ""),
                (0, "0.0.0.0:3000->3000/tcp", ""),
            ]
        )

        result = await coll.inspect_container("grafana")

        assert result["name"] == "grafana"
        assert result["resources"]["cpu_pct"] == "1.23%"
        assert result["ports"] == "0.0.0.0:3000->3000/tcp"

    @pytest.mark.asyncio
    async def test_bad_stats_json_is_ignored_not_fatal(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(
            side_effect=[
                (0, self.BASE_INSPECT_JSON, ""),
                (0, "not json", ""),
                (0, "", ""),
            ]
        )

        result = await coll.inspect_container("grafana")

        assert "resources" not in result
        assert result["name"] == "grafana"

    @pytest.mark.asyncio
    async def test_ports_empty_string_when_port_lookup_fails(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(
            side_effect=[
                (0, self.BASE_INSPECT_JSON, ""),
                (1, "", "stats unavailable"),
                (1, "", "no such container"),
            ]
        )

        result = await coll.inspect_container("grafana")

        assert result["ports"] == ""
        assert "resources" not in result


class TestDockerRestart:
    """restart_container() had no coverage at all beyond the demo collector."""

    @pytest.mark.asyncio
    async def test_invalid_name_returns_error_without_running_docker(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock()

        result = await coll.restart_container("bad;name")

        assert result == {"success": False, "error": "invalid container name"}
        coll._run.assert_not_called()

    @pytest.mark.asyncio
    async def test_success(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(0, "", ""))

        result = await coll.restart_container("grafana")

        assert result == {"success": True, "container": "grafana"}
        coll._run.assert_awaited_once_with("restart", "grafana", timeout=30)

    @pytest.mark.asyncio
    async def test_failure_returns_stderr(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(1, "", "no such container"))

        result = await coll.restart_container("grafana")

        assert result == {"success": False, "error": "no such container"}

    @pytest.mark.asyncio
    async def test_failure_without_stderr_uses_default_message(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        coll = DockerCollector(_make_config())
        coll._run = AsyncMock(return_value=(1, "", ""))

        result = await coll.restart_container("grafana")

        assert result == {"success": False, "error": "restart failed"}


class TestDiskCollectorLocalMounts:
    """Tests for the real DiskCollector's nsenter-less /proc/mounts fallback."""

    def test_filters_virtual_fs_and_dedupes_bind_mounts(self, tmp_path):
        """Virtual filesystems are skipped, and bind mounts of a real mount
        point (e.g. Docker's per-container /etc/hosts) collapse to the
        shortest (real) path on that device instead of listing both."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        bind_dir = real_dir / "bind"
        bind_dir.mkdir()

        proc_mounts = (
            "overlay / overlay rw 0 0\n"
            "tmpfs /dev tmpfs rw 0 0\n"
            f"/dev/sda1 {real_dir} ext4 rw 0 0\n"
            f"/dev/sda1 {bind_dir} ext4 rw 0 0\n"
        )

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert len(mounts) == 1
        assert mounts[0]["mount"] == str(real_dir)

    @pytest.mark.parametrize(
        "device",
        ["nas.local:/export", "//nas.local/share"],
        ids=["nfs", "cifs"],
    )
    def test_keeps_network_filesystem_mounts(self, tmp_path, device):
        """NFS/CIFS device fields (e.g. "host:/export", "//host/share")
        don't start with "/", but they're real mounts and must not be
        dropped from the reported list."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        net_dir = tmp_path / "net"
        net_dir.mkdir()

        proc_mounts = f"{device} {net_dir} nfs4 rw 0 0\n"

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert [m["mount"] for m in mounts] == [str(net_dir)]
        assert mounts[0]["fs"] == device

    def test_falls_back_to_root_when_nothing_real_found(self):
        """If every /proc/mounts line is virtual (e.g. an overlay-rooted
        container with no other real mount), fall back to root usage."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        proc_mounts = "overlay / overlay rw 0 0\ntmpfs /dev tmpfs rw 0 0\n"

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert len(mounts) == 1
        assert mounts[0]["mount"] == "/"

    def test_skips_mount_point_that_is_not_a_directory(self, tmp_path):
        """A /proc/mounts entry pointing at a path that doesn't exist (or
        isn't a directory) must be skipped rather than raising from a later
        os.stat()/disk_usage() call."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        missing_dir = tmp_path / "does-not-exist"
        good_dir = tmp_path / "good"
        good_dir.mkdir()

        proc_mounts = f"/dev/sdz1 {missing_dir} ext4 rw 0 0\n/dev/sda1 {good_dir} ext4 rw 0 0\n"

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert [m["mount"] for m in mounts] == [str(good_dir)]

    def test_skips_lines_with_fewer_than_three_fields(self, tmp_path):
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        real_dir = tmp_path / "real"
        real_dir.mkdir()
        proc_mounts = f"short line\n/dev/sda1 {real_dir} ext4 rw 0 0\n"

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=proc_mounts)):
            mounts = coll._local_mounts()

        assert [m["mount"] for m in mounts] == [str(real_dir)]

    def test_skips_mount_point_that_raises_oserror_on_disk_usage(self, tmp_path):
        """A stale/broken mount point (e.g. a dead NFS server) can raise
        OSError from shutil.disk_usage() — must be skipped, not raised, so
        one bad mount doesn't take down the whole mount list."""
        import shutil
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        broken_dir = tmp_path / "broken"
        broken_dir.mkdir()
        good_dir = tmp_path / "good"
        good_dir.mkdir()

        proc_mounts = f"/dev/sdz1 {broken_dir} ext4 rw 0 0\n/dev/sda1 {good_dir} ext4 rw 0 0\n"

        real_disk_usage = shutil.disk_usage

        def fake_disk_usage(path):
            if str(path) == str(broken_dir):
                raise OSError("stale mount")
            return real_disk_usage(path)

        coll = DiskCollector(_make_config())
        with (
            patch("builtins.open", mock_open(read_data=proc_mounts)),
            patch("shutil.disk_usage", side_effect=fake_disk_usage),
        ):
            mounts = coll._local_mounts()

        assert [m["mount"] for m in mounts] == [str(good_dir)]

    def test_skips_mount_with_zero_total_size(self, tmp_path):
        import shutil
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        zero_dir = tmp_path / "zero"
        zero_dir.mkdir()
        good_dir = tmp_path / "good"
        good_dir.mkdir()

        proc_mounts = f"/dev/zero1 {zero_dir} ext4 rw 0 0\n/dev/sda1 {good_dir} ext4 rw 0 0\n"

        class _ZeroUsage:
            total = used = free = 0

        real_disk_usage = shutil.disk_usage

        def fake_disk_usage(path):
            if str(path) == str(zero_dir):
                return _ZeroUsage()
            return real_disk_usage(path)

        coll = DiskCollector(_make_config())
        with (
            patch("builtins.open", mock_open(read_data=proc_mounts)),
            patch("shutil.disk_usage", side_effect=fake_disk_usage),
        ):
            mounts = coll._local_mounts()

        assert [m["mount"] for m in mounts] == [str(good_dir)]

    def test_proc_mounts_unreadable_falls_back_to_root_only(self):
        """/proc/mounts itself being unreadable (e.g. permission denied) is
        a different path than "every mount was virtual" — both end up at
        _root_only_mount(), but only this one exercises the outer except."""
        from unittest.mock import patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        with patch("builtins.open", side_effect=OSError("permission denied")):
            mounts = coll._local_mounts()

        assert len(mounts) == 1
        assert mounts[0]["mount"] == "/"


class TestDiskCollectorRootOnlyMountFallback:
    def test_returns_empty_list_when_disk_usage_raises(self):
        from unittest.mock import patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        with patch("shutil.disk_usage", side_effect=OSError("no such path")):
            mounts = coll._root_only_mount()

        assert mounts == []


class TestDiskCollectorNsenterMounts:
    """_nsenter_mounts() was only ever exercised indirectly through a stub
    (e.g. in TestDiskCollectorRootPercentConsistency) — the real df -h
    parsing implementation had no coverage."""

    DF_HEADER = "Filesystem      Size  Used Avail Use% Mounted on\n"

    @pytest.mark.asyncio
    async def test_parses_df_output(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        df_output = self.DF_HEADER + "/dev/sda1        50G   30G   20G  60% /\n"
        proc = MagicMock()
        proc.returncode = 0

        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
            patch(
                "buoy.collectors.disk.communicate",
                new=AsyncMock(return_value=(df_output.encode(), b"")),
            ),
        ):
            mounts = await coll._nsenter_mounts()

        assert mounts == [
            {
                "fs": "/dev/sda1",
                "size": "50G",
                "used": "30G",
                "avail": "20G",
                "pct": 60,
                "mount": "/",
            }
        ]

    @pytest.mark.asyncio
    async def test_non_integer_use_percent_defaults_to_zero(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        df_output = self.DF_HEADER + "tmpfs            50G   30G   20G    -  /weird\n"
        proc = MagicMock()
        proc.returncode = 0

        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
            patch(
                "buoy.collectors.disk.communicate",
                new=AsyncMock(return_value=(df_output.encode(), b"")),
            ),
        ):
            mounts = await coll._nsenter_mounts()

        assert mounts[0]["pct"] == 0

    @pytest.mark.asyncio
    async def test_nonzero_returncode_returns_empty_list(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        proc = MagicMock()
        proc.returncode = 1

        with (
            patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)),
            patch(
                "buoy.collectors.disk.communicate",
                new=AsyncMock(return_value=(b"", b"nsenter: failed")),
            ),
        ):
            mounts = await coll._nsenter_mounts()

        assert mounts == []

    @pytest.mark.asyncio
    async def test_missing_nsenter_binary_returns_empty_list(self):
        from unittest.mock import patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        with patch(
            "asyncio.create_subprocess_exec", side_effect=FileNotFoundError("nsenter not found")
        ):
            mounts = await coll._nsenter_mounts()

        assert mounts == []


class TestDiskCollectorRootPercentConsistency:
    """Tests for _root_disk_percent() agreeing with _all_mounts() (BUG-25).

    The headline gauge (collect_summary()'s disk_pct) used to read this
    process's own container rootfs via shutil.disk_usage("/"), while the
    detail panel's mount table (collect_detail()'s mounts) read the host's
    view via nsenter. The two could legitimately disagree.
    """

    @pytest.mark.asyncio
    async def test_root_percent_matches_all_mounts_root_entry(self):
        """The gauge must read its value from the same mount list the
        detail panel uses, not take an independent (and possibly
        container-scoped) measurement."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        host_mounts = [
            {
                "fs": "/dev/sda1",
                "size": "500G",
                "used": "450G",
                "avail": "50G",
                "pct": 90,
                "mount": "/",
            },
            {
                "fs": "/dev/sdb1",
                "size": "4T",
                "used": "1T",
                "avail": "3T",
                "pct": 25,
                "mount": "/mnt/storage",
            },
        ]

        with patch.object(coll, "_all_mounts", new=AsyncMock(return_value=host_mounts)):
            pct = await coll._root_disk_percent()

        # The host's root usage (90%), not whatever this test process's own
        # shutil.disk_usage("/") happens to report.
        assert pct == 90

    @pytest.mark.asyncio
    async def test_root_percent_falls_back_when_no_root_mount_present(self):
        """If the resolved mount list has no "/" entry at all (unexpected,
        but possible), fall back to this process's own root usage rather
        than crashing or silently reporting 0."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        mounts_without_root = [
            {
                "fs": "/dev/sdb1",
                "size": "4T",
                "used": "1T",
                "avail": "3T",
                "pct": 25,
                "mount": "/data",
            }
        ]

        with patch.object(coll, "_all_mounts", new=AsyncMock(return_value=mounts_without_root)):
            pct = await coll._root_disk_percent()

        assert 0 <= pct <= 100

    @pytest.mark.asyncio
    async def test_collect_summary_and_collect_detail_agree(self):
        """End-to-end: collect_summary()'s disk_pct and collect_detail()'s
        "/" mount entry must come from the same underlying data."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        host_mounts = [
            {
                "fs": "/dev/sda1",
                "size": "500G",
                "used": "450G",
                "avail": "50G",
                "pct": 90,
                "mount": "/",
            }
        ]

        with (
            patch.object(coll, "_nsenter_mounts", new=AsyncMock(return_value=host_mounts)),
            patch.object(coll, "_nvme_smart", new=AsyncMock(return_value=None)),
            patch.object(
                coll, "_disk_io", new=AsyncMock(return_value={"read_gb": 0, "write_gb": 0})
            ),
        ):
            summary = await coll.collect_summary()
            detail = await coll.collect_detail()

        root_entry = next(m for m in detail["mounts"] if m["mount"] == "/")
        assert summary["disk_pct"] == root_entry["pct"] == 90


class TestDiskCollectorRootPercentExceptions:
    @pytest.mark.asyncio
    async def test_returns_zero_when_all_mounts_raises(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        with patch.object(coll, "_all_mounts", new=AsyncMock(side_effect=RuntimeError("boom"))):
            pct = await coll._root_disk_percent()

        assert pct == 0


class TestDiskCollectorMountsCache:
    """Tests for _all_mounts()' 5s TTL cache (mirrors DockerCollector's
    list_containers cache — nsenter+df is a real subprocess spawn, and
    _all_mounts() is now on the hot path via _root_disk_percent())."""

    @pytest.mark.asyncio
    async def test_second_call_within_ttl_uses_cache(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        coll._nsenter_mounts = AsyncMock(return_value=[{"mount": "/", "pct": 42}])

        first = await coll._all_mounts()
        second = await coll._all_mounts()

        assert first == second
        coll._nsenter_mounts.assert_called_once()

    @pytest.mark.asyncio
    async def test_cache_expires_after_ttl(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        coll._nsenter_mounts = AsyncMock(return_value=[{"mount": "/", "pct": 42}])

        await coll._all_mounts()
        coll._mounts_cache_ts -= 6  # simulate TTL expiry (_MOUNTS_CACHE_TTL = 5.0)
        await coll._all_mounts()

        assert coll._nsenter_mounts.call_count == 2

    @pytest.mark.asyncio
    async def test_concurrent_calls_only_fetch_once(self):
        from unittest.mock import AsyncMock

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        coll._nsenter_mounts = AsyncMock(return_value=[{"mount": "/", "pct": 42}])

        results = await asyncio.gather(coll._all_mounts(), coll._all_mounts(), coll._all_mounts())

        assert all(r == results[0] for r in results)
        coll._nsenter_mounts.assert_called_once()

    @pytest.mark.asyncio
    async def test_second_waiter_finds_cache_set_by_first_without_refetching(self):
        """When two callers both miss the outer (lock-free) cache check and
        race for the lock, the loser's inner double-check inside the lock
        must see the cache the winner just populated and return it directly
        — not fetch a second time."""
        import time as time_module
        from unittest.mock import AsyncMock

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        coll._nsenter_mounts = AsyncMock(
            side_effect=AssertionError("second waiter must not re-fetch")
        )

        await coll._mounts_lock.acquire()
        task = asyncio.create_task(coll._all_mounts())
        await asyncio.sleep(0)  # let the task start and block on the held lock

        coll._mounts_cache = [{"mount": "/", "pct": 11}]
        coll._mounts_cache_ts = time_module.monotonic()
        coll._mounts_lock.release()

        result = await task

        assert result == [{"mount": "/", "pct": 11}]


class TestDiskCollectorNvme:
    """Tests for real DiskCollector NVMe SMART path."""

    @pytest.mark.asyncio
    async def test_nvme_smart_returns_none_when_unavailable(self):
        """_nvme_smart returns None gracefully when nsenter and smartctl are absent."""
        from unittest.mock import patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        # Both nsenter and direct smartctl calls raise FileNotFoundError
        with patch(
            "asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("smartctl not found"),
        ):
            result = await coll._nvme_smart()

        assert result is None

    @pytest.mark.asyncio
    async def test_nvme_smart_probes_discovered_device_not_hardcoded_path(self):
        """BUG-27: the discovered NVMe device is used, not a hardcoded
        /dev/nvme0n1 guess — fixes hosts where the drive is numbered
        differently (e.g. nvme1n1) or otherwise isn't named nvme0n1."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        smartctl_output = (
            "Temperature:                        41 Celsius\n"
            "Percentage Used:                    3%\n"
            "Power On Hours:                     1200\n"
            "Data Units Read:                    1,000 [512 MB]\n"
            "Data Units Written:                 500 [256 MB]\n"
        )

        with (
            patch(
                "buoy.collectors.disk.scan_nvme_devices",
                new=AsyncMock(return_value=["/dev/nvme1"]),
            ),
            patch(
                "buoy.collectors.disk.run_smartctl", new=AsyncMock(return_value=smartctl_output)
            ) as mock_run,
        ):
            result = await coll._nvme_smart()

        mock_run.assert_awaited_once_with("-a", "/dev/nvme1")
        assert result["temp"] == 41
        assert result["wear_pct"] == 3
        assert result["power_hours"] == 1200

    @pytest.mark.asyncio
    async def test_nvme_smart_falls_back_to_default_path_when_scan_finds_nothing(self):
        """If smartctl --scan finds no NVMe device at all, fall back to the
        historical /dev/nvme0n1 guess rather than giving up entirely."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        with (
            patch("buoy.collectors.disk.scan_nvme_devices", new=AsyncMock(return_value=[])),
            patch(
                "buoy.collectors.disk.run_smartctl", new=AsyncMock(return_value=None)
            ) as mock_run,
        ):
            result = await coll._nvme_smart()

        mock_run.assert_awaited_once_with("-a", "/dev/nvme0n1")
        assert result is None

    @pytest.mark.asyncio
    async def test_demo_disk_nvme_in_summary(self):
        """DemoDiskCollector always returns nvme data in collect_summary."""
        config = _make_config()
        coll = DemoDiskCollector(config)
        data = await coll.collect_summary()
        assert "nvme" in data
        nvme = data["nvme"]
        assert "temp" in nvme
        assert "wear_pct" in nvme
        assert "power_hours" in nvme
        assert "read" in nvme
        assert "written" in nvme

    @pytest.mark.asyncio
    async def test_nvme_smart_returns_none_for_device_not_found_banner(self):
        """smartctl still writes its version/copyright banner to stdout even
        when it fails to open the device — e.g. probing /dev/nvme0n1 on a
        host with no NVMe drive at all (reproduced on a Docker Desktop VM).
        `output` is non-empty but contains none of the expected SMART
        fields, so _nvme_smart() must return None instead of a misleading
        all-zero dict that collect_summary() would then surface as a fake
        "0% worn, 0°C" NVMe panel."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        device_not_found_banner = (
            "smartctl 7.3 2022-02-28 r5338 [x86_64-linux-6.1.0] (local build)\n"
            "Copyright (C) 2002-22, Bruce Allen, Christian Franke, www.smartmontools.org\n\n"
            "Smartctl open device: /dev/nvme0n1 failed: No such device\n"
        )

        with (
            patch(
                "buoy.collectors.disk.scan_nvme_devices",
                new=AsyncMock(return_value=["/dev/nvme0n1"]),
            ),
            patch(
                "buoy.collectors.disk.run_smartctl",
                new=AsyncMock(return_value=device_not_found_banner),
            ),
        ):
            result = await coll._nvme_smart()

        assert result is None

    @pytest.mark.asyncio
    async def test_nvme_smart_omitted_from_summary_when_device_not_found(self):
        """End-to-end: collect_summary() must not include an "nvme" key at
        all when smartctl only produced a banner, not real SMART data."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        device_not_found_banner = (
            "smartctl 7.3 2022-02-28 r5338\n"
            "Smartctl open device: /dev/nvme0n1 failed: No such device\n"
        )

        with (
            patch.object(coll, "_root_disk_percent", new=AsyncMock(return_value=42)),
            patch("buoy.collectors.disk.scan_nvme_devices", new=AsyncMock(return_value=[])),
            patch(
                "buoy.collectors.disk.run_smartctl",
                new=AsyncMock(return_value=device_not_found_banner),
            ),
        ):
            summary = await coll.collect_summary()

        assert "nvme" not in summary

    @pytest.mark.asyncio
    async def test_collect_summary_includes_nvme_when_smart_data_parses(self):
        """The mirror case of test_nvme_smart_omitted_from_summary_when_device_not_found:
        when smartctl returns real SMART fields, collect_summary() must
        surface them under an "nvme" key."""
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.disk import DiskCollector

        config = _make_config()
        coll = DiskCollector(config)

        smartctl_output = (
            "Temperature:                        41 Celsius\n"
            "Percentage Used:                    3%\n"
            "Power On Hours:                     1200\n"
            "Data Units Read:                    1,000 [512 MB]\n"
            "Data Units Written:                 500 [256 MB]\n"
        )

        with (
            patch.object(coll, "_root_disk_percent", new=AsyncMock(return_value=42)),
            patch("buoy.collectors.disk.scan_nvme_devices", new=AsyncMock(return_value=[])),
            patch(
                "buoy.collectors.disk.run_smartctl",
                new=AsyncMock(return_value=smartctl_output),
            ),
        ):
            summary = await coll.collect_summary()

        assert summary["nvme"]["temp"] == 41
        assert summary["nvme"]["wear_pct"] == 3
        assert summary["nvme"]["power_hours"] == 1200
        assert summary["nvme"]["read"] == "512 MB"
        assert summary["nvme"]["written"] == "256 MB"


class TestDiskCollectorIo:
    """Tests for the real DiskCollector's /proc/diskstats-based I/O totals.

    Regression coverage for BUG-26: _disk_io() used to match only an exact
    device-name allowlist ("nvme0n1", "sda", "mmcblk0"), so a VM
    (vda/xvda), a second SATA disk (sdb), or an additional NVMe drive
    reported 0 GB read/write regardless of actual activity.
    """

    ONE_GB_SECTORS = 2097152  # 1 GiB / 512-byte sectors

    @staticmethod
    def _diskstat_line(major, minor, name, read_sectors, write_sectors):
        return f"{major} {minor} {name} 100 0 {read_sectors} 100 200 0 {write_sectors} 200 0 0 0\n"

    async def _run(self, lines: list[str]) -> dict:
        from unittest.mock import mock_open, patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        with patch("builtins.open", mock_open(read_data="".join(lines))):
            return await coll._disk_io()

    @pytest.mark.asyncio
    async def test_vm_virtio_disk_is_recognized(self):
        """A VM's virtio disk (vda) used to report 0 GB — it's not in the
        old exact-match allowlist at all."""
        lines = [self._diskstat_line(252, 0, "vda", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS)]
        result = await self._run(lines)
        assert result == {"read_gb": 1.0, "write_gb": 1.0}

    @pytest.mark.asyncio
    async def test_xen_virtio_disk_is_recognized(self):
        lines = [self._diskstat_line(202, 0, "xvda", self.ONE_GB_SECTORS, 0)]
        result = await self._run(lines)
        assert result == {"read_gb": 1.0, "write_gb": 0.0}

    @pytest.mark.asyncio
    async def test_second_sata_disk_is_summed_with_first(self):
        """A second real disk (sdb) used to be invisible — only the exact
        name "sda" matched. Both disks' I/O must be summed, and their
        partitions excluded (already counted via the whole device)."""
        lines = [
            self._diskstat_line(8, 0, "sda", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS // 2),
            self._diskstat_line(8, 1, "sda1", 1_000_000, 500_000),  # partition, excluded
            self._diskstat_line(8, 16, "sdb", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS // 2),
            self._diskstat_line(8, 17, "sdb1", 1_000_000, 500_000),  # partition, excluded
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 2.0, "write_gb": 1.0}

    @pytest.mark.asyncio
    async def test_additional_nvme_drive_is_summed(self):
        """A second NVMe drive (nvme1n1) used to be invisible — only
        "nvme0n1" matched exactly."""
        lines = [
            self._diskstat_line(259, 0, "nvme0n1", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS),
            self._diskstat_line(259, 1, "nvme0n1p1", 500_000, 500_000),  # partition, excluded
            self._diskstat_line(259, 2, "nvme1n1", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS),
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 2.0, "write_gb": 2.0}

    @pytest.mark.asyncio
    async def test_mmcblk_partition_excluded_from_whole_device(self):
        lines = [
            self._diskstat_line(179, 0, "mmcblk0", self.ONE_GB_SECTORS, self.ONE_GB_SECTORS),
            self._diskstat_line(179, 1, "mmcblk0p1", 500_000, 500_000),  # partition, excluded
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 1.0, "write_gb": 1.0}

    @pytest.mark.asyncio
    async def test_virtual_devices_excluded(self):
        """loop/dm/md devices are excluded — their I/O is already accounted
        for on the physical device(s) underneath, so counting them too
        would double-count."""
        lines = [
            self._diskstat_line(7, 0, "loop0", 1_000_000, 0),
            self._diskstat_line(253, 0, "dm-0", 1_000_000, 1_000_000),
            self._diskstat_line(9, 0, "md0", 1_000_000, 1_000_000),
        ]
        result = await self._run(lines)
        assert result == {"read_gb": 0, "write_gb": 0}

    @pytest.mark.asyncio
    async def test_returns_zeros_when_diskstats_unreadable(self):
        from unittest.mock import patch

        from buoy.collectors.disk import DiskCollector

        coll = DiskCollector(_make_config())
        with patch("builtins.open", side_effect=OSError("no such file")):
            result = await coll._disk_io()

        assert result == {"read_gb": 0, "write_gb": 0}


class TestDiskCollectorSmartHelpers:
    """_find_line() and _extract_bracket()'s no-match branches — only their
    happy paths were exercised indirectly through _nvme_smart()."""

    def test_find_line_returns_none_when_prefix_absent(self):
        from buoy.collectors.disk import DiskCollector

        assert DiskCollector._find_line("no matching content here\n", "Data Units Read:") is None

    def test_extract_bracket_returns_unknown_when_no_brackets(self):
        from buoy.collectors.disk import DiskCollector

        assert DiskCollector._extract_bracket("Data Units Read: 123") == "unknown"


class TestNetworkLatency:
    """Tests for NetworkCollector tailscale ping and HTTP fallback."""

    def _make_net_config(self, peers=None):
        from buoy.config import PeerConfig

        config = _make_config("compass")
        if peers:
            config.network.peers = [PeerConfig(name=n, url=u) for n, u in peers]
        return config

    def _mock_proc(self, returncode, stdout):
        from unittest.mock import AsyncMock, MagicMock

        proc = MagicMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(stdout, b""))
        return proc

    @pytest.mark.asyncio
    async def test_tailscale_ping_parses_latency(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config()
        coll = NetworkCollector(config)
        proc = self._mock_proc(0, b"pong from compass (100.64.67.98) via DERP(nyc) in 2.1ms\n")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await coll._tailscale_ping("compass")

        assert result == 2.1

    @pytest.mark.asyncio
    async def test_tailscale_ping_returns_none_on_failure(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config()
        coll = NetworkCollector(config)
        proc = self._mock_proc(1, b"")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)):
            result = await coll._tailscale_ping("compass")

        assert result is None

    @pytest.mark.asyncio
    async def test_measure_latency_falls_back_to_http(self):
        from unittest.mock import AsyncMock, MagicMock, patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "http://harbor.local")])
        coll = NetworkCollector(config)

        mock_response = MagicMock()
        mock_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)

        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client):
                results = await coll.measure_latency()

        assert len(results) == 1
        assert results[0]["online"] is True
        assert results[0]["latency_ms"] >= 0

    @pytest.mark.asyncio
    async def test_measure_latency_self_node(self):
        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("compass", "http://compass.local")])
        coll = NetworkCollector(config)
        results = await coll.measure_latency()

        assert results == [{"name": "compass", "latency_ms": 0, "online": True}]

    # --- verify_ssl propagation tests ---

    def _make_mock_client(self, status_code=200):
        from unittest.mock import AsyncMock, MagicMock

        mock_response = MagicMock()
        mock_response.status_code = status_code
        mock_response.json = MagicMock(return_value={})

        mock_client = AsyncMock()
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=None)
        mock_client.get = AsyncMock(return_value=mock_response)
        return mock_client

    @pytest.mark.asyncio
    async def test_collect_uses_verify_true_by_default(self):
        """collect() passes verify=True to AsyncClient when network.verify_ssl defaults."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        # Default: network.verify_ssl=True, peer.verify_ssl=None
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await coll.collect()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is True

    @pytest.mark.asyncio
    async def test_collect_uses_network_verify_false(self):
        """collect() passes verify=False when network.verify_ssl=False."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        config.network.verify_ssl = False
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await coll.collect()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False

    @pytest.mark.asyncio
    async def test_collect_per_peer_override_wins(self):
        """Per-peer verify_ssl=False wins over network.verify_ssl=True."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector
        from buoy.config import PeerConfig

        config = self._make_net_config()
        config.network.verify_ssl = True
        config.network.peers = [
            PeerConfig(name="harbor", url="https://harbor.local", verify_ssl=False)
        ]
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
            await coll.collect()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False

    @pytest.mark.asyncio
    async def test_measure_latency_http_fallback_verify_true(self):
        """HTTP fallback in measure_latency() uses verify=True by default."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
                await coll.measure_latency()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is True

    @pytest.mark.asyncio
    async def test_measure_latency_http_fallback_verify_false(self):
        """HTTP fallback in measure_latency() uses verify=False when network.verify_ssl=False."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        config = self._make_net_config([("harbor", "https://harbor.local")])
        config.network.verify_ssl = False
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
                await coll.measure_latency()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False

    @pytest.mark.asyncio
    async def test_measure_latency_runs_peers_concurrently(self):
        """BUG-34 regression: measuring N peers must take ~one peer's latency,
        not N times that — each peer used to be awaited serially inside a
        plain `for` loop, so slow/offline peers could make the whole poll
        overrun its own refresh interval."""
        import time
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.network import NetworkCollector

        per_peer_delay = 0.2
        num_peers = 4
        config = self._make_net_config(
            [(f"peer{i}", f"http://peer{i}.local") for i in range(num_peers)]
        )
        coll = NetworkCollector(config)

        async def slow_ping(_peer_name):
            await asyncio.sleep(per_peer_delay)
            return 1.0

        with patch.object(coll, "_tailscale_ping", new=AsyncMock(side_effect=slow_ping)):
            start = time.monotonic()
            results = await coll.measure_latency()
            elapsed = time.monotonic() - start

        assert len(results) == num_peers
        # Serial execution would take num_peers * per_peer_delay (~0.8s); a
        # generous ceiling well under that catches a regression to the old
        # sequential loop without being flaky under CI scheduling jitter.
        assert elapsed < per_peer_delay * (num_peers / 2)

    @pytest.mark.asyncio
    async def test_measure_latency_per_peer_override_wins(self):
        """Per-peer verify_ssl=False wins in measure_latency() HTTP fallback."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector
        from buoy.config import PeerConfig

        config = self._make_net_config()
        config.network.verify_ssl = True
        config.network.peers = [
            PeerConfig(name="harbor", url="https://harbor.local", verify_ssl=False)
        ]
        coll = NetworkCollector(config)

        mock_client = self._make_mock_client()
        with patch("asyncio.create_subprocess_exec", side_effect=FileNotFoundError):
            with patch("httpx.AsyncClient", return_value=mock_client) as mock_cls:
                await coll.measure_latency()

        mock_cls.assert_called_once()
        assert mock_cls.call_args.kwargs["verify"] is False


class TestDockerListContainerStates:
    """Tests for DockerCollector.list_container_states()."""

    def _make_proc(self, returncode, stdout):
        from unittest.mock import AsyncMock, MagicMock

        proc = MagicMock()
        proc.returncode = returncode
        proc.communicate = AsyncMock(return_value=(stdout.encode(), b""))
        return proc

    @pytest.mark.asyncio
    async def test_parses_running_and_stopped_containers(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)

        ps_proc = self._make_proc(0, "abc123\ndef456\n")
        inspect_lines = (
            '{"name":"/grafana","status":"running","restart_count":0}\n'
            '{"name":"/redis","status":"exited","restart_count":3}\n'
        )
        inspect_proc = self._make_proc(0, inspect_lines)

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ps_proc if call_count == 1 else inspect_proc

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)):
            result = await coll.list_container_states()

        assert len(result) == 2
        names = {r["name"] for r in result}
        assert names == {"grafana", "redis"}

        grafana = next(r for r in result if r["name"] == "grafana")
        assert grafana["status"] == "running"
        assert grafana["restart_count"] == 0

        redis = next(r for r in result if r["name"] == "redis")
        assert redis["status"] == "exited"
        assert redis["restart_count"] == 3

    @pytest.mark.asyncio
    async def test_strips_leading_slash_from_name(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)

        ps_proc = self._make_proc(0, "abc123\n")
        inspect_proc = self._make_proc(
            0, '{"name":"/my-container","status":"running","restart_count":0}\n'
        )

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ps_proc if call_count == 1 else inspect_proc

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)):
            result = await coll.list_container_states()

        assert result[0]["name"] == "my-container"

    @pytest.mark.asyncio
    async def test_returns_empty_when_no_containers(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        ps_proc = self._make_proc(0, "")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=ps_proc)):
            result = await coll.list_container_states()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_ps_failure(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        ps_proc = self._make_proc(1, "")

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=ps_proc)):
            result = await coll.list_container_states()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_when_ids_are_blank_after_filtering(self):
        """Defensive branch: if `_run`'s stdout were ever non-empty but
        every line blank once split (no real IDs), must not proceed to a
        batch inspect call with an empty id list. Stubs `_run` directly
        since `_run` itself always strips its stdout, which would otherwise
        make this state unreachable through the real subprocess path."""
        from unittest.mock import AsyncMock

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        coll._run = AsyncMock(return_value=(0, "\n\n", ""))

        result = await coll.list_container_states()

        assert result == []

    @pytest.mark.asyncio
    async def test_returns_empty_on_batch_inspect_failure(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        ps_proc = self._make_proc(0, "abc123\n")
        inspect_proc = self._make_proc(1, "")

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ps_proc if call_count == 1 else inspect_proc

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)):
            result = await coll.list_container_states()

        assert result == []

    @pytest.mark.asyncio
    async def test_blank_and_malformed_inspect_lines_are_skipped(self):
        from unittest.mock import AsyncMock, patch

        from buoy.collectors.docker import DockerCollector

        config = _make_config()
        coll = DockerCollector(config)
        ps_proc = self._make_proc(0, "abc123\ndef456\n")
        # A blank line in the *middle* of the output (not at either end,
        # which `_run`'s own stdout.strip() would otherwise trim away).
        inspect_lines = (
            'not valid json\n\n{"name":"/grafana","status":"running","restart_count":0}\n'
        )
        inspect_proc = self._make_proc(0, inspect_lines)

        call_count = 0

        async def fake_exec(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            return ps_proc if call_count == 1 else inspect_proc

        with patch("asyncio.create_subprocess_exec", new=AsyncMock(side_effect=fake_exec)):
            result = await coll.list_container_states()

        assert len(result) == 1
        assert result[0]["name"] == "grafana"


class TestDemoDockerListContainerStates:
    """Tests for DemoDockerCollector.list_container_states()."""

    @pytest.mark.asyncio
    async def test_returns_states_for_all_demo_containers(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        states = await coll.list_container_states()
        assert len(states) > 0
        for s in states:
            assert "name" in s
            assert "status" in s
            assert "restart_count" in s
            assert isinstance(s["restart_count"], int)

    @pytest.mark.asyncio
    async def test_all_demo_containers_are_running(self):
        config = _make_config()
        coll = DemoDockerCollector(config)
        states = await coll.list_container_states()
        for s in states:
            assert s["status"] == "running"


class TestSystemCollectorMemory:
    """Tests for the real SystemCollector's /proc/meminfo-based memory reads.

    Regression coverage for BUG-29 (used = MemTotal - MemFree - Buffers -
    Cached overstated usage vs free/htop, since it ignores SReclaimable and
    Shmem) and BUG-30 (memory detail's top_processes was hardcoded empty).
    """

    MEMINFO = (
        "MemTotal:       16384000 kB\n"
        "MemFree:         1024000 kB\n"
        "MemAvailable:    9000000 kB\n"
        "Buffers:          500000 kB\n"
        "Cached:          6000000 kB\n"
        "SwapTotal:       2048000 kB\n"
        "SwapFree:        1500000 kB\n"
    )

    def test_used_matches_total_minus_available(self):
        """used should track MemAvailable, not the free/buffers/cached formula
        that ignores reclaimable slab and shared memory."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=self.MEMINFO)):
            used_gb, total_gb = coll._read_memory()

        # 16384000 - 9000000 = 7384000 kB ≈ 7.0 GB, not the ~8.9 GB the old
        # (MemFree+Buffers+Cached) formula would have produced.
        assert used_gb == pytest.approx(7.0, abs=0.01)
        assert total_gb == pytest.approx(15.6, abs=0.01)

    def test_detail_used_mb_matches_total_minus_available(self):
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=self.MEMINFO)):
            detail = coll._read_memory_detail()

        assert detail["used_mb"] == detail["total_mb"] - detail["available_mb"]

    def test_falls_back_to_memfree_when_memavailable_missing(self):
        """Very old kernels don't expose MemAvailable at all."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        meminfo = "MemTotal:       16384000 kB\nMemFree:         1024000 kB\n"
        coll = SystemCollector(_make_config())
        with patch("builtins.open", mock_open(read_data=meminfo)):
            used_gb, total_gb = coll._read_memory()

        assert used_gb == pytest.approx(14.6, abs=0.01)
        assert total_gb == pytest.approx(15.6, abs=0.01)

    @pytest.mark.asyncio
    async def test_collect_detail_populates_top_processes(self):
        """collect_detail() must wire memory.top_processes from
        _top_processes_by("mem") instead of leaving it hardcoded empty."""
        from unittest.mock import AsyncMock, mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        coll._is_linux = True  # exercise the real (non-fallback) path regardless of test host OS
        fake_top = [{"pid": 1, "cpu": 0.1, "mem": 42.0, "cmd": "buoy"}]
        top_processes_mock = AsyncMock(return_value=fake_top)

        with (
            patch("builtins.open", mock_open(read_data=self.MEMINFO)),
            patch.object(coll, "_top_processes_by", new=top_processes_mock),
            patch.object(coll, "_read_cpu_detail", new=AsyncMock(return_value={})),
        ):
            detail = await coll.collect_detail()

        assert detail["memory"]["top_processes"] == fake_top
        top_processes_mock.assert_awaited_once_with("mem")


class TestSystemCollectorCpu:
    """Tests for the real SystemCollector's /proc/stat-based CPU sampling.

    Regression coverage for BUG-31: _read_cpu() used to block for a fixed
    100ms on every call (two samples taken 100ms apart back-to-back) instead
    of keeping the previous sample on the instance and computing the delta
    across calls.
    """

    @staticmethod
    def _stat_line(user, nice, system, idle, iowait=0, irq=0, softirq=0, steal=0):
        return f"cpu  {user} {nice} {system} {idle} {iowait} {irq} {softirq} {steal} 0 0\n"

    @pytest.mark.asyncio
    async def test_first_call_returns_zero_with_no_prior_sample(self):
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        stat = self._stat_line(100, 0, 50, 800)
        with patch("builtins.open", mock_open(read_data=stat)):
            result = await coll._read_cpu()

        assert result == 0

    @pytest.mark.asyncio
    async def test_second_call_computes_delta_against_previous_sample(self):
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        first = self._stat_line(100, 0, 50, 800)
        second = self._stat_line(150, 0, 70, 850)

        with patch("builtins.open", mock_open(read_data=first)):
            await coll._read_cpu()
        with patch("builtins.open", mock_open(read_data=second)):
            result = await coll._read_cpu()

        # idle_delta = 850-800 = 50, total_delta = 1070-950 = 120
        # 100 * (1 - 50/120) = 58.33 -> int() truncates to 58
        assert result == 58

    @pytest.mark.asyncio
    async def test_does_not_sleep(self):
        """The old implementation awaited asyncio.sleep(0.1) on every call;
        the fix removes that fixed blocking window entirely."""
        from unittest.mock import AsyncMock, mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        stat = self._stat_line(100, 0, 50, 800)
        sleep_mock = AsyncMock()
        with (
            patch("builtins.open", mock_open(read_data=stat)),
            patch("asyncio.sleep", sleep_mock),
        ):
            await coll._read_cpu()

        sleep_mock.assert_not_called()

    @pytest.mark.asyncio
    async def test_zero_total_delta_returns_zero(self):
        """Two identical samples (e.g. calls close enough together that the
        jiffy counters haven't moved) must not raise ZeroDivisionError."""
        from unittest.mock import mock_open, patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        stat = self._stat_line(100, 0, 50, 800)

        with patch("builtins.open", mock_open(read_data=stat)):
            await coll._read_cpu()
            result = await coll._read_cpu()

        assert result == 0


class TestSystemCollectorTemperature:
    """Tests for the real SystemCollector's CPU temperature detection (BUG-28).

    thermal_zone0 is the CPU on a Raspberry Pi but is frequently `acpitz`,
    a wifi radio, or a battery sensor on x86 — _read_temperature() used to
    always read zone0 by a fixed index regardless of what it actually was,
    reporting the wrong sensor (or 0°C, indistinguishable from a real cold
    reading) on non-Pi hardware.
    """

    @staticmethod
    def _patch_sysfs(hwmon: dict | None = None, thermal: dict | None = None):
        """Fake /sys/class/hwmon and /sys/class/thermal trees.

        hwmon: {"hwmon0": {"name": "coretemp", "temp1_input": "45000"}, ...}
        thermal: {"thermal_zone0": {"type": "cpu-thermal", "temp": "42000"}, ...}
        """
        import io
        from unittest.mock import patch

        hwmon = hwmon or {}
        thermal = thermal or {}

        files: dict[str, str] = {}
        for entry, attrs in hwmon.items():
            for fname, content in attrs.items():
                files[f"/sys/class/hwmon/{entry}/{fname}"] = content
        for entry, attrs in thermal.items():
            for fname, content in attrs.items():
                files[f"/sys/class/thermal/{entry}/{fname}"] = content

        def fake_listdir(path):
            if path == "/sys/class/hwmon":
                return sorted(hwmon.keys())
            if path == "/sys/class/thermal":
                return sorted(thermal.keys())
            raise FileNotFoundError(path)

        def fake_open(path, *args, **kwargs):
            if path in files:
                return io.StringIO(files[path])
            raise FileNotFoundError(path)

        return (
            patch("os.listdir", side_effect=fake_listdir),
            patch("builtins.open", side_effect=fake_open),
        )

    def test_prefers_coretemp_hwmon_over_thermal_zone(self):
        """A coretemp hwmon driver wins even if thermal_zone0 exists with an
        unrelated type — hwmon is checked before any thermal zone scan."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            hwmon={"hwmon0": {"name": "coretemp", "temp1_input": "45000"}},
            thermal={"thermal_zone0": {"type": "iwlwifi_1", "temp": "30000"}},
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 45

    def test_falls_back_to_cpu_thermal_zone_type_when_no_hwmon(self):
        """Pi-style: no relevant hwmon driver, but a zone typed cpu-thermal
        exists — found by type, regardless of its index."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={
                "thermal_zone0": {"type": "iwlwifi_1", "temp": "30000"},
                "thermal_zone1": {"type": "cpu-thermal", "temp": "52000"},
            },
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 52

    def test_falls_back_to_acpitz_as_last_resort(self):
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={"thermal_zone0": {"type": "acpitz", "temp": "48000"}},
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 48

    def test_prefers_specific_zone_type_over_acpitz(self):
        """acpitz is a best-effort last resort — a more specific CPU zone
        type wins if both are present on the same host."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={
                "thermal_zone0": {"type": "acpitz", "temp": "40000"},
                "thermal_zone1": {"type": "x86_pkg_temp", "temp": "55000"},
            },
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp == 55

    def test_returns_none_when_no_matching_sensor_anywhere(self):
        """A wifi/battery-only thermal zone with no matching hwmon must not
        be silently reported as 0°C — that's indistinguishable from a real
        (very cold) reading."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        p_listdir, p_open = self._patch_sysfs(
            thermal={"thermal_zone0": {"type": "iwlwifi_1", "temp": "30000"}},
        )
        with p_listdir, p_open:
            temp = coll._read_temperature()

        assert temp is None

    def test_returns_none_when_hwmon_path_does_not_exist(self):
        """A restrictive container without /sys/class/hwmon mounted must not
        raise — falls through to the thermal zone scan."""
        from unittest.mock import patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with patch("os.listdir", side_effect=FileNotFoundError):
            temp = coll._read_hwmon_cpu_temp()

        assert temp is None


class TestSystemCollectorFallback:
    """Tests for the non-Linux fallback stats (BUG-33).

    _fallback_stats() used to report cpu/mem/temp as 0 on macOS/Windows —
    indistinguishable from "everything is idle and cold" — instead of
    surfacing that these /proc- and /sys-based metrics simply aren't
    available on this platform.
    """

    def test_fallback_stats_reports_metrics_as_unavailable_not_zero(self):
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        stats = coll._fallback_stats()

        assert stats["cpu"] is None
        assert stats["mem_used"] is None
        assert stats["mem_total"] is None
        assert stats["temp"] is None
        # Identity fields are still populated normally.
        assert stats["hostname"] == "test-node"
        assert "model" in stats

    @pytest.mark.asyncio
    async def test_collect_uses_fallback_on_non_linux(self):
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        coll._is_linux = False

        data = await coll.collect()

        assert data["cpu"] is None
        assert data["mem_used"] is None
        assert data["mem_total"] is None
        assert data["temp"] is None


class TestSystemCollectorCgroupCpuQuota:
    """Tests for cgroup-aware core count (BUG-32).

    os.cpu_count() reflects the host's total core count and ignores any
    cgroup CPU quota (e.g. Docker's --cpus=N) — a container with a 2-CPU
    quota on a 16-core host reported "16 cores", which also skews
    detail.js's `load_1 > cores` overload warning threshold.
    """

    @staticmethod
    def _patch_files(files: dict[str, str]):
        """files: path -> content. Any path not in the dict raises
        FileNotFoundError, matching a real missing-file/no-cgroup host."""
        import io
        from unittest.mock import patch

        def fake_open(path, *args, **kwargs):
            if path in files:
                return io.StringIO(files[path])
            raise FileNotFoundError(path)

        return patch("builtins.open", side_effect=fake_open)

    def test_v2_whole_number_quota(self):
        """cgroup v2: --cpus=2 on a 100ms period -> 2 cores exactly."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with self._patch_files({"/sys/fs/cgroup/cpu.max": "200000 100000\n"}):
            cores = coll._cgroup_cpu_quota_cores()

        assert cores == 2

    def test_v2_fractional_quota(self):
        """cgroup v2: --cpus=1.5 must not round away the fraction."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with self._patch_files({"/sys/fs/cgroup/cpu.max": "150000 100000\n"}):
            cores = coll._cgroup_cpu_quota_cores()

        assert cores == 1.5

    def test_v2_unlimited_returns_none(self):
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with self._patch_files({"/sys/fs/cgroup/cpu.max": "max 100000\n"}):
            cores = coll._cgroup_cpu_quota_cores()

        assert cores is None

    def test_v1_fallback_when_v2_absent(self):
        """cgroup v1 hosts don't have cpu.max at all — fall back to the
        separate cfs_quota_us/cfs_period_us files."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with self._patch_files(
            {
                "/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "300000\n",
                "/sys/fs/cgroup/cpu/cpu.cfs_period_us": "100000\n",
            }
        ):
            cores = coll._cgroup_cpu_quota_cores()

        assert cores == 3

    def test_v1_unlimited_quota_returns_none(self):
        """cgroup v1 represents "no limit" as -1, not a missing file."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with self._patch_files({"/sys/fs/cgroup/cpu/cpu.cfs_quota_us": "-1\n"}):
            cores = coll._cgroup_cpu_quota_cores()

        assert cores is None

    def test_no_cgroup_files_returns_none(self):
        """Non-Linux or a host without cgroups mounted — must not raise."""
        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with self._patch_files({}):
            cores = coll._cgroup_cpu_quota_cores()

        assert cores is None

    def test_effective_cores_uses_quota_when_set(self):
        from unittest.mock import patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with (
            self._patch_files({"/sys/fs/cgroup/cpu.max": "200000 100000\n"}),
            patch("os.cpu_count", return_value=16),
        ):
            cores = coll._effective_cpu_cores()

        assert cores == 2

    def test_effective_cores_falls_back_to_host_when_no_quota(self):
        from unittest.mock import patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with self._patch_files({}), patch("os.cpu_count", return_value=8):
            cores = coll._effective_cpu_cores()

        assert cores == 8

    def test_effective_cores_clamps_quota_to_host_count(self):
        """A quota that (implausibly) exceeds the host's real core count
        must not be reported as-is — cap at what's actually there."""
        from unittest.mock import patch

        from buoy.collectors.system import SystemCollector

        coll = SystemCollector(_make_config())
        with (
            self._patch_files({"/sys/fs/cgroup/cpu.max": "800000 100000\n"}),  # quota=8
            patch("os.cpu_count", return_value=2),
        ):
            cores = coll._effective_cpu_cores()

        assert cores == 2


_NET_DEV_HEADER = (
    "Inter-|   Receive                                                |  Transmit\n"
    " face |bytes    packets errs drop fifo frame compressed multicast|"
    "bytes    packets errs drop fifo colls carrier compressed\n"
)


def _net_dev_line(name, rx_bytes, tx_bytes, rx_errs=0, rx_drop=0, tx_errs=0, tx_drop=0):
    fields = [rx_bytes, 0, rx_errs, rx_drop, 0, 0, 0, 0, tx_bytes, 0, tx_errs, tx_drop, 0, 0, 0, 0]
    return f"{name}: " + " ".join(str(f) for f in fields) + "\n"


def _net_dev_text(entries):
    """entries: list of (name, rx_bytes, tx_bytes, rx_errs, rx_drop, tx_errs, tx_drop)."""
    return _NET_DEV_HEADER + "".join(_net_dev_line(*e) for e in entries)


_ROUTE_HEADER = "Iface\tDestination\tGateway\tFlags\tRefCnt\tUse\tMetric\tMask\tMTU\tWindow\tIRTT\n"


def _route_line(iface, dest="00000000", metric=0):
    return f"{iface}\t{dest}\t0102A8C0\t0003\t0\t0\t{metric}\t00000000\t0\t0\t0\n"


def _route_text(entries):
    """entries: list of (iface, dest, metric)."""
    return _ROUTE_HEADER + "".join(_route_line(*e) for e in entries)


class TestNetworkThroughputParsing:
    """Pure-parsing tests for NetworkCollector's /proc/net/dev + /proc/net/route helpers."""

    def test_header_lines_ignored(self):
        from buoy.collectors.network import NetworkCollector

        text = _net_dev_text([("eth0", 100, 200)])
        result = NetworkCollector._parse_net_dev(text)

        assert set(result) == {"eth0"}

    def test_requires_sixteen_fields(self):
        from buoy.collectors.network import NetworkCollector

        text = _NET_DEV_HEADER + "eth0: 100 200 300\n"
        result = NetworkCollector._parse_net_dev(text)

        assert result == {}

    def test_colon_flush_against_name_is_handled(self):
        from buoy.collectors.network import NetworkCollector

        text = _net_dev_text([("wlan0", 111, 222)])
        result = NetworkCollector._parse_net_dev(text)

        assert "wlan0" in result

    def test_extracts_rx_tx_bytes_errors_drops(self):
        from buoy.collectors.network import NetworkCollector

        text = _net_dev_text([("eth0", 500000, 300000, 1, 2, 3, 4)])
        result = NetworkCollector._parse_net_dev(text)

        assert result["eth0"] == {
            "rx_bytes": 500000,
            "rx_errors": 1,
            "rx_dropped": 2,
            "tx_bytes": 300000,
            "tx_errors": 3,
            "tx_dropped": 4,
        }

    def test_alias_style_names_parsed(self):
        from buoy.collectors.network import NetworkCollector

        text = _net_dev_text([("eth0:1", 10, 20)])
        result = NetworkCollector._parse_net_dev(text)

        # partition on first ':' only — an alias suffix after a second colon
        # ends up folded into the field list and fails the 16-field check,
        # so it's simply skipped rather than mis-parsed.
        assert "eth0" not in result

    def test_default_route_picks_lowest_metric(self):
        from buoy.collectors.network import NetworkCollector

        text = _route_text([("eth1", "00000000", 600), ("eth0", "00000000", 100)])
        assert NetworkCollector._parse_default_route(text) == "eth0"

    def test_default_route_ignores_non_default_destinations(self):
        from buoy.collectors.network import NetworkCollector

        text = _route_text([("eth0", "000011AC", 0)])
        assert NetworkCollector._parse_default_route(text) is None

    def test_default_route_empty_text_returns_none(self):
        from buoy.collectors.network import NetworkCollector

        assert NetworkCollector._parse_default_route("") is None


class TestNetworkThroughputFiltering:
    """Tests for NetworkCollector._included (interface allow/skip filtering)."""

    @staticmethod
    def _coll(interfaces=None):
        from buoy.collectors.network import NetworkCollector

        config = _make_config()
        config.network.interfaces = interfaces or []
        return NetworkCollector(config)

    @pytest.mark.parametrize("name", ["lo", "veth1a2b", "docker0", "br-abc123", "virbr0"])
    def test_default_skips_virtual_interfaces(self, name):
        coll = self._coll()
        assert coll._included(name) is False

    @pytest.mark.parametrize("name", ["eth0", "enp3s0", "tailscale0", "wg0"])
    def test_default_keeps_real_interfaces(self, name):
        coll = self._coll()
        assert coll._included(name) is True

    def test_allowlist_includes_normally_skipped_interface(self):
        coll = self._coll(interfaces=["br-abc123"])
        assert coll._included("br-abc123") is True

    def test_allowlist_excludes_normally_kept_interface_when_omitted(self):
        coll = self._coll(interfaces=["eth0"])
        assert coll._included("wg0") is False


class TestNetworkThroughputRates:
    """Tests for NetworkCollector._compute_rates (pure rate math)."""

    def test_no_prior_sample_yields_zero(self):
        from buoy.collectors.network import NetworkCollector

        cur = {"eth0": {"rx_bytes": 1000, "tx_bytes": 500}}
        rates = NetworkCollector._compute_rates({}, cur, elapsed=5.0)

        assert rates == {"eth0": {"rx_bytes_per_sec": 0.0, "tx_bytes_per_sec": 0.0}}

    def test_computes_delta_over_elapsed(self):
        from buoy.collectors.network import NetworkCollector

        prev = {"eth0": {"rx_bytes": 1000, "tx_bytes": 500}}
        cur = {"eth0": {"rx_bytes": 6000, "tx_bytes": 1500}}
        rates = NetworkCollector._compute_rates(prev, cur, elapsed=5.0)

        assert rates == {"eth0": {"rx_bytes_per_sec": 1000.0, "tx_bytes_per_sec": 200.0}}

    def test_counter_regression_clamps_to_zero(self):
        from buoy.collectors.network import NetworkCollector

        prev = {"eth0": {"rx_bytes": 6000, "tx_bytes": 1500}}
        cur = {"eth0": {"rx_bytes": 100, "tx_bytes": 50}}  # counter reset/wrapped
        rates = NetworkCollector._compute_rates(prev, cur, elapsed=5.0)

        assert rates == {"eth0": {"rx_bytes_per_sec": 0.0, "tx_bytes_per_sec": 0.0}}

    def test_non_positive_elapsed_yields_zero(self):
        from buoy.collectors.network import NetworkCollector

        prev = {"eth0": {"rx_bytes": 1000, "tx_bytes": 500}}
        cur = {"eth0": {"rx_bytes": 6000, "tx_bytes": 1500}}
        rates = NetworkCollector._compute_rates(prev, cur, elapsed=0.0)

        assert rates == {"eth0": {"rx_bytes_per_sec": 0.0, "tx_bytes_per_sec": 0.0}}


class TestNetworkThroughputPrimarySelection:
    def test_highest_total_bytes_fallback(self):
        from buoy.collectors.network import NetworkCollector

        interfaces = {
            "eth0": {"rx_bytes": 1000, "tx_bytes": 500},
            "wg0": {"rx_bytes": 50000, "tx_bytes": 20000},
        }
        assert NetworkCollector._pick_highest_total(interfaces) == "wg0"

    def test_highest_total_bytes_empty_returns_none(self):
        from buoy.collectors.network import NetworkCollector

        assert NetworkCollector._pick_highest_total({}) is None


class TestNetworkThroughputCollect:
    """Tests for the stateful NetworkCollector.collect_throughput()."""

    @pytest.mark.asyncio
    async def test_non_linux_returns_empty(self):
        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        coll._is_linux = False  # simulate macOS/Windows without patching platform.system globally

        assert await coll.collect_throughput() == {}

    @pytest.mark.asyncio
    async def test_unreadable_proc_returns_empty_without_raising(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        with patch("builtins.open", side_effect=OSError("no such file")):
            result = await coll.collect_throughput()

        assert result == {}

    @pytest.mark.asyncio
    async def test_first_sample_reports_zero_rates(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        text = _net_dev_text([("eth0", 500000, 300000)])
        with patch.object(
            coll, "_read_net_dev_and_route", return_value=(text, "", "/proc/net/dev")
        ):
            result = await coll.collect_throughput()

        assert result["net"]["rx_bytes_per_sec"] == 0.0
        assert result["net"]["tx_bytes_per_sec"] == 0.0
        iface = result["net"]["interfaces"][0]
        assert iface["rx_bytes"] == 500000
        assert iface["tx_bytes"] == 300000

    @pytest.mark.asyncio
    async def test_second_sample_computes_rate_against_first(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        first = _net_dev_text([("eth0", 1000, 500)])
        second = _net_dev_text([("eth0", 6000, 1500)])

        with (
            patch("time.monotonic", return_value=100.0),
            patch.object(
                coll, "_read_net_dev_and_route", return_value=(first, "", "/proc/net/dev")
            ),
        ):
            await coll.collect_throughput()

        with patch.object(
            coll, "_read_net_dev_and_route", return_value=(second, "", "/proc/net/dev")
        ):
            with patch("time.monotonic", return_value=105.0):
                result = await coll.collect_throughput()

        assert result["net"]["rx_bytes_per_sec"] == 1000.0
        assert result["net"]["tx_bytes_per_sec"] == 200.0

    @pytest.mark.asyncio
    async def test_errors_and_drops_surfaced_verbatim(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        text = _net_dev_text([("eth0", 1000, 500, 3, 7, 2, 9)])
        with patch.object(
            coll, "_read_net_dev_and_route", return_value=(text, "", "/proc/net/dev")
        ):
            result = await coll.collect_throughput()

        iface = result["net"]["interfaces"][0]
        assert iface["rx_errors"] == 3
        assert iface["rx_dropped"] == 7
        assert iface["tx_errors"] == 2
        assert iface["tx_dropped"] == 9

    @pytest.mark.asyncio
    async def test_resample_within_min_interval_returns_cached_result(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        text = _net_dev_text([("eth0", 1000, 500)])

        with (
            patch("time.monotonic", return_value=100.0),
            patch.object(
                coll, "_read_net_dev_and_route", return_value=(text, "", "/proc/net/dev")
            ) as mock_read,
        ):
            first = await coll.collect_throughput()

        with (
            patch("time.monotonic", return_value=100.5),  # < _MIN_SAMPLE_INTERVAL later
            patch.object(coll, "_read_net_dev_and_route") as mock_read2,
        ):
            second = await coll.collect_throughput()

        assert second is first
        mock_read2.assert_not_called()
        assert mock_read.call_count == 1

    @pytest.mark.asyncio
    async def test_primary_uses_default_route(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        dev_text = _net_dev_text([("eth0", 1000, 500), ("wg0", 90000, 90000)])
        route_text = _route_text([("eth0", "00000000", 100)])
        with patch.object(
            coll, "_read_net_dev_and_route", return_value=(dev_text, route_text, "/proc/net/dev")
        ):
            result = await coll.collect_throughput()

        assert result["net"]["primary"] == "eth0"

    @pytest.mark.asyncio
    async def test_primary_falls_back_to_highest_total_without_route(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        dev_text = _net_dev_text([("eth0", 1000, 500), ("wg0", 90000, 90000)])
        with patch.object(
            coll, "_read_net_dev_and_route", return_value=(dev_text, "", "/proc/net/dev")
        ):
            result = await coll.collect_throughput()

        assert result["net"]["primary"] == "wg0"

    @pytest.mark.asyncio
    async def test_source_reflects_host_netns_path(self):
        import io
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        text = _net_dev_text([("eth0", 1000, 500)])

        def fake_open(path, *args, **kwargs):
            if path == "/proc/1/net/dev":
                return io.StringIO(text)
            if path == "/proc/1/net/route":
                return io.StringIO("")
            raise FileNotFoundError(path)

        with patch("builtins.open", side_effect=fake_open):
            result = await coll.collect_throughput()

        assert result["net"]["source"] == "/proc/1/net/dev"

    @pytest.mark.asyncio
    async def test_falls_back_to_container_netns_path(self):
        import io
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        text = _net_dev_text([("eth0", 1000, 500)])

        def fake_open(path, *args, **kwargs):
            if path == "/proc/net/dev":
                return io.StringIO(text)
            if path == "/proc/net/route":
                return io.StringIO("")
            raise FileNotFoundError(path)

        with patch("builtins.open", side_effect=fake_open):
            result = await coll.collect_throughput()

        assert result["net"]["source"] == "/proc/net/dev"

    @pytest.mark.asyncio
    async def test_excluded_interfaces_absent_from_result(self):
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        text = _net_dev_text([("eth0", 1000, 500), ("lo", 10, 10), ("docker0", 20, 20)])
        with patch.object(
            coll, "_read_net_dev_and_route", return_value=(text, "", "/proc/net/dev")
        ):
            result = await coll.collect_throughput()

        names = {i["name"] for i in result["net"]["interfaces"]}
        assert names == {"eth0"}

    @pytest.mark.asyncio
    async def test_stale_interface_dropped_after_disappearing(self):
        """An interface present in the first sample but gone from the second
        (e.g. a short-lived veth) must not linger in the rate-tracking state."""
        from unittest.mock import patch

        from buoy.collectors.network import NetworkCollector

        coll = NetworkCollector(_make_config())
        first = _net_dev_text([("eth0", 1000, 500), ("wg0", 2000, 1000)])
        second = _net_dev_text([("eth0", 2000, 1000)])

        with (
            patch("time.monotonic", return_value=100.0),
            patch.object(
                coll, "_read_net_dev_and_route", return_value=(first, "", "/proc/net/dev")
            ),
        ):
            await coll.collect_throughput()

        with (
            patch("time.monotonic", return_value=105.0),
            patch.object(
                coll, "_read_net_dev_and_route", return_value=(second, "", "/proc/net/dev")
            ),
        ):
            result = await coll.collect_throughput()

        names = {i["name"] for i in result["net"]["interfaces"]}
        assert names == {"eth0"}
        assert "wg0" not in coll._last_net_sample[1]
