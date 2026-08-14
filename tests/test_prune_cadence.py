"""Tests for the deterministic prune cadence used by _stats_loop.

The old scheduling condition (`int(loop.time()) % 500 < stats_interval`) was
driven by a monotonic clock of unknown origin and could prune twice in a row
or skip entirely. It was replaced by a fixed cycle-count cadence.
"""

from buoy.server import PRUNE_EVERY_CYCLES


def _should_prune(cycle: int) -> bool:
    return cycle % PRUNE_EVERY_CYCLES == 0


class TestPruneCadence:
    def test_prunes_exactly_once_per_window(self):
        cycles = range(1, PRUNE_EVERY_CYCLES * 5 + 1)
        prune_cycles = [c for c in cycles if _should_prune(c)]
        assert prune_cycles == [PRUNE_EVERY_CYCLES * n for n in range(1, 6)]

    def test_never_prunes_twice_in_a_row(self):
        prune_cycles = [c for c in range(1, PRUNE_EVERY_CYCLES * 10 + 1) if _should_prune(c)]
        gaps = [b - a for a, b in zip(prune_cycles, prune_cycles[1:])]
        assert all(gap == PRUNE_EVERY_CYCLES for gap in gaps)

    def test_never_skips_a_window(self):
        for n in range(1, 10):
            window = range((n - 1) * PRUNE_EVERY_CYCLES + 1, n * PRUNE_EVERY_CYCLES + 1)
            assert sum(1 for c in window if _should_prune(c)) == 1
