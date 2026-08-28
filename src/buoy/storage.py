"""SQLite ring buffer for 24h metric history.

When features.history is enabled, metrics are stored in a local SQLite database.
Auto-prunes entries older than 24h on each write cycle.

Storage location: /data/buoy.db (Docker volume) or ./buoy.db (local dev).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

logger = logging.getLogger("buoy.storage")

RETENTION_SECONDS = 86400  # 24 hours
DB_FILENAME = "buoy.db"


class MetricStore:
    """SQLite-backed ring buffer for time-series metric storage."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._conn: sqlite3.Connection | None = None
        self._db_path: Path | None = None
        self._lock = threading.Lock()

    def open(self):
        """Open (or create) the SQLite database."""
        # Determine storage path
        data_dir = Path("/data")
        if not data_dir.exists():
            data_dir = Path(".")

        self._db_path = data_dir / DB_FILENAME
        with self._lock:
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
            self._create_tables()

    def close(self):
        """Close the database connection."""
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None

    def record(self, collector: str, data: dict):
        """Store a metric snapshot.

        Args:
            collector: Name of the collector (e.g., 'system', 'docker', 'disk')
            data: The collected data dict to store as JSON
        """
        ts = int(time.time())
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            try:
                conn.execute(
                    "INSERT INTO metrics (ts, collector, data) VALUES (?, ?, ?)",
                    (ts, collector, json.dumps(data)),
                )
                conn.commit()
            except sqlite3.Error:
                logger.warning("storage: failed to record %s metric", collector, exc_info=True)

    def prune(self):
        """Delete entries older than 24h."""
        cutoff = int(time.time()) - RETENTION_SECONDS
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            try:
                conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
                conn.execute("DELETE FROM container_states WHERE ts < ?", (cutoff,))
                conn.commit()
            except sqlite3.Error:
                logger.warning("storage: failed to prune old entries", exc_info=True)

    def record_container_states(self, states: list[dict]):
        """Batch-insert container state samples.

        Each dict must have: name (str), status (str), restart_count (int).
        """
        if not states:
            return

        ts = int(time.time())
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            try:
                conn.executemany(
                    "INSERT INTO container_states (ts, name, status, restart_count) "
                    "VALUES (?, ?, ?, ?)",
                    [(ts, s["name"], s["status"], s["restart_count"]) for s in states],
                )
                conn.commit()
            except sqlite3.Error:
                logger.warning("storage: failed to record container states", exc_info=True)

    def query_container_history(self, name: str, period_seconds: int) -> list[tuple[int, str, int]]:
        """Return time-ordered (ts, status, restart_count) samples for a container.

        Args:
            name: Container name (exact match).
            period_seconds: How far back to look.

        Returns:
            List of (timestamp, status, restart_count) tuples ascending by time.
        """
        cutoff = int(time.time()) - period_seconds
        with self._lock:
            conn = self._conn
            if conn is None:
                return []
            try:
                cursor = conn.execute(
                    "SELECT ts, status, restart_count FROM container_states "
                    "WHERE name = ? AND ts >= ? ORDER BY ts ASC",
                    (name, cutoff),
                )
                return list(cursor)
            except sqlite3.Error:
                logger.debug(
                    "storage: failed to query container history for %s", name, exc_info=True
                )
                return []

    def record_latency(self, peer: str, latency_ms: float):
        """Store a latency measurement for a peer.

        Only persists online readings (latency_ms > 0); skips self (0) and offline (-1).
        """
        if latency_ms <= 0:
            return
        self.record("latency", {"peer": peer, "latency_ms": latency_ms})

    def record_latency_batch(self, readings: list[tuple[str, float]]):
        """Batch-insert latency readings for multiple peers in one commit.

        Args:
            readings: List of (peer, latency_ms) tuples. Only online readings
                (latency_ms > 0) are persisted; self (0) and offline (-1) are skipped.
        """
        rows = [(peer, latency_ms) for peer, latency_ms in readings if latency_ms > 0]
        if not rows:
            return

        ts = int(time.time())
        with self._lock:
            conn = self._conn
            if conn is None:
                return
            try:
                conn.executemany(
                    "INSERT INTO metrics (ts, collector, data) VALUES (?, 'latency', ?)",
                    [
                        (ts, json.dumps({"peer": peer, "latency_ms": latency_ms}))
                        for peer, latency_ms in rows
                    ],
                )
                conn.commit()
            except sqlite3.Error:
                pass

    def query_latency(self, peer: str, period_seconds: int) -> list[tuple[int, float]]:
        """Query latency history for a specific peer.

        Args:
            peer: Peer name to filter by.
            period_seconds: How far back to look.

        Returns:
            List of (timestamp, latency_ms) tuples, ordered ascending.
        """
        cutoff = int(time.time()) - period_seconds
        with self._lock:
            conn = self._conn
            if conn is None:
                return []
            try:
                cursor = conn.execute(
                    "SELECT ts, json_extract(data, '$.latency_ms') FROM metrics "
                    "WHERE collector = 'latency' AND json_valid(data) AND ts >= ? "
                    "AND json_extract(data, '$.peer') = ? "
                    "AND json_extract(data, '$.latency_ms') IS NOT NULL "
                    "ORDER BY ts ASC",
                    (cutoff, peer),
                )
                return list(cursor)
            except sqlite3.Error:
                logger.debug("storage: failed to query latency history for %s", peer, exc_info=True)
                return []

    def query(self, metric: str, period_seconds: int) -> list[tuple[int, float]]:
        """Query historical data for a specific metric.

        Args:
            metric: One of 'cpu', 'mem', 'temp', 'disk', 'containers'
            period_seconds: How far back to look (e.g., 3600 for 1h)

        Returns:
            List of (timestamp, value) tuples, ordered by time ascending.
        """
        cutoff = int(time.time()) - period_seconds
        with self._lock:
            conn = self._conn
            if conn is None:
                return []
            try:
                cursor = conn.execute(
                    "SELECT ts, data FROM metrics "
                    "WHERE collector = 'stats' AND ts >= ? ORDER BY ts ASC",
                    (cutoff,),
                )
                results = []
                for ts, data_json in cursor:
                    try:
                        data = json.loads(data_json)
                        value = self._extract_metric(data, metric)
                        if value is not None:
                            results.append((ts, value))
                    except (json.JSONDecodeError, KeyError):
                        continue
                return results
            except sqlite3.Error:
                logger.debug("storage: failed to query %s history", metric, exc_info=True)
                return []

    def _extract_metric(self, data: dict, metric: str) -> float | None:
        """Extract a specific metric value from a stats snapshot."""
        metric_map = {
            "cpu": lambda d: d.get("cpu"),
            "mem": lambda d: (
                (d.get("mem_used", 0) / d.get("mem_total", 1)) * 100
                if d.get("mem_total", 0) > 0
                else None
            ),
            "temp": lambda d: d.get("temp"),
            "disk": lambda d: d.get("disk_pct"),
            "containers": lambda d: d.get("containers"),
        }
        extractor = metric_map.get(metric)
        if not extractor:
            return None
        try:
            return extractor(data)
        except (TypeError, ZeroDivisionError):
            return None

    def _create_tables(self):
        """Create the metrics table if it doesn't exist."""
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS metrics (
                ts INTEGER NOT NULL,
                collector TEXT NOT NULL,
                data TEXT NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_ts ON metrics(ts)
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_collector_ts ON metrics(collector, ts)
        """)
        # json_valid(data) is part of the partial-index predicate (not just a query
        # filter): it keeps malformed rows out of the index so the json_extract
        # expression below is never evaluated against non-JSON data.
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_metrics_latency_peer
            ON metrics(json_extract(data, '$.peer'), ts)
            WHERE collector = 'latency' AND json_valid(data)
        """)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS container_states (
                ts INTEGER NOT NULL,
                name TEXT NOT NULL,
                status TEXT NOT NULL,
                restart_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_cstates_name_ts ON container_states(name, ts)
        """)
        self._conn.commit()
