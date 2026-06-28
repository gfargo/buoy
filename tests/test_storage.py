"""Tests for Buoy SQLite ring buffer (MetricStore)."""

import time

import pytest

from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig
from buoy.storage import RETENTION_SECONDS, MetricStore


def _make_config():
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.features = FeaturesConfig()
    return config


class TestMetricStoreLifecycle:
    """Test open/close and database creation."""

    def test_open_creates_db(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()
        assert store._conn is not None
        assert (tmp_path / "buoy.db").exists()
        store.close()

    def test_close_clears_connection(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()
        store.close()
        assert store._conn is None

    def test_double_close_is_safe(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()
        store.close()
        store.close()  # Should not raise


class TestMetricStoreRecord:
    """Test recording metrics."""

    def test_record_stores_data(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record("stats", {"cpu": 42, "temp": 55})

        cursor = store._conn.execute("SELECT COUNT(*) FROM metrics")
        assert cursor.fetchone()[0] == 1
        store.close()

    def test_record_without_open_is_noop(self):
        config = _make_config()
        store = MetricStore(config)
        # Should not raise
        store.record("stats", {"cpu": 10})

    def test_multiple_records(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        for i in range(10):
            store.record("stats", {"cpu": i * 10})

        cursor = store._conn.execute("SELECT COUNT(*) FROM metrics")
        assert cursor.fetchone()[0] == 10
        store.close()


class TestMetricStorePrune:
    """Test retention pruning."""

    def test_prune_removes_old_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        # Insert an old entry (25h ago)
        old_ts = int(time.time()) - RETENTION_SECONDS - 3600
        store._conn.execute(
            "INSERT INTO metrics (ts, collector, data) VALUES (?, ?, ?)",
            (old_ts, "stats", '{"cpu": 99}'),
        )
        # Insert a recent entry
        store.record("stats", {"cpu": 50})
        store._conn.commit()

        store.prune()

        cursor = store._conn.execute("SELECT COUNT(*) FROM metrics")
        assert cursor.fetchone()[0] == 1  # Only the recent one
        store.close()

    def test_prune_keeps_recent_entries(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record("stats", {"cpu": 50})
        store.record("stats", {"cpu": 60})
        store.prune()

        cursor = store._conn.execute("SELECT COUNT(*) FROM metrics")
        assert cursor.fetchone()[0] == 2
        store.close()

    def test_prune_without_open_is_noop(self):
        config = _make_config()
        store = MetricStore(config)
        store.prune()  # Should not raise


class TestMetricStoreQuery:
    """Test querying historical data."""

    def test_query_cpu(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record("stats", {"cpu": 42, "mem_used": 2048, "mem_total": 8192, "temp": 55})
        results = store.query("cpu", 3600)

        assert len(results) == 1
        assert results[0][1] == 42
        store.close()

    def test_query_mem_percentage(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record("stats", {"cpu": 10, "mem_used": 4096, "mem_total": 8192})
        results = store.query("mem", 3600)

        assert len(results) == 1
        assert results[0][1] == pytest.approx(50.0)
        store.close()

    def test_query_temp(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record("stats", {"cpu": 10, "temp": 67})
        results = store.query("temp", 3600)

        assert len(results) == 1
        assert results[0][1] == 67
        store.close()

    def test_query_unknown_metric_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record("stats", {"cpu": 10})
        results = store.query("nonexistent_metric", 3600)

        assert results == []
        store.close()

    def test_query_filters_by_period(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        # Old entry (2h ago)
        old_ts = int(time.time()) - 7200
        store._conn.execute(
            "INSERT INTO metrics (ts, collector, data) VALUES (?, ?, ?)",
            (old_ts, "stats", '{"cpu": 99}'),
        )
        # Recent entry
        store.record("stats", {"cpu": 42})
        store._conn.commit()

        # Query only last hour
        results = store.query("cpu", 3600)
        assert len(results) == 1
        assert results[0][1] == 42
        store.close()

    def test_query_without_open_returns_empty(self):
        config = _make_config()
        store = MetricStore(config)
        assert store.query("cpu", 3600) == []

    def test_query_only_reads_stats_collector(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        # Record under different collector names
        store.record("stats", {"cpu": 42})
        store.record("docker", {"cpu": 99})

        results = store.query("cpu", 3600)
        assert len(results) == 1
        assert results[0][1] == 42
        store.close()


class TestContainerStates:
    """Test container state recording and querying."""

    def test_record_and_query_round_trip(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        states = [
            {"name": "grafana", "status": "running", "restart_count": 0},
            {"name": "redis", "status": "exited", "restart_count": 2},
        ]
        store.record_container_states(states)

        grafana = store.query_container_history("grafana", 3600)
        assert len(grafana) == 1
        _, status, rc = grafana[0]
        assert status == "running"
        assert rc == 0

        redis = store.query_container_history("redis", 3600)
        assert len(redis) == 1
        _, status, rc = redis[0]
        assert status == "exited"
        assert rc == 2
        store.close()

    def test_query_filters_by_name(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record_container_states([
            {"name": "alpha", "status": "running", "restart_count": 0},
            {"name": "beta", "status": "running", "restart_count": 1},
        ])

        alpha = store.query_container_history("alpha", 3600)
        assert len(alpha) == 1
        beta = store.query_container_history("beta", 3600)
        assert len(beta) == 1
        gamma = store.query_container_history("gamma", 3600)
        assert gamma == []
        store.close()

    def test_query_filters_by_period(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        old_ts = int(time.time()) - 7200
        store._conn.execute(
            "INSERT INTO container_states (ts, name, status, restart_count) VALUES (?, ?, ?, ?)",
            (old_ts, "myapp", "running", 0),
        )
        store._conn.commit()
        store.record_container_states([{"name": "myapp", "status": "running", "restart_count": 0}])

        results = store.query_container_history("myapp", 3600)
        assert len(results) == 1  # only the recent one
        store.close()

    def test_prune_removes_old_container_states(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        old_ts = int(time.time()) - RETENTION_SECONDS - 3600
        store._conn.execute(
            "INSERT INTO container_states (ts, name, status, restart_count) VALUES (?, ?, ?, ?)",
            (old_ts, "myapp", "running", 0),
        )
        store._conn.commit()
        store.record_container_states([{"name": "myapp", "status": "running", "restart_count": 0}])

        store.prune()

        cursor = store._conn.execute("SELECT COUNT(*) FROM container_states")
        assert cursor.fetchone()[0] == 1  # only the recent one
        store.close()

    def test_record_empty_list_is_noop(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        store.record_container_states([])

        cursor = store._conn.execute("SELECT COUNT(*) FROM container_states")
        assert cursor.fetchone()[0] == 0
        store.close()

    def test_record_without_open_is_noop(self):
        config = _make_config()
        store = MetricStore(config)
        store.record_container_states([{"name": "x", "status": "running", "restart_count": 0}])

    def test_query_without_open_returns_empty(self):
        config = _make_config()
        store = MetricStore(config)
        assert store.query_container_history("x", 3600) == []

    def test_results_ordered_ascending(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        config = _make_config()
        store = MetricStore(config)
        store.open()

        now = int(time.time())
        for offset, status in [(300, "running"), (200, "exited"), (100, "running")]:
            store._conn.execute(
                "INSERT INTO container_states (ts, name, status, restart_count) VALUES (?, ?, ?, ?)",
                (now - offset, "app", status, 0),
            )
        store._conn.commit()

        results = store.query_container_history("app", 3600)
        assert len(results) == 3
        timestamps = [r[0] for r in results]
        assert timestamps == sorted(timestamps)
        store.close()
