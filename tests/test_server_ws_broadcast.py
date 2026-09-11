"""Regression tests for app-bound WebSocket broadcasts."""

import asyncio
import json

import pytest

from buoy.alerts import AlertEngine
from buoy.config import BuoyConfig, FeaturesConfig, NodeConfig, RefreshConfig
from buoy.server import BuoyAppState, _stats_loop, broadcast_alert, broadcast_stats


class _MutatingClient:
    """Discard another client from this app's set while a broadcast is iterating."""

    def __init__(self, state, other):
        self.state = state
        self.other = other
        self.sent = []

    async def send_text(self, message):
        self.sent.append(message)
        self.state.ws_clients.discard(self.other)


class _PlainClient:
    def __init__(self):
        self.sent = []

    async def send_text(self, message):
        self.sent.append(message)


class TestBroadcastStats:
    @pytest.mark.asyncio
    async def test_survives_concurrent_discard_during_iteration(self):
        state = BuoyAppState(config=BuoyConfig())
        victim = _PlainClient()
        mutator = _MutatingClient(state, other=victim)
        state.ws_clients.update({mutator, victim})

        await broadcast_stats(state, {"cpu": 1})

    @pytest.mark.asyncio
    async def test_only_sends_to_clients_owned_by_given_app(self):
        state_a = BuoyAppState(config=BuoyConfig())
        state_b = BuoyAppState(config=BuoyConfig())
        client_a = _PlainClient()
        client_b = _PlainClient()
        state_a.ws_clients.add(client_a)
        state_b.ws_clients.add(client_b)

        await broadcast_stats(state_a, {"cpu": 17})

        assert len(client_a.sent) == 1
        assert '"cpu": 17' in client_a.sent[0]
        assert client_b.sent == []
        assert state_b.ws_clients == {client_b}


class TestBroadcastAlert:
    @pytest.mark.asyncio
    async def test_survives_concurrent_discard_during_iteration(self):
        state = BuoyAppState(config=BuoyConfig())
        victim = _PlainClient()
        mutator = _MutatingClient(state, other=victim)
        state.ws_clients.update({mutator, victim})

        await broadcast_alert(state, {"type": "alert", "message": "disk full"})

    @pytest.mark.asyncio
    async def test_only_sends_to_clients_owned_by_given_app(self):
        state_a = BuoyAppState(config=BuoyConfig())
        state_b = BuoyAppState(config=BuoyConfig())
        client_a = _PlainClient()
        client_b = _PlainClient()
        state_a.ws_clients.add(client_a)
        state_b.ws_clients.add(client_b)

        await broadcast_alert(state_a, {"type": "alert", "message": "disk full"})

        assert len(client_a.sent) == 1
        assert '"message": "disk full"' in client_a.sent[0]
        assert client_b.sent == []
        assert state_b.ws_clients == {client_b}


class TestStatsLoopBroadcastsAlerts:
    """BUG-12: /api/stats already included an `alerts` array, but the
    WebSocket broadcast in _stats_loop did not — a client relying on the
    WS push (the common case once connected) never saw active-alerts state
    at all outside of the transient toast fired the moment an alert changes.
    """

    def _make_config(self):
        config = BuoyConfig()
        config.node = NodeConfig(name="test")
        config.features = FeaturesConfig(websocket=True)
        config.refresh = RefreshConfig(stats_interval=0)
        return config

    @pytest.mark.asyncio
    async def test_active_alert_included_in_broadcast_payload(self):
        config = self._make_config()
        state = BuoyAppState(config=config)
        state.alert_engine = AlertEngine(config)
        client = _PlainClient()
        state.ws_clients.add(client)

        # disk has duration=0, fires immediately on first breach
        await state.alert_engine.evaluate(
            {"cpu": 10, "mem_used": 0, "mem_total": 1, "temp": 40, "disk_pct": 95}
        )

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_stats_loop(state), timeout=0.2)

        assert client.sent
        # The broadcast for a given iteration reflects alert state as of
        # *before* that iteration's own evaluate() call — check the first
        # broadcast, since with stats_interval=0 the loop runs many
        # iterations within the timeout window, and each one evaluates
        # against the collector-less "empty" stats fallback (no active
        # breach), which resolves this manually-fired alert on its very
        # first pass.
        payload = json.loads(client.sent[0])
        assert payload["type"] == "stats"
        alerts = payload["data"]["alerts"]
        assert len(alerts) == 1
        assert alerts[0]["metric"] == "disk"
        assert alerts[0]["level"] == "crit"

    @pytest.mark.asyncio
    async def test_no_active_alerts_broadcasts_empty_list(self):
        config = self._make_config()
        state = BuoyAppState(config=config)
        state.alert_engine = AlertEngine(config)
        client = _PlainClient()
        state.ws_clients.add(client)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_stats_loop(state), timeout=0.2)

        assert client.sent
        payload = json.loads(client.sent[-1])
        assert payload["data"]["alerts"] == []

    @pytest.mark.asyncio
    async def test_broadcasts_empty_list_without_an_alert_engine(self):
        """Guard: no alert engine configured must not crash the loop."""
        config = self._make_config()
        state = BuoyAppState(config=config)
        client = _PlainClient()
        state.ws_clients.add(client)

        with pytest.raises(TimeoutError):
            await asyncio.wait_for(_stats_loop(state), timeout=0.2)

        assert client.sent
        payload = json.loads(client.sent[-1])
        assert payload["data"]["alerts"] == []
