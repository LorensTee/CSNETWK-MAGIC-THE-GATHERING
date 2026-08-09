"""
tests/test_priority_loop.py — RFC §8.1.3: the player who casts a spell or
activates an ability retains priority (the next PRIORITY_GRANT goes to them,
not automatically to the Active Player).
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from server.game_lifecycle import GameLifecycle
from server.game_state import GameState


def _make_gs() -> GameState:
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.active_player = "p1"
    gs.life_totals = {"p1": 20, "p2": 20}
    gs.hands = {"p1": [], "p2": []}
    gs.libraries = {"p1": ["mountain_001"] * 10, "p2": ["mountain_001"] * 10}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    gs.mana_pools = {}
    return gs


def _make_lifecycle(gs: GameState, windows: list[tuple]) -> GameLifecycle:
    lc = GameLifecycle.__new__(GameLifecycle)
    lc.gs = gs
    lc._game_over = asyncio.Event()
    lc.card_loader = SimpleNamespace(
        get_card=lambda cid: SimpleNamespace(
            card_type="Instant", toughness=1, power=0,
        ),
    )
    lc.stack_mgr = SimpleNamespace(is_empty=lambda gs: True)
    lc.window_calls: list[tuple[str, str]] = []
    lc.processed: list[Any] = []

    async def run_window(c1, c2, ap, nap, read_pdu=None, on_ap_pass_cb=None):
        lc.window_calls.append((ap, nap))
        return windows.pop(0)

    async def fake_process_action(gs, ap, nap, action):
        lc.processed.append((ap, nap, action))

    async def noop(*args, **kwargs):
        return None

    lc.priority_mgr = SimpleNamespace(run_priority_window=run_window)
    lc._process_action = fake_process_action
    lc._check_game_over = lambda gs: False
    lc._broadcast_game_state = noop
    lc._connection_for = lambda pid: SimpleNamespace(
        player_id=pid, seq_num=1, verbose=False,
    )
    lc.wait_for_pdu = noop
    return lc


class TestCasterRetainsPriority:

    def test_next_grant_goes_to_caster_after_cast(self):
        gs = _make_gs()
        windows = [
            (False, {"type": "CAST_SPELL", "player_id": "p2"}, "p2"),
            (True, None, None),
        ]
        lc = _make_lifecycle(gs, windows)

        asyncio.run(lc._run_priority_loop(gs, "p1", "p2"))

        # Window 1: AP first (normal order). Window 2: the CASTER (p2) first.
        assert lc.window_calls == [("p1", "p2"), ("p2", "p1")]
        # The action was processed once.
        assert len(lc.processed) == 1
        assert lc.processed[0][2]["type"] == "CAST_SPELL"

    def test_ap_caster_still_opens_next_window(self):
        gs = _make_gs()
        windows = [
            (False, {"type": "PLAY_LAND", "player_id": "p1"}, "p1"),
            (True, None, None),
        ]
        lc = _make_lifecycle(gs, windows)

        asyncio.run(lc._run_priority_loop(gs, "p1", "p2"))

        assert lc.window_calls == [("p1", "p2"), ("p1", "p2")]
