"""
tests/test_game_flow.py — Integration-like tests for the game lifecycle.

Exercises GameLifecycle state transitions, handler methods, and game-over
detection without requiring mock TCP streams (uses direct method calls).

All async setup is wrapped in ``asyncio.run()`` — no pytest-asyncio needed.
"""

import asyncio

from server.card_loader import CardLoader
from server.config import ServerConfig
from server.game_lifecycle import GameLifecycle
from server.game_state import GameState
from server.connection import ServerConnection


def _make_lifecycle():
    """Return a GameLifecycle with stub connections and loaded cards.
    Must be called inside ``asyncio.run()`` or an event loop context.

    Note: conn1 and conn2 share the same reader/writer stubs.  This is
    safe for the current tests (no I/O exercised), but would cause
    cross-talk if a test were added that reads/writes both connections.
    """
    config = ServerConfig(verbose=False)
    loader = CardLoader()
    loader.load()

    reader = asyncio.StreamReader()

    class StubWriter:
        """Minimal writer stub — no real I/O, never awaited for write()."""
        def write(self, data):
            pass  # sync — matches ServerConnection.send_pdu call pattern
        async def drain(self):
            pass
        def close(self):
            pass  # sync
        async def wait_closed(self):
            pass
        def get_extra_info(self, name):
            return None

    writer = StubWriter()

    conn1 = ServerConnection(reader, writer, verbose=False)
    conn2 = ServerConnection(reader, writer, verbose=False)

    lifecycle = GameLifecycle(config, loader, [conn1, conn2])
    return lifecycle, conn1, conn2


class TestGameOverDetection:

    def test_life_zero_detected(self):
        async def run():
            lifecycle, _, _ = _make_lifecycle()
            gs = lifecycle.gs
            gs.player_ids = ["p1", "p2"]
            gs.life_totals = {"p1": -3, "p2": 20}
            assert lifecycle._check_game_over(gs) is True
        asyncio.run(run())

    def test_life_positive_no_game_over(self):
        async def run():
            lifecycle, _, _ = _make_lifecycle()
            gs = lifecycle.gs
            gs.player_ids = ["p1", "p2"]
            gs.life_totals = {"p1": 5, "p2": 20}
            assert lifecycle._check_game_over(gs) is False
        asyncio.run(run())

    def test_deck_empty_detected(self):
        async def run():
            lifecycle, _, _ = _make_lifecycle()
            gs = lifecycle.gs
            gs.player_ids = ["p1", "p2"]
            gs.life_totals = {"p1": 20, "p2": 20}
            gs._draw_failed_for = "p2"
            assert lifecycle._check_game_over(gs) is True
            assert gs._draw_failed_for is None
        asyncio.run(run())


class TestOpponent:

    def test_opponent(self):
        async def run():
            lifecycle, _, _ = _make_lifecycle()
            lifecycle.gs.player_ids = ["p1", "p2"]
            assert lifecycle._opponent("p1") == "p2"
            assert lifecycle._opponent("p2") == "p1"
        asyncio.run(run())

    def test_opponent_none_when_empty(self):
        async def run():
            lifecycle, _, _ = _make_lifecycle()
            assert lifecycle._opponent("p1") is None
        asyncio.run(run())


class TestMulliganSeqnum:

    def test_mulligan_expected_seq_stored(self):
        async def run():
            lifecycle, conn1, _ = _make_lifecycle()
            conn1.seq_num = 5
            lifecycle._mulligan_expected_seq["player_1"] = conn1.seq_num
            assert lifecycle._mulligan_expected_seq.get("player_1") == 5
        asyncio.run(run())

    def test_mulligan_expected_seq_not_set_initially(self):
        async def run():
            lifecycle, _, _ = _make_lifecycle()
            assert lifecycle._mulligan_expected_seq.get("player_1") is None
        asyncio.run(run())


class TestDeckList:

    def test_deck_list_stored(self):
        async def run():
            lifecycle, _, _ = _make_lifecycle()
            lifecycle._deck_lists["p1"] = ["card1", "card2"]
            assert "p1" in lifecycle._deck_lists
            assert len(lifecycle._deck_lists["p1"]) == 2
        asyncio.run(run())
