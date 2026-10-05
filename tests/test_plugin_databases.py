"""Tests for the Databases plugin."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from buoy.plugins.builtin.databases import (
    DatabasesPlugin,
    _mysql_metrics,
    _parse_redis_info,
    _pg_metrics,
    _rate,
    _redis_metrics,
    _status_for,
    _worst,
)


def _make_plugin(**settings):
    plugin = DatabasesPlugin()
    plugin.configure(settings)
    return plugin


class TestRate:
    def test_first_sample_returns_none(self):
        assert _rate(None, None, 10.0, 100.0) is None

    def test_normal_delta(self):
        # 60 units over 60s elapsed -> 60/min
        assert _rate(100.0, 0.0, 160.0, 60.0) == pytest.approx(60.0)

    def test_counter_reset_returns_none(self):
        assert _rate(500.0, 0.0, 10.0, 60.0) is None

    def test_zero_elapsed_returns_none(self):
        assert _rate(100.0, 60.0, 110.0, 60.0) is None


class TestPgMetrics:
    def test_primary_not_in_recovery(self):
        row = {
            "total_connections": 20,
            "active_connections": 5,
            "max_connections": 100,
            "in_recovery": False,
            "caught_up": False,
            "replay_lag_s": None,
            "slow_queries": 1,
        }
        m = _pg_metrics(row)
        assert m["connections"] == 20
        assert m["connections_pct"] == 20.0
        assert m["is_replica"] is False
        assert m["replication_lag_s"] is None
        assert m["replication_broken"] is False
        assert m["slow_queries"] == 1

    def test_replica_caught_up_reports_zero_lag(self):
        row = {
            "total_connections": 10,
            "active_connections": 1,
            "max_connections": 100,
            "in_recovery": True,
            "caught_up": True,
            "replay_lag_s": 999.0,  # stale timestamp value; caught_up wins
            "slow_queries": 0,
        }
        m = _pg_metrics(row)
        assert m["is_replica"] is True
        assert m["replication_lag_s"] == 0.0
        assert m["replication_broken"] is False

    def test_replica_with_lag(self):
        row = {
            "total_connections": 10,
            "active_connections": 1,
            "max_connections": 100,
            "in_recovery": True,
            "caught_up": False,
            "replay_lag_s": 42.5,
            "slow_queries": 0,
        }
        m = _pg_metrics(row)
        assert m["replication_lag_s"] == 42.5
        assert m["replication_broken"] is False

    def test_replica_missing_lag_is_broken(self):
        row = {
            "total_connections": 10,
            "active_connections": 1,
            "max_connections": 100,
            "in_recovery": True,
            "caught_up": False,
            "replay_lag_s": None,
            "slow_queries": 0,
        }
        m = _pg_metrics(row)
        assert m["replication_broken"] is True

    def test_zero_max_connections_avoids_division_error(self):
        row = {
            "total_connections": 0,
            "active_connections": 0,
            "max_connections": 0,
            "in_recovery": False,
            "caught_up": False,
            "replay_lag_s": None,
            "slow_queries": 0,
        }
        m = _pg_metrics(row)
        assert m["connections_pct"] == 0.0


class TestMysqlMetrics:
    def test_primary_no_replica_row(self):
        status = {"Threads_connected": "12", "Slow_queries": "5"}
        m = _mysql_metrics(status, 150, None, None, None, 100.0)
        assert m["connections"] == 12
        assert m["connections_pct"] == pytest.approx(8.0)
        assert m["is_replica"] is False
        assert m["replication_lag_s"] is None
        assert m["replication_broken"] is False
        assert m["slow_queries_per_min"] is None

    def test_replica_with_lag(self):
        status = {"Threads_connected": "5", "Slow_queries": "10"}
        replica_row = {"Seconds_Behind_Source": "30"}
        m = _mysql_metrics(status, 100, replica_row, 5.0, 40.0, 100.0)
        assert m["is_replica"] is True
        assert m["replication_lag_s"] == 30.0
        assert m["replication_broken"] is False
        assert m["slow_queries_per_min"] == pytest.approx((10.0 - 5.0) * 60 / 60)

    def test_replica_null_lag_is_broken(self):
        status = {"Threads_connected": "5", "Slow_queries": "0"}
        replica_row = {"Seconds_Behind_Source": None}
        m = _mysql_metrics(status, 100, replica_row, None, None, 100.0)
        assert m["is_replica"] is True
        assert m["replication_broken"] is True

    def test_mariadb_field_name_fallback(self):
        status = {"Threads_connected": "5", "Slow_queries": "0"}
        replica_row = {"Seconds_Behind_Master": "12"}
        m = _mysql_metrics(status, 100, replica_row, None, None, 100.0)
        assert m["replication_lag_s"] == 12.0


class TestParseRedisInfo:
    def test_parses_key_value_lines_and_skips_sections(self):
        text = "# Clients\r\nconnected_clients:10\r\n# Memory\r\nused_memory:1024\r\n"
        info = _parse_redis_info(text)
        assert info == {"connected_clients": "10", "used_memory": "1024"}

    def test_ignores_blank_and_malformed_lines(self):
        text = "connected_clients:5\n\nnotakeyvalue\nused_memory:100\n"
        info = _parse_redis_info(text)
        assert info == {"connected_clients": "5", "used_memory": "100"}


class TestRedisMetrics:
    def test_master_no_maxmemory(self):
        info = {
            "connected_clients": "10",
            "used_memory": "5000",
            "maxmemory": "0",
            "evicted_keys": "0",
            "role": "master",
        }
        m = _redis_metrics(info, None, None, 100.0)
        assert m["memory_pct"] is None
        assert m["is_replica"] is False
        assert m["evicted_keys_per_min"] is None

    def test_replica_link_down(self):
        info = {
            "connected_clients": "3",
            "used_memory": "500",
            "maxmemory": "1000",
            "evicted_keys": "20",
            "role": "slave",
            "master_link_status": "down",
        }
        m = _redis_metrics(info, 10.0, 40.0, 100.0)
        assert m["is_replica"] is True
        assert m["replica_link_status"] == "down"
        assert m["replication_broken"] is True
        assert m["memory_pct"] == 50.0
        assert m["evicted_keys_per_min"] == pytest.approx((20.0 - 10.0) * 60 / 60)

    def test_replica_link_up_not_broken(self):
        info = {
            "connected_clients": "3",
            "used_memory": "500",
            "maxmemory": "1000",
            "evicted_keys": "0",
            "role": "slave",
            "master_link_status": "up",
        }
        m = _redis_metrics(info, None, None, 100.0)
        assert m["replication_broken"] is False


class TestProbeRedis:
    @pytest.mark.asyncio
    async def test_probe_consumes_already_parsed_info_dict(self):
        # redis-py's INFO response callback returns a parsed dict, not raw
        # text/bytes — the probe must call client.info() directly rather
        # than treat the response as something to decode/parse itself.
        plugin = _make_plugin(targets=[{"name": "cache", "engine": "redis", "host": "h1"}])
        fake_client = AsyncMock()
        fake_client.info = AsyncMock(
            return_value={
                "connected_clients": 7,
                "used_memory": 2048,
                "maxmemory": 4096,
                "evicted_keys": 3,
                "role": "master",
            }
        )
        fake_redis_module = MagicMock()
        fake_redis_module.Redis = MagicMock(return_value=fake_client)

        with patch(
            "buoy.plugins.builtin.databases._load_redis",
            return_value=fake_redis_module,
        ):
            metrics = await plugin._probe_redis(
                {"name": "cache", "engine": "redis", "host": "h1"}, 5.0
            )

        assert metrics["connected_clients"] == 7
        assert metrics["memory_pct"] == 50.0
        fake_client.aclose.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_configured_timeout_reaches_driver_call(self):
        plugin = _make_plugin(targets=[{"name": "cache", "engine": "redis", "host": "h1"}])
        fake_client = AsyncMock()
        fake_client.info = AsyncMock(return_value={"role": "master"})
        fake_redis_module = MagicMock()
        fake_redis_module.Redis = MagicMock(return_value=fake_client)

        with patch(
            "buoy.plugins.builtin.databases._load_redis",
            return_value=fake_redis_module,
        ):
            await plugin._probe_redis({"name": "cache", "engine": "redis", "host": "h1"}, 17.0)

        _, kwargs = fake_redis_module.Redis.call_args
        assert kwargs["socket_timeout"] == 17.0
        assert kwargs["socket_connect_timeout"] == 17.0


class TestWorst:
    def test_warn_outranks_unavailable(self):
        assert _worst(["unavailable", "warn"]) == "warn"

    def test_error_outranks_everything(self):
        assert _worst(["unavailable", "warn", "error"]) == "error"

    def test_unavailable_alone(self):
        assert _worst(["ok", "unavailable"]) == "unavailable"


class TestCounterKey:
    def test_same_name_different_engine_does_not_collide(self):
        mysql_key = DatabasesPlugin._counter_key({"engine": "mysql", "name": "db1"})
        redis_key = DatabasesPlugin._counter_key({"engine": "redis", "name": "db1"})
        assert mysql_key != redis_key

    def test_unnamed_targets_distinguished_by_host_and_port(self):
        a = DatabasesPlugin._counter_key({"engine": "redis", "host": "h1", "port": 6379})
        b = DatabasesPlugin._counter_key({"engine": "redis", "host": "h2", "port": 6379})
        assert a != b


class TestStatusFor:
    def test_ok_when_under_thresholds(self):
        m = {"connections_pct": 10, "replication_lag_s": None, "memory_pct": None}
        assert _status_for(m, 80, 95, 30, 300) == "ok"

    def test_warn_on_connections(self):
        m = {"connections_pct": 85, "replication_lag_s": None, "memory_pct": None}
        assert _status_for(m, 80, 95, 30, 300) == "warn"

    def test_error_on_connections(self):
        m = {"connections_pct": 96, "replication_lag_s": None, "memory_pct": None}
        assert _status_for(m, 80, 95, 30, 300) == "error"

    def test_warn_on_lag(self):
        m = {"connections_pct": 0, "replication_lag_s": 35, "memory_pct": None}
        assert _status_for(m, 80, 95, 30, 300) == "warn"

    def test_error_on_lag(self):
        m = {"connections_pct": 0, "replication_lag_s": 400, "memory_pct": None}
        assert _status_for(m, 80, 95, 30, 300) == "error"

    def test_broken_replication_is_always_error(self):
        m = {"connections_pct": 0, "replication_lag_s": None, "replication_broken": True}
        assert _status_for(m, 80, 95, 30, 300) == "error"

    def test_warn_on_high_memory(self):
        m = {"connections_pct": 0, "replication_lag_s": None, "memory_pct": 97}
        assert _status_for(m, 80, 95, 30, 300) == "warn"


class TestCollect:
    @pytest.mark.asyncio
    async def test_no_targets_returns_disabled(self):
        plugin = _make_plugin(targets=[])
        result = await plugin.collect()
        assert result.status == "disabled"
        assert "Not configured" in result.summary

    @pytest.mark.asyncio
    async def test_missing_targets_key_returns_disabled(self):
        plugin = _make_plugin()
        result = await plugin.collect()
        assert result.status == "disabled"

    @pytest.mark.asyncio
    async def test_all_ok(self):
        plugin = _make_plugin(
            targets=[{"name": "db1", "engine": "postgres", "host": "h1"}],
        )
        ok_metrics = {
            "connections": 5,
            "max_connections": 100,
            "connections_pct": 5.0,
            "replication_lag_s": None,
            "replication_broken": False,
        }
        with patch.object(plugin, "_probe_postgres", AsyncMock(return_value=ok_metrics)):
            result = await plugin.collect()
        assert result.status == "ok"
        assert "db1" in result.summary
        assert result.detail["databases"][0]["status"] == "ok"

    @pytest.mark.asyncio
    async def test_over_critical_connections_is_error(self):
        plugin = _make_plugin(
            targets=[{"name": "db1", "engine": "postgres", "host": "h1"}],
            connections_critical_pct=95,
        )
        metrics = {
            "connections": 96,
            "max_connections": 100,
            "connections_pct": 96.0,
            "replication_lag_s": None,
            "replication_broken": False,
        }
        with patch.object(plugin, "_probe_postgres", AsyncMock(return_value=metrics)):
            result = await plugin.collect()
        assert result.status == "error"

    @pytest.mark.asyncio
    async def test_replica_lag_over_warn_is_warn(self):
        plugin = _make_plugin(
            targets=[{"name": "replica1", "engine": "mysql", "host": "h1"}],
            replication_lag_warn_s=30,
        )
        metrics = {
            "connections": 5,
            "max_connections": 100,
            "connections_pct": 5.0,
            "replication_lag_s": 60.0,
            "replication_broken": False,
        }
        with patch.object(plugin, "_probe_mysql", AsyncMock(return_value=metrics)):
            result = await plugin.collect()
        assert result.status == "warn"

    @pytest.mark.asyncio
    async def test_one_probe_failure_does_not_affect_others(self):
        plugin = _make_plugin(
            targets=[
                {"name": "good", "engine": "postgres", "host": "h1"},
                {"name": "bad", "engine": "mysql", "host": "h2"},
            ],
        )
        ok_metrics = {
            "connections": 5,
            "max_connections": 100,
            "connections_pct": 5.0,
            "replication_lag_s": None,
            "replication_broken": False,
        }
        with (
            patch.object(plugin, "_probe_postgres", AsyncMock(return_value=ok_metrics)),
            patch.object(plugin, "_probe_mysql", AsyncMock(side_effect=RuntimeError("boom"))),
        ):
            result = await plugin.collect()

        assert result.status == "error"
        rows = {r["name"]: r for r in result.detail["databases"]}
        assert rows["good"]["status"] == "ok"
        assert rows["bad"]["status"] == "error"

    @pytest.mark.asyncio
    async def test_missing_driver_is_unavailable(self):
        plugin = _make_plugin(
            targets=[{"name": "db1", "engine": "postgres", "host": "h1"}],
        )
        with patch(
            "buoy.plugins.builtin.databases._load_asyncpg",
            side_effect=ImportError("no module"),
        ):
            result = await plugin.collect()
        assert result.status == "unavailable"
        assert "buoy[databases]" in result.detail["databases"][0]["error"]

    @pytest.mark.asyncio
    async def test_unknown_engine_errors(self):
        plugin = _make_plugin(targets=[{"name": "db1", "engine": "mongodb", "host": "h1"}])
        result = await plugin.collect()
        assert result.status == "error"
        assert "unknown engine" in result.detail["databases"][0]["error"]

    @pytest.mark.asyncio
    async def test_unavailable_target_does_not_mask_warn_on_another(self):
        plugin = _make_plugin(
            targets=[
                {"name": "warn-db", "engine": "postgres", "host": "h1"},
                {"name": "no-driver-db", "engine": "mysql", "host": "h2"},
            ],
        )
        warn_metrics = {
            "connections": 90,
            "max_connections": 100,
            "connections_pct": 90.0,
            "replication_lag_s": None,
            "replication_broken": False,
        }
        with (
            patch.object(plugin, "_probe_postgres", AsyncMock(return_value=warn_metrics)),
            patch(
                "buoy.plugins.builtin.databases._load_aiomysql",
                side_effect=ImportError("no module"),
            ),
        ):
            result = await plugin.collect()

        assert result.status == "warn"
        assert "2 needs attention" in result.summary
        assert "no-driver-db unavailable" in result.summary

    @pytest.mark.asyncio
    async def test_secret_never_leaks_into_summary_or_detail(self):
        plugin = _make_plugin(
            targets=[
                {
                    "name": "db1",
                    "engine": "postgres",
                    "host": "h1",
                    "password": "hunter2",
                }
            ],
        )
        with patch.object(
            plugin,
            "_probe_postgres",
            AsyncMock(side_effect=RuntimeError("could not connect: password=hunter2")),
        ):
            result = await plugin.collect()

        blob = json.dumps(result.detail) + result.summary
        assert "hunter2" not in blob


class TestRenderAndDemo:
    def test_demo_data_renders(self):
        plugin = DatabasesPlugin()
        data = plugin.demo_data()
        assert data.status == "warn"
        blocks = plugin.render(data)
        assert blocks[0]["type"] == "table"
        assert len(blocks[0]["rows"]) == 3

    def test_render_empty_shows_text(self):
        from buoy.plugins.protocol import PanelData

        plugin = DatabasesPlugin()
        blocks = plugin.render(PanelData(status="disabled", summary="", detail={}))
        assert blocks == [{"type": "text", "value": "No databases configured", "status": "dim"}]

    def test_redis_connected_clients_shown_in_conns_column(self):
        plugin = DatabasesPlugin()
        data = plugin.demo_data()
        redis_row = next(r for r in data.detail["databases"] if r["engine"] == "redis")
        assert redis_row["metrics"]["connected_clients"] == 36

        blocks = plugin.render(data)
        table_row = next(
            row
            for row, r in zip(blocks[0]["rows"], data.detail["databases"], strict=True)
            if r["engine"] == "redis"
        )
        assert table_row[2]["value"] == "36"

    def test_redis_broken_replica_link_renders_as_error(self):
        from buoy.plugins.protocol import PanelData

        plugin = DatabasesPlugin()
        row = {
            "name": "cache-replica",
            "engine": "redis",
            "status": "error",
            "error": "",
            "metrics": {
                "connected_clients": 3,
                "is_replica": True,
                "replica_link_status": "down",
                "replication_broken": True,
            },
        }
        blocks = plugin.render(PanelData(status="error", summary="", detail={"databases": [row]}))
        lag_cell = blocks[0]["rows"][0][3]
        assert lag_cell["value"] == "link down"
        assert lag_cell["status"] == "error"
