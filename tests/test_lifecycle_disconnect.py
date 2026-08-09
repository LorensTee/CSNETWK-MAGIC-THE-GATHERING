"""
tests/test_lifecycle_disconnect.py — Robustness: a disconnect (or the
watchdog's GAME_OVER) must unblock every lifecycle wait promptly.

Covers: LOBBY polling, MULLIGAN event gather, priority-wait futures, and
per-game state reset (mulligan seq numbers) between games.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from server.game_lifecycle import GameLifecycle, GameOverInterrupt
from server.game_state import GameState


def _make_lifecycle() -> GameLifecycle:
    lc = GameLifecycle.__new__(GameLifecycle)
    lc.gs = GameState()
    lc._game_over = asyncio.Event()
    lc._pending_pdu: dict[str, asyncio.Future] = {}
    lc._deck_lists = {}
    lc._player_index = {}
    lc._mulligan_kept = {}
    lc._mulligan_expected_seq = {}
    return lc


class TestLobbyDisconnect:

    def test_lobby_wait_ends_when_game_over_set(self):
        lc = _make_lifecycle()
        lc.gs.players_ready = 0
        lc.gs.player_ids = []

        async def scenario():
            async def set_over():
                await asyncio.sleep(0.05)
                lc._game_over.set()

            t = asyncio.create_task(set_over())
            # Must return promptly (would hang forever before the fix).
            await asyncio.wait_for(lc._run_lobby(lc.gs, []), timeout=2)
            t.cancel()
            assert lc._game_over.is_set()

        asyncio.run(scenario())


class TestMulliganDisconnect:

    def test_mulligan_wait_ends_when_game_over_set(self):
        lc = _make_lifecycle()
        lc.gs.player_ids = ["p1", "p2"]
        lc._mulligan_kept = {"p1": asyncio.Event(), "p2": asyncio.Event()}

        async def scenario():
            async def set_over():
                await asyncio.sleep(0.05)
                lc._game_over.set()

            t = asyncio.create_task(set_over())
            await asyncio.wait_for(lc._run_mulligan(lc.gs, []), timeout=2)
            t.cancel()
            assert lc._game_over.is_set()

        asyncio.run(scenario())


class TestPriorityWaitUnblocked:

    def test_wait_for_pdu_raises_promptly_on_game_over(self):
        import time as _time

        lc = _make_lifecycle()

        async def scenario():
            async def set_over():
                await asyncio.sleep(0.05)
                lc._game_over.set()

            t = asyncio.create_task(set_over())
            start = _time.monotonic()
            # Game over must surface as GameOverInterrupt (not
            # TimeoutError): the engine unwinds gracefully without
            # re-broadcasting GAME_OVER as DISCONNECT.
            with pytest.raises(GameOverInterrupt):
                await lc.wait_for_pdu("p1", 30)  # inner timeout far away
            elapsed = _time.monotonic() - start
            t.cancel()
            # Must unblock promptly (game over), not after the 30 s timeout.
            assert elapsed < 1.0, f"unblocked after {elapsed:.2f}s"
            # Pending future cleaned up.
            assert "p1" not in lc._pending_pdu

        asyncio.run(scenario())


class TestGameOverReset:

    def test_ready_state_cleared_by_end_game(self):
        """The ready-state reset lives in _end_game (it must run before
        the next game's PLAYER_READYs are counted, even when they arrive
        while the previous game's engine is still unwinding)."""
        lc = _make_lifecycle()
        lc.connections = []  # _end_game broadcasts over connections
        lc._mulligan_expected_seq = {"p1": 7, "p2": 9}
        lc._deck_lists = {"p1": ["x"], "p2": ["y"]}
        lc._mulligan_kept = {"p1": asyncio.Event()}
        lc._player_index = {"p1": 0}
        lc.stack_mgr = SimpleNamespace(clear_cache=lambda: None)
        lc.combat_mgr = SimpleNamespace(reset=lambda: None)

        async def scenario():
            await lc._end_game(lc.gs, "CONCEDE", "p2", "p1")
            assert lc._mulligan_expected_seq == {}
            assert lc._deck_lists == {}
            assert lc._player_index == {}
            assert lc.gs.players_ready == 0
            assert lc._game_over.is_set()

        asyncio.run(scenario())

    def test_zones_cleared_by_run_game_over(self):
        lc = _make_lifecycle()
        lc.gs.hands = {"p1": ["x"]}
        lc.gs.libraries = {"p1": ["y"]}
        lc.gs.graveyards = {"p1": ["z"]}
        lc.gs.battlefield = {"p1": []}
        lc.stack_mgr = SimpleNamespace(clear_cache=lambda: None)
        lc.combat_mgr = SimpleNamespace(reset=lambda: None)

        asyncio.run(lc._run_game_over(lc.gs, []))

        assert lc.gs.phase == "LOBBY"
        assert lc.gs.hands == {}
        assert lc.gs.libraries == {}
        assert lc.gs.graveyards == {}


class TestEndGameRobustness:
    """Review findings: _end_game must dedupe concurrent calls, set
    _game_over even when a broadcast send fails, and never leave the
    mulligan bookkeeping in a stale state."""

    def test_end_game_dedupes_concurrent_calls(self):
        lc = _make_lifecycle()
        lc.connections = []
        calls = []

        async def fake_broadcast(pdu):
            calls.append(pdu)
            await asyncio.sleep(0.01)  # widen the race window

        lc.broadcast = fake_broadcast

        async def scenario():
            # Two game-over sources firing concurrently must yield exactly
            # one GAME_OVER broadcast (atomic claim via _game_over.set()).
            await asyncio.gather(
                lc._end_game(lc.gs, "CONCEDE", "p2", "p1"),
                lc._end_game(lc.gs, "DISCONNECT", "p1", "p2"),
            )
            assert lc._game_over.is_set()
            assert len(calls) == 1, f"expected 1 broadcast, got {len(calls)}"

        asyncio.run(scenario())

    def test_end_game_sets_game_over_even_if_broadcast_raises(self):
        lc = _make_lifecycle()
        lc.connections = []

        async def failing_broadcast(pdu):
            raise ConnectionError("socket died mid-send")

        lc.broadcast = failing_broadcast

        async def scenario():
            with pytest.raises(ConnectionError):
                await lc._end_game(lc.gs, "CONCEDE", "p2", "p1")
            # The game-over flag and ready-state reset still ran.
            assert lc._game_over.is_set()
            assert lc.gs.players_ready == 0

        asyncio.run(scenario())

    def test_keep_after_game_over_does_not_touch_reset_dicts(self):
        """A MULLIGAN_CHOICE dispatched after _end_game's reset must not
        KeyError on the cleared _mulligan_kept nor re-seed
        _mulligan_expected_seq (which would STALE-reject the next game)."""
        lc = _make_lifecycle()
        lc.connections = []
        lc.gs.phase = "MULLIGAN"
        lc._game_over.set()  # Game already ended.
        lc._mulligan_kept = {}
        lc._mulligan_expected_seq = {}
        lc.gs.player_ids = ["p1", "p2"]
        lc.gs.life_totals = {"p1": 20, "p2": 20}

        async def scenario():
            conn = SimpleNamespace(player_id="p1", seq_num=5)
            await lc.handle_mulligan_choice(
                conn,
                {"type": "MULLIGAN_CHOICE", "seq_num": 3,
                 "player_id": "p1", "keep": True, "cards_to_bottom": []},
            )
            assert lc._mulligan_expected_seq == {}
            assert lc._mulligan_kept == {}

        asyncio.run(scenario())
