"""Regression tests for app-bound WebSocket broadcasts."""

import pytest

from buoy.config import BuoyConfig
from buoy.server import BuoyAppState, broadcast_alert, broadcast_stats


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
