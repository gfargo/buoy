"""Regression tests for BUG-20: broadcasting must not mutate _ws_clients while iterating it."""

import pytest

import buoy.server as srv


@pytest.fixture(autouse=True)
def isolate_ws_clients():
    original = set(srv._ws_clients)
    srv._ws_clients.clear()
    yield
    srv._ws_clients.clear()
    srv._ws_clients.update(original)


class _MutatingClient:
    """A fake WebSocket that discards another client from the set mid-broadcast,
    simulating a concurrent ws_endpoint task disconnecting while broadcast_stats/
    broadcast_alert is iterating _ws_clients."""

    def __init__(self, other):
        self.other = other
        self.sent = []

    async def send_text(self, message):
        self.sent.append(message)
        srv._ws_clients.discard(self.other)


class _PlainClient:
    def __init__(self):
        self.sent = []

    async def send_text(self, message):
        self.sent.append(message)


class TestBroadcastStats:
    @pytest.mark.asyncio
    async def test_survives_concurrent_discard_during_iteration(self):
        victim = _PlainClient()
        mutator = _MutatingClient(other=victim)
        srv._ws_clients.update({mutator, victim})

        await srv.broadcast_stats({"cpu": 1})


class TestBroadcastAlert:
    @pytest.mark.asyncio
    async def test_survives_concurrent_discard_during_iteration(self):
        victim = _PlainClient()
        mutator = _MutatingClient(other=victim)
        srv._ws_clients.update({mutator, victim})

        await srv.broadcast_alert({"type": "alert", "message": "disk full"})
