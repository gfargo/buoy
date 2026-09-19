"""Databases plugin — Postgres/MySQL/Redis connections, replication lag, slow queries, memory.

Supports any number of targets across the three engines in one config list;
each target is probed independently and reported as its own row, with the
dashboard card aggregating worst-status-wins across all of them.

Driver packages (asyncpg, aiomysql, redis) are optional extras
(``pip install "buoy[databases]"``) and are imported lazily inside the probe
functions so this module — and therefore ``collect_all_now()`` in demo mode
and the test suite that imports every builtin module — never requires them
to be installed.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

from buoy.plugins import panel
from buoy.plugins.protocol import PanelData, Plugin, PluginManifest

_ENGINES = {"postgres", "mysql", "redis"}


class _DriverUnavailableError(Exception):
    """Raised when an optional database driver package isn't installed."""


# ── Lazy driver loading ─────────────────────────────────────────────────────


def _load_asyncpg():
    import asyncpg

    return asyncpg


def _load_aiomysql():
    import aiomysql

    return aiomysql


def _load_redis():
    import redis.asyncio as redis

    return redis


# ── Pure metric mappers (unit-testable without a live database) ────────────


def _rate(prev_value: float | None, prev_ts: float | None, value: float, ts: float) -> float | None:
    """Per-minute delta for a cumulative counter.

    Returns None for the first sample (no prior value) or across a counter
    reset (e.g. server restart), since a negative delta is meaningless.
    """
    if prev_value is None or prev_ts is None:
        return None
    elapsed = ts - prev_ts
    if elapsed <= 0:
        return None
    delta = value - prev_value
    if delta < 0:
        return None
    return delta * 60 / elapsed


def _pg_metrics(row: dict[str, Any]) -> dict[str, Any]:
    """Map a single pg_stat_activity/settings query result row to metrics."""
    total = int(row.get("total_connections", 0))
    active = int(row.get("active_connections", 0))
    max_connections = int(row.get("max_connections", 0))
    in_recovery = bool(row.get("in_recovery", False))
    replay_lag_s = row.get("replay_lag_s")
    caught_up = bool(row.get("caught_up", False))
    slow_queries = int(row.get("slow_queries", 0))

    lag_s: float | None = None
    if in_recovery:
        lag_s = 0.0 if caught_up else (float(replay_lag_s) if replay_lag_s is not None else None)

    return {
        "connections": total,
        "active_connections": active,
        "max_connections": max_connections,
        "connections_pct": (total / max_connections * 100) if max_connections else 0.0,
        "is_replica": in_recovery,
        "replication_lag_s": lag_s,
        "replication_broken": in_recovery and lag_s is None,
        "slow_queries": slow_queries,
    }


def _mysql_metrics(
    status: dict[str, str],
    max_connections: int,
    replica_row: dict[str, Any] | None,
    prev_slow_queries: float | None,
    prev_ts: float | None,
    now: float,
) -> dict[str, Any]:
    """Map SHOW GLOBAL STATUS + @@max_connections + replica status to metrics."""
    threads_connected = int(status.get("Threads_connected", 0))
    slow_queries_total = float(status.get("Slow_queries", 0))
    slow_queries_per_min = _rate(prev_slow_queries, prev_ts, slow_queries_total, now)

    is_replica = replica_row is not None
    lag_s: float | None = None
    replication_broken = False
    if is_replica:
        raw_lag = replica_row.get("Seconds_Behind_Source", replica_row.get("Seconds_Behind_Master"))
        if raw_lag is None:
            replication_broken = True
        else:
            lag_s = float(raw_lag)

    return {
        "connections": threads_connected,
        "max_connections": max_connections,
        "connections_pct": (threads_connected / max_connections * 100) if max_connections else 0.0,
        "is_replica": is_replica,
        "replication_lag_s": lag_s,
        "replication_broken": replication_broken,
        "slow_queries_total": slow_queries_total,
        "slow_queries_per_min": slow_queries_per_min,
    }


def _parse_redis_info(text: str) -> dict[str, str]:
    """Parse Redis INFO output ('key:value' lines, '#' section headers skipped)."""
    info: dict[str, str] = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        key, _, value = line.partition(":")
        info[key] = value
    return info


def _redis_metrics(
    info: dict[str, Any],
    prev_evicted: float | None,
    prev_ts: float | None,
    now: float,
) -> dict[str, Any]:
    connected_clients = int(info.get("connected_clients", 0))
    used_memory = int(info.get("used_memory", 0))
    maxmemory = int(info.get("maxmemory", 0))
    evicted_total = float(info.get("evicted_keys", 0))
    evicted_per_min = _rate(prev_evicted, prev_ts, evicted_total, now)

    role = info.get("role", "master")
    is_replica = role == "slave"
    link_status = info.get("master_link_status")  # "up" | "down" | None (not a replica)

    return {
        "connected_clients": connected_clients,
        "used_memory_bytes": used_memory,
        "maxmemory_bytes": maxmemory,
        "memory_pct": (used_memory / maxmemory * 100) if maxmemory else None,
        "is_replica": is_replica,
        "replica_link_status": link_status,
        "replication_broken": is_replica and link_status == "down",
        "evicted_keys_total": evicted_total,
        "evicted_keys_per_min": evicted_per_min,
    }


def _status_for(
    metrics: dict[str, Any],
    connections_warn_pct: float,
    connections_critical_pct: float,
    replication_lag_warn_s: float,
    replication_lag_critical_s: float,
) -> str:
    """Worst-status-wins evaluation of a single target's metrics against thresholds."""
    if metrics.get("replication_broken"):
        return "error"

    conn_pct = metrics.get("connections_pct")
    status = "ok"
    if conn_pct is not None:
        if conn_pct >= connections_critical_pct:
            status = "error"
        elif conn_pct >= connections_warn_pct:
            status = "warn"

    lag_s = metrics.get("replication_lag_s")
    if lag_s is not None:
        if lag_s >= replication_lag_critical_s:
            status = "error"
        elif lag_s >= replication_lag_warn_s and status != "error":
            status = "warn"

    mem_pct = metrics.get("memory_pct")
    if mem_pct is not None and mem_pct >= 95 and status != "error":
        status = "warn"

    return status


def _worst(statuses: list[str]) -> str:
    """Aggregate per-target statuses, worst wins.

    `warn`/`error` outrank `unavailable`: a target we simply couldn't probe
    (missing driver) must not mask a real alert on another target.
    """
    if "error" in statuses:
        return "error"
    if "warn" in statuses:
        return "warn"
    if "unavailable" in statuses:
        return "unavailable"
    return "ok"


# ── Plugin ───────────────────────────────────────────────────────────────


class DatabasesPlugin(Plugin):
    """Shows connections, replication lag, slow queries, and memory/evictions per database."""

    manifest = PluginManifest(
        id="databases",
        name="Databases",
        icon="🗄️",
        description="Connections, replication lag, slow queries, memory/evictions",
        version="1.0.0",
        config_schema={
            "targets": {"type": "array", "default": []},
            "connections_warn_pct": {"type": "integer", "default": 80},
            "connections_critical_pct": {"type": "integer", "default": 95},
            "replication_lag_warn_s": {"type": "integer", "default": 30},
            "replication_lag_critical_s": {"type": "integer", "default": 300},
            "slow_query_seconds": {"type": "integer", "default": 5},
            "timeout_s": {"type": "integer", "default": 5},
        },
        refresh_interval=60,
    )

    def __init__(self) -> None:
        super().__init__()
        # Previous cumulative-counter samples per target, for _rate().
        self._prev_counters: dict[str, tuple[float, float]] = {}

    @staticmethod
    def _counter_key(target: dict[str, Any]) -> str:
        """Unique key for `_prev_counters`.

        `name` is optional and engines are probed with a shared dict, so
        keying on name alone lets an unnamed target (or a same-named
        MySQL/Redis pair) collide and corrupt each other's rate history.
        """
        engine = target.get("engine", "")
        name = target.get("name") or target.get("host", "")
        port = target.get("port", "")
        return f"{engine}:{name}:{port}"

    async def collect(self) -> PanelData:
        targets = self.config.get("targets") or []
        if not targets:
            return PanelData(status="disabled", summary="Not configured")

        timeout_s = float(self.config.get("timeout_s", 5))
        results = await asyncio.gather(
            *(self._probe_target(t, timeout_s) for t in targets),
            return_exceptions=True,
        )

        rows = []
        for target, result in zip(targets, results, strict=True):
            name = target.get("name") or target.get("host") or "database"
            engine = target.get("engine", "")
            if isinstance(result, BaseException):
                rows.append(
                    {
                        "name": name,
                        "engine": engine,
                        "status": "error",
                        "error": self._sanitize_error(str(result)),
                        "metrics": {},
                    }
                )
            else:
                rows.append(result)

        overall = _worst([r["status"] for r in rows])

        if len(rows) == 1:
            summary = self._single_summary(rows[0])
        else:
            needing_attention = [r for r in rows if r["status"] in ("warn", "error", "unavailable")]
            summary = f"{len(rows)} databases"
            if needing_attention:
                summary += f" · {len(needing_attention)} needs attention"
                unavailable = [r["name"] for r in needing_attention if r["status"] == "unavailable"]
                if unavailable:
                    summary += f" ({', '.join(unavailable)} unavailable)"

        return PanelData(status=overall, summary=summary, detail={"databases": rows})

    def _single_summary(self, row: dict[str, Any]) -> str:
        name = row["name"]
        if row["status"] == "unavailable":
            return f"{name}: {row.get('error', 'unavailable')}"
        if row["status"] == "error" and not row.get("metrics"):
            return f"{name}: {row.get('error', 'error')}"
        m = row.get("metrics", {})
        conns = m.get("connections")
        max_conns = m.get("max_connections")
        if conns is not None and max_conns:
            return f"{name} · {conns}/{max_conns} conns"
        if conns is not None:
            return f"{name} · {conns} conns"
        clients = m.get("connected_clients")
        if clients is not None:
            return f"{name} · {clients} clients"
        return name

    @staticmethod
    def _sanitize_error(message: str) -> str:
        """Strip anything that looks like a connection string or credential.

        Driver exceptions can embed DSNs (postgresql://user:pw@host/db) or raw
        host:port pairs; only a short, generic reason is safe to serve to the
        browser via /api and the WS feed.
        """
        lower = message.lower()
        if "password" in lower or "authentication" in lower:
            return "authentication failed"
        if "://" in message or "@" in message:
            return "connection failed"
        if "timeout" in lower or "timed out" in lower:
            return "timed out"
        if "refused" in lower or "unreachable" in lower or "name or service" in lower:
            return "unreachable"
        return "connection failed"

    async def _probe_target(self, target: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        name = target.get("name") or target.get("host") or "database"
        engine = target.get("engine", "")
        if engine not in _ENGINES:
            return {
                "name": name,
                "engine": engine,
                "status": "error",
                "error": f"unknown engine '{engine}'",
                "metrics": {},
            }

        probe = {
            "postgres": self._probe_postgres,
            "mysql": self._probe_mysql,
            "redis": self._probe_redis,
        }[engine]

        try:
            metrics = await asyncio.wait_for(probe(target, timeout_s), timeout=timeout_s)
        except _DriverUnavailableError as e:
            return {
                "name": name,
                "engine": engine,
                "status": "unavailable",
                "error": str(e),
                "metrics": {},
            }
        except TimeoutError:
            return {
                "name": name,
                "engine": engine,
                "status": "error",
                "error": "timed out",
                "metrics": {},
            }
        except Exception as e:  # noqa: BLE001
            return {
                "name": name,
                "engine": engine,
                "status": "error",
                "error": self._sanitize_error(str(e)),
                "metrics": {},
            }

        status = _status_for(
            metrics,
            connections_warn_pct=float(self.config.get("connections_warn_pct", 80)),
            connections_critical_pct=float(self.config.get("connections_critical_pct", 95)),
            replication_lag_warn_s=float(self.config.get("replication_lag_warn_s", 30)),
            replication_lag_critical_s=float(self.config.get("replication_lag_critical_s", 300)),
        )
        return {"name": name, "engine": engine, "status": status, "error": "", "metrics": metrics}

    def _resolve_password(self, target: dict[str, Any]) -> str:
        password_env = target.get("password_env")
        if password_env:
            import os

            return os.environ.get(password_env, "")
        return target.get("password", "")

    async def _probe_postgres(self, target: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        try:
            asyncpg = _load_asyncpg()
        except ImportError as e:
            raise _DriverUnavailableError('Install buoy[databases] ("asyncpg")') from e

        conn = await asyncpg.connect(
            host=target.get("host"),
            port=int(target.get("port", 5432)),
            user=target.get("user"),
            password=self._resolve_password(target),
            database=target.get("database", "postgres"),
            ssl="require" if target.get("tls") else None,
            timeout=timeout_s,
        )
        try:
            row = await conn.fetchrow(
                """
                select
                    (select count(*) from pg_stat_activity) as total_connections,
                    (select count(*) from pg_stat_activity where state = 'active') as active_connections,
                    current_setting('max_connections')::int as max_connections,
                    pg_is_in_recovery() as in_recovery,
                    (pg_last_wal_receive_lsn() = pg_last_wal_replay_lsn()) as caught_up,
                    extract(epoch from (now() - pg_last_xact_replay_timestamp())) as replay_lag_s,
                    (
                        select count(*) from pg_stat_activity
                        where state = 'active'
                          and query_start < now() - ($1 || ' seconds')::interval
                    ) as slow_queries
                """,
                str(int(self.config.get("slow_query_seconds", 5))),
            )
            return _pg_metrics(dict(row))
        finally:
            await conn.close()

    async def _probe_mysql(self, target: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        try:
            aiomysql = _load_aiomysql()
        except ImportError as e:
            raise _DriverUnavailableError('Install buoy[databases] ("aiomysql")') from e

        tls_ctx = None
        if target.get("tls"):
            import ssl as ssl_module

            tls_ctx = ssl_module.create_default_context()

        conn = await aiomysql.connect(
            host=target.get("host"),
            port=int(target.get("port", 3306)),
            user=target.get("user"),
            password=self._resolve_password(target),
            db=target.get("database"),
            ssl=tls_ctx,
            connect_timeout=timeout_s,
        )
        try:
            async with conn.cursor(aiomysql.DictCursor) as cur:
                await cur.execute("SHOW GLOBAL STATUS")
                status = {row["Variable_name"]: row["Value"] for row in await cur.fetchall()}

                await cur.execute("SELECT @@max_connections AS max_connections")
                max_connections = int((await cur.fetchone())["max_connections"])

                replica_row = None
                for stmt in ("SHOW REPLICA STATUS", "SHOW SLAVE STATUS"):
                    try:
                        await cur.execute(stmt)
                        replica_row = await cur.fetchone()
                        break
                    except Exception:  # noqa: BLE001
                        continue

            now = time.monotonic()
            key = self._counter_key(target)
            prev = self._prev_counters.get(key)
            prev_value, prev_ts = prev if prev else (None, None)
            metrics = _mysql_metrics(status, max_connections, replica_row, prev_value, prev_ts, now)
            self._prev_counters[key] = (metrics["slow_queries_total"], now)
            return metrics
        finally:
            conn.close()

    async def _probe_redis(self, target: dict[str, Any], timeout_s: float) -> dict[str, Any]:
        try:
            redis = _load_redis()
        except ImportError as e:
            raise _DriverUnavailableError('Install buoy[databases] ("redis")') from e

        try:
            db_index = int(target.get("database", 0) or 0)
        except (TypeError, ValueError):
            db_index = 0

        client = redis.Redis(
            host=target.get("host"),
            port=int(target.get("port", 6379)),
            password=self._resolve_password(target) or None,
            db=db_index,
            ssl=bool(target.get("tls")),
            socket_timeout=timeout_s,
            socket_connect_timeout=timeout_s,
        )
        try:
            info = await client.info()

            now = time.monotonic()
            key = self._counter_key(target)
            prev = self._prev_counters.get(key)
            prev_value, prev_ts = prev if prev else (None, None)
            metrics = _redis_metrics(info, prev_value, prev_ts, now)
            self._prev_counters[key] = (metrics["evicted_keys_total"], now)
            return metrics
        finally:
            await client.aclose()

    def demo_data(self) -> PanelData:
        rows = [
            {
                "name": "app-db",
                "engine": "postgres",
                "status": "ok",
                "error": "",
                "metrics": {
                    "connections": 22,
                    "active_connections": 4,
                    "max_connections": 100,
                    "connections_pct": 22.0,
                    "is_replica": False,
                    "replication_lag_s": None,
                    "replication_broken": False,
                    "slow_queries": 0,
                },
            },
            {
                "name": "app-db-replica",
                "engine": "mysql",
                "status": "warn",
                "error": "",
                "metrics": {
                    "connections": 18,
                    "max_connections": 150,
                    "connections_pct": 12.0,
                    "is_replica": True,
                    "replication_lag_s": 42.0,
                    "replication_broken": False,
                    "slow_queries_total": 128,
                    "slow_queries_per_min": 2.5,
                },
            },
            {
                "name": "cache",
                "engine": "redis",
                "status": "warn",
                "error": "",
                "metrics": {
                    "connected_clients": 36,
                    "used_memory_bytes": 950_000_000,
                    "maxmemory_bytes": 1_000_000_000,
                    "memory_pct": 95.0,
                    "is_replica": False,
                    "replica_link_status": None,
                    "evicted_keys_total": 4200,
                    "evicted_keys_per_min": 14.0,
                },
            },
        ]
        return PanelData(
            status="warn",
            summary="3 databases · 2 needs attention",
            detail={"databases": rows},
        )

    def render(self, data: PanelData) -> list[dict] | None:
        rows = data.detail.get("databases") or []
        if not rows:
            return [panel.text("No databases configured", status="dim")]

        table_rows = []
        for r in rows:
            m = r.get("metrics", {})
            conns_pct = m.get("connections_pct")
            conns = "—"
            if m.get("connections") is not None:
                max_c = m.get("max_connections")
                conns = f"{m['connections']}/{max_c}" if max_c else str(m["connections"])
            elif m.get("connected_clients") is not None:
                conns = str(m["connected_clients"])

            lag = "—"
            if m.get("replica_link_status") == "down":
                lag = "link down"
            elif m.get("replication_broken"):
                lag = "broken"
            elif m.get("replication_lag_s") is not None:
                lag = f"{m['replication_lag_s']:.0f}s"

            notes = r.get("error") or ""
            if not notes:
                if m.get("slow_queries"):
                    notes = f"{m['slow_queries']} slow queries"
                elif m.get("slow_queries_per_min"):
                    notes = f"{m['slow_queries_per_min']:.1f} slow/min"
                elif m.get("evicted_keys_per_min"):
                    notes = f"{m['evicted_keys_per_min']:.1f} evictions/min"
                elif m.get("memory_pct") is not None:
                    notes = f"{m['memory_pct']:.0f}% mem"

            table_rows.append(
                [
                    panel.cell(r.get("name", "")),
                    panel.cell(r.get("engine", "")),
                    panel.cell(conns, status=r.get("status") if conns_pct is not None else None),
                    panel.cell(lag, status="error" if lag in ("broken", "link down") else None),
                    panel.cell(notes or "—", status=r.get("status")),
                ]
            )

        return [panel.table(["Database", "Engine", "Conns", "Lag", "Notes"], table_rows)]
