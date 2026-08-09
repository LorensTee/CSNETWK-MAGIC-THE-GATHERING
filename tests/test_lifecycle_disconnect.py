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
