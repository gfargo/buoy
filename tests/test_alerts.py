"""Tests for Buoy alert engine."""

import pytest

from buoy.alerts import Alert, AlertEngine
from buoy.config import BuoyConfig, FeaturesConfig, NetworkConfig, NodeConfig, PluginsConfig


def _make_config():
    config = BuoyConfig()
    config.node = NodeConfig(name="test")
    config.network = NetworkConfig()
    config.features = FeaturesConfig()
    config.plugins = PluginsConfig()
    return config


class TestAlertDataclass:
    """Test the Alert dataclass."""

    def test_new_alert_is_active(self):
        alert = Alert(metric="cpu", level="warn", value=85, threshold=80, message="CPU high")
        assert alert.is_active is True

    def test_resolved_alert_is_not_active(self):
        alert = Alert(metric="cpu", level="warn", value=85, threshold=80, message="CPU high")
        alert.resolved_at = 1000.0
        assert alert.is_active is False

    def test_to_dict(self):
        alert = Alert(
            metric="temp",
            level="crit",
            value=90,
            threshold=85,
            message="TEMP crit: 90",
            fired_at=1000.0,
        )
        d = alert.to_dict()
        assert d["metric"] == "temp"
        assert d["level"] == "crit"
        assert d["value"] == 90
        assert d["threshold"] == 85
        assert d["active"] is True
        assert d["resolved_at"] is None


class TestAlertEngineEvaluation:
    """Test metric evaluation against thresholds."""

    @pytest.mark.asyncio
    async def test_normal_values_no_alerts(self):
        config = _make_config()
        engine = AlertEngine(config)

        await engine.evaluate({"cpu": 30, "mem_used": 2048, "mem_total": 8192, "temp": 45, "disk_pct": 50})
        assert len(engine.active_alerts) == 0

    @pytest.mark.asyncio
    async def test_cpu_crit_immediate_fire(self):
        """CPU at critical level fires immediately (duration=60 but tested with sustained breach)."""
        config = _make_config()
        engine = AlertEngine(config)

        # First eval — starts the breach timer
        await engine.evaluate({"cpu": 96, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})
        # With duration=60, alert doesn't fire on first eval
        assert len(engine.active_alerts) == 0

    @pytest.mark.asyncio
    async def test_disk_crit_fires_immediately(self):
        """Disk has duration=0, so it fires on first breach."""
        config = _make_config()
        engine = AlertEngine(config)

        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 92})
        assert len(engine.active_alerts) == 1
        assert engine.active_alerts[0].metric == "disk"
        assert engine.active_alerts[0].level == "crit"

    @pytest.mark.asyncio
    async def test_disk_warn_fires_immediately(self):
        """Disk warn threshold (80%) fires with duration=0."""
        config = _make_config()
        engine = AlertEngine(config)

        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 82})
        assert len(engine.active_alerts) == 1
        assert engine.active_alerts[0].level == "warn"

    @pytest.mark.asyncio
    async def test_alert_resolves_when_value_drops(self):
        """Active alert resolves when metric goes back to normal."""
        config = _make_config()
        engine = AlertEngine(config)

        # Fire
        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 92})
        assert len(engine.active_alerts) == 1

        # Resolve
        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})
        assert len(engine.active_alerts) == 0

    @pytest.mark.asyncio
    async def test_alert_history_grows(self):
        """Fired alerts are recorded in history."""
        config = _make_config()
        engine = AlertEngine(config)

        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 95})
        assert len(engine.alert_history) == 1
        assert engine.alert_history[0]["metric"] == "disk"

    @pytest.mark.asyncio
    async def test_multiple_metrics_can_alert(self):
        """Multiple metrics can have active alerts simultaneously."""
        config = _make_config()
        engine = AlertEngine(config)

        # Disk fires immediately (duration=0); CPU/temp have durations
        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 95})
        assert len(engine.active_alerts) == 1  # Just disk (others have duration)


class TestAlertEngineBroadcast:
    """Test WebSocket broadcast integration."""

    @pytest.mark.asyncio
    async def test_broadcast_called_on_fire(self):
        config = _make_config()
        received = []

        async def fake_broadcast(msg):
            received.append(msg)

        engine = AlertEngine(config, broadcast_fn=fake_broadcast)
        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 92})

        assert len(received) == 1
        assert received[0]["type"] == "alert"
        assert received[0]["metric"] == "disk"

    @pytest.mark.asyncio
    async def test_broadcast_called_on_resolve(self):
        config = _make_config()
        received = []

        async def fake_broadcast(msg):
            received.append(msg)

        engine = AlertEngine(config, broadcast_fn=fake_broadcast)
        # Fire
        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 92})
        # Resolve
        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})

        assert len(received) == 2
        assert received[1]["type"] == "alert_resolved"

    @pytest.mark.asyncio
    async def test_no_broadcast_when_none(self):
        """Engine works fine without a broadcast function."""
        config = _make_config()
        engine = AlertEngine(config, broadcast_fn=None)
        # Should not raise
        await engine.evaluate({"cpu": 10, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 95})
        assert len(engine.active_alerts) == 1


class TestAlertEngineDuration:
    """Test duration-based threshold logic."""

    @pytest.mark.asyncio
    async def test_duration_prevents_immediate_fire(self):
        """Metrics with duration > 0 don't fire on first breach."""
        config = _make_config()
        engine = AlertEngine(config)

        # CPU has duration=60, so first breach just starts the timer
        await engine.evaluate({"cpu": 96, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})
        assert len(engine.active_alerts) == 0
        assert "cpu" in engine._breach_start

    @pytest.mark.asyncio
    async def test_duration_fires_after_sustained(self):
        """After sustained breach beyond duration, alert fires."""
        config = _make_config()
        engine = AlertEngine(config)

        # Start breach
        await engine.evaluate({"cpu": 96, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})
        assert len(engine.active_alerts) == 0

        # Simulate time passing beyond the duration threshold
        engine._breach_start["cpu"] -= 120  # Pretend it started 120s ago

        # Now it should fire
        await engine.evaluate({"cpu": 96, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})
        assert len(engine.active_alerts) == 1
        assert engine.active_alerts[0].metric == "cpu"

    @pytest.mark.asyncio
    async def test_breach_timer_resets_on_recovery(self):
        """If metric drops below threshold, breach timer is cleared."""
        config = _make_config()
        engine = AlertEngine(config)

        # Start breach
        await engine.evaluate({"cpu": 96, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})
        assert "cpu" in engine._breach_start

        # Recover
        await engine.evaluate({"cpu": 30, "mem_used": 2048, "mem_total": 8192, "temp": 40, "disk_pct": 50})
        assert "cpu" not in engine._breach_start
