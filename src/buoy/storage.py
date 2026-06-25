"""SQLite ring buffer for 24h metric history.

When features.history is enabled, metrics are stored in a local SQLite database.
Auto-prunes entries older than 24h on each write cycle.

Storage location: /data/buoy.db (Docker volume) or ./buoy.db (local dev).
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from buoy.config import BuoyConfig

RETENTION_SECONDS = 86400  # 24 hours
DB_FILENAME = "buoy.db"


class MetricStore:
    """SQLite-backed ring buffer for time-series metric storage."""

    def __init__(self, config: BuoyConfig):
        self.config = config
        self._conn: sqlite3.Connection | None = None
        self._db_path: Path | None = None

    def open(self):
        """Open (or create) the SQLite database."""
        # Determine storage path
        data_dir = Path("/data")
        if not data_dir.exists():
            data_dir = Path(".")

        self._db_path = data_dir / DB_FILENAME
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._create_tables()

    def close(self):
        """Close the database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None

    def record(self, collector: str, data: dict):
        """Store a metric snapshot.

        Args:
            collector: Name of the collector (e.g., 'system', 'docker', 'disk')
            data: The collected data dict to store as JSON
        """
        if not self._conn:
            return

        ts = int(time.time())
        try:
            self._conn.execute(
                "INSERT INTO metrics (ts, collector, data) VALUES (?, ?, ?)",
                (ts, collector, json.dumps(data)),
            )
            self._conn.commit()
        except sqlite3.Error:
            pass

    def prune(self):
        """Delete entries older than 24h."""
        if not self._conn:
            return

        cutoff = int(time.time()) - RETENTION_SECONDS
        try:
            self._conn.execute("DELETE FROM metrics WHERE ts < ?", (cutoff,))
            self._conn.commit()
        except sqlite3.Error:
            pass

    def query(self, metric: str, period_seconds: int) -> list[tuple[int, float]]:
        """Query historical data for a specific metric.

        Args:
            metric: One of 'cpu', 'mem', 'temp', 'disk', 'containers'
            period_seconds: How far back to look (e.g., 3600 for 1h)

        Returns:
            List of (timestamp, value) tuples, ordered by time ascending.
        """
        if not self._conn:
            return []

        cutoff = int(time.time()) - period_seconds
        try:
            cursor = self._conn.execute(
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
        self._conn.commit()
