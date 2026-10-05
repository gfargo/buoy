"""Docker collector — container discovery, stats, inspect, logs, restart.

Uses the Docker CLI via async subprocess. Validates all container names
before passing to shell commands.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import re
import time
from typing import TYPE_CHECKING

from buoy.subprocess_utils import communicate

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.collectors.docker")

_CONTAINER_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_.\-]*$")
# The group label name is interpolated into the `docker ps --format` Go
# template, so it must be restricted to safe label-name characters — an
# unvalidated value would let a config-controlled string break out of the
# `{{.Label "..."}}` template (quote/brace injection).
_LABEL_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]*$")
_LIST_CACHE_TTL = 5.0

# Docker appends this to `.Status` for containers with a healthcheck, e.g.
# "Up 2 hours (healthy)" / "Up 12 seconds (health: starting)". Containers
# without a healthcheck have no such suffix — this must never be sourced from
# `{{.State.Health.Status}}` via `inspect`, which nil-pointer-panics (and
# fails the *entire* batch) on any container that lacks one.
_HEALTH_RE = re.compile(r"\((healthy|unhealthy|health: starting)\)")

# `docker logs --timestamps` prefixes each line with an RFC3339Nano
# timestamp (e.g. "2024-01-15T10:23:45.123456789Z message"), which sorts
# correctly as a plain string — trailing zeros are trimmed by Go's
# formatting, but that only ever shortens a shared numeric prefix, so
# lexicographic order still matches chronological order.
_LOG_TIMESTAMP_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z")


def _valid_name(name: str) -> bool:
    return bool(_CONTAINER_NAME_RE.match(name)) and len(name) <= 128


def _first_name(names: str) -> str:
    """`docker ps`/`docker stats` can render comma-joined aliases for a
    container with multiple names; take the first (they refer to the same
    container, so any consistent choice matches across both commands)."""
    return names.split(",", 1)[0].strip()


def _parse_health(status: str) -> str | None:
    """Extract health state from a `docker ps` Status string, e.g. 'Up 2
    hours (healthy)' -> 'healthy'. None when the container has no healthcheck."""
    match = _HEALTH_RE.search(status or "")
    if not match:
        return None
    value = match.group(1)
    return "starting" if value == "health: starting" else value


def _log_line_sort_key(line: str) -> str:
    """Sort key for a --timestamps log line: the leading timestamp, or ""
    for a line that doesn't start with one (sorts first, best-effort)."""
    match = _LOG_TIMESTAMP_RE.match(line)
    return match.group(0) if match else ""


class DockerCollector:
    """Collects Docker container information via CLI."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._available: bool | None = None
        self._containers_cache: list[dict] | None = None
        self._containers_cache_ts: float = 0.0
        self._list_lock = asyncio.Lock()
        self._states_cache: list[dict] | None = None
        self._states_cache_ts: float = 0.0
        self._states_lock = asyncio.Lock()
        self._stats_cache: dict[str, dict] = {}
        self._stats_cache_ts: float = 0.0
        self._stats_task: asyncio.Task | None = None
        self._group_label_warned = False

    async def _run(self, *args: str, timeout: float = 10) -> tuple[int, str, str]:
        """Run a docker command and return (returncode, stdout, stderr)."""
        try:
            proc = await asyncio.create_subprocess_exec(
                "docker",
                *args,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await communicate(proc, timeout=timeout)
            return proc.returncode, stdout.decode().strip(), stderr.decode().strip()
        except TimeoutError:
            return 1, "", "timeout"
        except FileNotFoundError:
            return 1, "", "docker not found"
        except Exception as e:
            logger.debug("docker %s failed: %s", " ".join(args), e, exc_info=True)
            return 1, "", str(e)

    async def is_available(self, *, force: bool = False) -> bool:
        """Check if Docker CLI is accessible.

        Cached forever by default (mirrors the 5s-stats-path caching elsewhere
        in this class) since most callers hit this every stats tick. Pass
        ``force=True`` to reset the cache and re-probe — used by the
        capability-refresh loop so a socket that recovers after startup is
        eventually reflected instead of staying stuck on the first result.
        """
        if force:
            self._available = None
        if self._available is None:
            code, _, _ = await self._run("info", "--format", "{{.ID}}", timeout=5)
            self._available = code == 0
        return self._available

    async def list_containers(self) -> list[dict]:
        """List running containers with name and host port (cached, 5s TTL)."""
        now = time.monotonic()
        if self._containers_cache is not None and now - self._containers_cache_ts < _LIST_CACHE_TTL:
            return self._containers_cache

        async with self._list_lock:
            now = time.monotonic()
            if (
                self._containers_cache is not None
                and now - self._containers_cache_ts < _LIST_CACHE_TTL
            ):
                return self._containers_cache

            containers = await self._fetch_containers()
            self._containers_cache = containers
            self._containers_cache_ts = time.monotonic()
            return containers

    async def _fetch_containers(self) -> list[dict]:
        """Shell out to `docker ps` and parse the container list, including compose service label
        and (when configured) a group label such as `com.docker.compose.project`."""
        group_label = self.config.services.group_label
        include_group = (
            isinstance(group_label, str)
            and bool(group_label)
            and _LABEL_NAME_RE.match(group_label) is not None
        )
        if group_label and not include_group and not self._group_label_warned:
            logger.warning(
                "services.group_label %r is not a valid Docker label name — falling back to "
                "no label-based grouping",
                group_label,
            )
            self._group_label_warned = True

        fmt = '{{.Names}}\t{{.Ports}}\t{{.Label "com.docker.compose.service"}}'
        if include_group:
            fmt += f'\t{{{{.Label "{group_label}"}}}}'

        code, stdout, _ = await self._run("ps", "--format", fmt)
        if code != 0 or not stdout:
            return []

        containers = []
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            # The group/project field is kept last so a stray tab embedded in
            # a label value only ever corrupts that trailing field, never
            # shifts name/ports/service out of position.
            parts = line.split("\t", 3)
            name = parts[0].strip()
            ports_str = parts[1].strip() if len(parts) > 1 else ""
            service = parts[2].strip() if len(parts) > 2 else ""
            project = parts[3].strip() if include_group and len(parts) > 3 else ""

            host_port = self._parse_first_port(ports_str)
            containers.append(
                {
                    "name": name,
                    "host_port": host_port,
                    "service": service,
                    "project": project,
                }
            )

        return containers

    async def collect_summary(self) -> dict:
        """Collect container count and list (all containers, with state/health/
        cpu/mem) for the stats endpoint.

        ``containers`` stays the *running* count (feeds the headline gauge and
        history) while ``containers_list`` includes stopped/exited containers
        too, so they're visible without an extra click. Stats
        (``docker stats --no-stream``) are served from a stale-while-revalidate
        cache and never awaited here, so a slow/cold stats call can't add
        latency to this hot path.
        """
        containers, states = await asyncio.gather(
            self.list_containers(), self._container_states_cached()
        )
        stats = await self._container_stats_cached() if self.config.features.container_stats else {}

        if not states and containers:
            # `docker ps -a` (states) failed/timed out while the separate `docker
            # ps` call (containers) succeeded — fall back to the running set so
            # `containers_list` doesn't go empty while `containers` still shows a
            # count. Self-heals once the states cache TTL expires.
            states = [
                {"name": c["name"], "state": "running", "status": "", "health": None}
                for c in containers
            ]

        containers_list = []
        for s in states:
            name = s["name"]
            entry = {
                "name": name,
                "state": s["state"],
                "status": s["status"],
                "health": s["health"],
                "cpu_pct": None,
                "mem_usage": None,
                "mem_pct": None,
            }
            entry.update(stats.get(name, {}))
            containers_list.append(entry)

        return {
            "containers": len(containers),
            "containers_list": containers_list,
        }

    async def _fetch_container_states(self) -> list[dict]:
        """Batch-fetch name/state/status/health for ALL containers (running +
        stopped) via `docker ps -a`. One subprocess regardless of container
        count."""
        code, stdout, _ = await self._run(
            "ps", "-a", "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"
        )
        if code != 0 or not stdout:
            return []

        states = []
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t", 2)
            if len(parts) < 3:
                continue
            name = _first_name(parts[0])
            if not name:
                continue
            status = parts[2].strip()
            states.append(
                {
                    "name": name,
                    "state": parts[1].strip(),
                    "status": status,
                    "health": _parse_health(status),
                }
            )
        return states

    async def _container_states_cached(self) -> list[dict]:
        """`_fetch_container_states()` behind the same TTL cache pattern as
        `list_containers()`."""
        now = time.monotonic()
        if self._states_cache is not None and now - self._states_cache_ts < _LIST_CACHE_TTL:
            return self._states_cache

        async with self._states_lock:
            now = time.monotonic()
            if self._states_cache is not None and now - self._states_cache_ts < _LIST_CACHE_TTL:
                return self._states_cache

            states = await self._fetch_container_states()
            self._states_cache = states
            self._states_cache_ts = time.monotonic()
            return states

    async def _fetch_container_stats(self) -> dict[str, dict]:
        """Batch-fetch cpu/mem for all running containers via one
        `docker stats --no-stream` call (no name args = all running)."""
        code, stdout, _ = await self._run(
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.MemPerc}}",
            timeout=15,
        )
        if code != 0 or not stdout:
            return {}

        stats: dict[str, dict] = {}
        for line in stdout.split("\n"):
            if not line.strip():
                continue
            parts = line.split("\t")
            if len(parts) < 4:
                continue
            name = _first_name(parts[0])
            if not name:
                continue
            stats[name] = {
                "cpu_pct": parts[1].strip(),
                "mem_usage": parts[2].strip(),
                "mem_pct": parts[3].strip(),
            }
        return stats

    async def _refresh_stats(self) -> None:
        try:
            self._stats_cache = await self._fetch_container_stats()
        except Exception:
            logger.debug("docker stats refresh failed", exc_info=True)
        finally:
            self._stats_cache_ts = time.monotonic()

    async def _container_stats_cached(self) -> dict[str, dict]:
        """Stale-while-revalidate: always return immediately (empty on cold
        start), kicking off a background refresh when the cache is stale and
        no refresh is already in flight. Never awaits the `docker stats`
        subprocess inline — it's ~1-2s and scales with container count, and
        this is called on every `/api/stats` request."""
        now = time.monotonic()
        stale = now - self._stats_cache_ts >= self.config.refresh.container_stats_interval
        if stale and (self._stats_task is None or self._stats_task.done()):
            self._stats_task = asyncio.create_task(self._refresh_stats())
        return self._stats_cache

    async def aclose(self) -> None:
        """Cancel an in-flight background stats refresh, if any (called from
        on_shutdown so a refresh doesn't outlive the app and log a 'Task was
        destroyed but it is pending' warning)."""
        if self._stats_task is not None and not self._stats_task.done():
            self._stats_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stats_task

    async def inspect_container(self, name: str) -> dict:
        """Get detailed info for a single container."""
        if not _valid_name(name):
            return {"error": "invalid container name"}

        # Basic inspect
        code, stdout, stderr = await self._run(
            "inspect",
            "--format",
            '{"status":"{{.State.Status}}","started":"{{.State.StartedAt}}",'
            '"image":"{{.Config.Image}}","restart_count":{{.RestartCount}},'
            '"pid":{{.State.Pid}},"image_created":"{{.Created}}"}',
            name,
        )

        if code != 0:
            return {"error": stderr or "container not found"}

        try:
            info = json.loads(stdout)
        except json.JSONDecodeError:
            return {"error": "failed to parse inspect output"}

        info["name"] = name

        # Resource usage (docker stats --no-stream)
        stats_code, stats_out, _ = await self._run(
            "stats",
            "--no-stream",
            "--format",
            '{"cpu_pct":"{{.CPUPerc}}","mem_usage":"{{.MemUsage}}",'
            '"mem_pct":"{{.MemPerc}}","net_io":"{{.NetIO}}","block_io":"{{.BlockIO}}"}',
            name,
            timeout=15,
        )

        if stats_code == 0 and stats_out:
            try:
                info["resources"] = json.loads(stats_out)
            except json.JSONDecodeError:
                pass

        # Ports
        ports_code, ports_out, _ = await self._run("port", name, timeout=5)
        info["ports"] = ports_out if ports_code == 0 else ""

        return info

    async def get_logs(self, name: str, tail: int = 30) -> dict:
        """Get last N lines of container logs, stdout and stderr interleaved by timestamp."""
        if not _valid_name(name):
            return {"error": "invalid container name"}

        code, stdout, stderr = await self._run(
            "logs",
            "--tail",
            str(tail),
            "--timestamps",
            name,
            timeout=5,
        )

        # docker returns the last `tail` log records total already correctly
        # ordered *within* each stream — but stdout and stderr arrive on
        # separate pipes, so simply concatenating them (stdout, then all of
        # stderr) destroys the real chronological interleaving, and a chatty
        # stderr can push all of stdout out of the requested tail (BUG-49).
        # --timestamps was already being requested but never used for this;
        # sort the merged lines by their leading timestamp to restore it.
        stdout_lines = stdout.strip().split("\n") if stdout.strip() else []
        stderr_lines = stderr.strip().split("\n") if stderr.strip() else []
        lines = sorted(stdout_lines + stderr_lines, key=_log_line_sort_key)
        return {"container": name, "lines": lines[-tail:]}

    async def stream_logs(self, name: str, tail: int = 100, max_line_bytes: int = 8192):
        """Follow container logs, yielding ``{"stream": "stdout"|"stderr", "line": str}``.

        Unlike ``get_logs``/``_run``, this is a long-running follow (``docker
        logs --follow``), not a one-shot call bounded by ``communicate``'s
        timeout — the caller is responsible for stopping iteration (e.g. by
        closing the async generator), which cancels the pump tasks and kills
        the child process in the ``finally`` block below.
        """
        if not _valid_name(name):
            raise ValueError("invalid container name")

        proc = await asyncio.create_subprocess_exec(
            "docker",
            "logs",
            "--follow",
            "--timestamps",
            "--tail",
            str(int(tail)),
            name,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            limit=max_line_bytes * 2,
        )

        queue: asyncio.Queue = asyncio.Queue(maxsize=1000)
        sentinel = object()

        async def _pump(stream, label: str) -> None:
            try:
                while True:
                    try:
                        raw = await stream.readline()
                    except (ValueError, asyncio.LimitOverrunError):
                        # Line exceeded the stream's internal buffer limit.
                        # readline() already discarded the offending bytes
                        # from its buffer, so the next call resumes cleanly.
                        await queue.put({"stream": label, "line": "…[truncated: line too long]"})
                        continue
                    if not raw:
                        break
                    line = raw.decode("utf-8", errors="replace").rstrip("\n")
                    encoded = line.encode("utf-8")
                    if len(encoded) > max_line_bytes:
                        # Truncate on the encoded bytes (not decoded chars) so
                        # multi-byte UTF-8 content honors the documented byte
                        # budget; errors="ignore" drops a boundary-split char.
                        line = (
                            encoded[:max_line_bytes].decode("utf-8", errors="ignore")
                            + "…[truncated]"
                        )
                    await queue.put({"stream": label, "line": line})
            finally:
                await queue.put(sentinel)

        pumps = [
            asyncio.create_task(_pump(proc.stdout, "stdout")),
            asyncio.create_task(_pump(proc.stderr, "stderr")),
        ]

        try:
            finished = 0
            while finished < len(pumps):
                item = await queue.get()
                if item is sentinel:
                    finished += 1
                    continue
                yield item
        finally:
            for task in pumps:
                task.cancel()
            await asyncio.gather(*pumps, return_exceptions=True)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            await proc.wait()

    async def restart_container(self, name: str) -> dict:
        """Restart a container by name."""
        if not _valid_name(name):
            return {"success": False, "error": "invalid container name"}

        code, _, stderr = await self._run("restart", name, timeout=30)
        if code == 0:
            return {"success": True, "container": name}
        return {"success": False, "error": stderr or "restart failed"}

    async def list_container_states(self) -> list[dict]:
        """Return name/status/restart_count for all containers (running + stopped)."""
        # Get all container IDs (including stopped)
        code, stdout, _ = await self._run("ps", "-aq", "--no-trunc")
        if code != 0 or not stdout:
            return []

        ids = [i for i in stdout.split("\n") if i.strip()]
        if not ids:
            return []

        # Batch inspect: one call for all IDs
        inspect_code, inspect_out, _ = await self._run(
            "inspect",
            "--format",
            '{"name":"{{.Name}}","status":"{{.State.Status}}","restart_count":{{.RestartCount}}}',
            *ids,
        )
        if inspect_code != 0 or not inspect_out:
            return []

        states = []
        for line in inspect_out.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
                name = obj.get("name", "").lstrip("/")
                if name:
                    states.append(
                        {
                            "name": name,
                            "status": obj.get("status", "unknown"),
                            "restart_count": int(obj.get("restart_count", 0)),
                        }
                    )
            except (json.JSONDecodeError, ValueError):
                continue
        return states

    # ── Helpers ────────────────────────────────────────────────────────────────

    @staticmethod
    def _parse_first_port(ports_str: str) -> int | None:
        """Extract the first host-bound port from Docker ports string."""
        if not ports_str:
            return None

        for mapping in ports_str.split(","):
            mapping = mapping.strip()
            if "->" in mapping:
                left = mapping.split("->")[0]
                if ":" in left:
                    port_str = left.rsplit(":", 1)[1]
                else:
                    port_str = left
                try:
                    return int(port_str)
                except ValueError:
                    continue
        return None
