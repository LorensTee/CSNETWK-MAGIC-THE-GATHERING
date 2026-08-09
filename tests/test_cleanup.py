"""
tests/test_cleanup.py — RFC §7.8 cleanup discard loop (Module 02).

The cleanup step must NOT open a priority window.  When the active
player's hand exceeds 7, the server sends GAME_STATE_UPDATE and awaits
DISCARD (echoing that GSU's seq), rejecting invalid discards with
ILLEGAL_ACTION, and repeats until the hand is ≤ 7.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

from server.game_lifecycle import GameLifecycle
from server.game_state import GameState


def _make_gs(hand_size: int, ap: str = "p1") -> GameState:
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.active_player = ap
    gs.turn = 1
    gs.life_totals = {"p1": 20, "p2": 20}
    gs.hands = {"p1": [f"card_{i:03d}" for i in range(hand_size)], "p2": []}
    gs.libraries = {"p1": ["mountain_001"] * 10, "p2": ["mountain_001"] * 10}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    gs.mana_pools = {}
    return gs


def _make_lifecycle(gs: GameState, responses: list[dict]) -> GameLifecycle:
    lc = GameLifecycle.__new__(GameLifecycle)  # skip __init__ (no card loader)
    lc.gs = gs
    lc.priority_mgr = SimpleNamespace(
        config=SimpleNamespace(time_limit_ms=60000),
    )
    lc.conn = SimpleNamespace(seq_num=5)
    lc.responses = list(responses)
    lc.waits = 0
    lc.sent_errors: list[str] = []
    lc.gsu_to: list[str] = []
    lc.ended: tuple | None = None

    async def wait_for_pdu(pid, timeout):
        lc.waits += 1
        if not lc.responses:
            raise asyncio.TimeoutError()
        return lc.responses.pop(0)

    async def send_to(pid, pdu):
        if pdu.get("type") == "GAME_STATE_UPDATE":
            lc.gsu_to.append(pid)

    async def send_error(conn, code, message, rejected_action):
        lc.sent_errors.append(code)

    async def end_game(gs, reason, winner, loser):
        lc.ended = (reason, winner, loser)

    lc._connection_for = lambda pid: lc.conn
    lc.wait_for_pdu = wait_for_pdu
    lc.send_to = send_to
    lc.send_error = send_error
    lc._opponent = lambda pid: "p2" if pid == "p1" else "p1"
    lc._end_game = end_game
    return lc


def _discard(*card_ids: str) -> dict:
    return {"type": "DISCARD", "card_ids": list(card_ids)}


class TestCleanupDiscard:

    def test_discard_loop_without_priority_window(self):
        gs = _make_gs(hand_size=9)
        lc = _make_lifecycle(gs, [_discard("card_008", "card_007")])

        asyncio.run(lc._run_cleanup_discard(gs, "p1"))

        # Exactly one DISCARD wait; hand down to 7; cards hit the graveyard.
        assert lc.waits == 1
        assert len(gs.hands["p1"]) == 7
        assert set(gs.graveyards["p1"]) == {"card_008", "card_007"}
        assert gs._cleanup_discard_for is None
        # No errors; final GSU broadcast to BOTH players (§7.8).
        assert lc.sent_errors == []
        assert set(lc.gsu_to) == {"p1", "p2"}

    def test_repeats_until_hand_at_or_below_seven(self):
        gs = _make_gs(hand_size=10)
        # Validator (§8.15) requires exactly hand-7 = 3 cards per DISCARD.
        # An undersized first attempt is rejected; the loop keeps waiting
        # until a valid discard brings the hand to 7.
        lc = _make_lifecycle(gs, [_discard("card_009", "card_008"),
                                  _discard("card_007", "card_006", "card_005")])

        asyncio.run(lc._run_cleanup_discard(gs, "p1"))

        assert lc.waits == 2  # Invalid attempt + retry.
        assert lc.sent_errors == ["ILLEGAL_ACTION"]
        assert len(gs.hands["p1"]) == 7
        assert gs._cleanup_discard_for is None

    def test_invalid_discard_rejected_with_error(self):
        gs = _make_gs(hand_size=9)
        # First attempt discards a card NOT in hand → ILLEGAL_ACTION.
        lc = _make_lifecycle(gs, [_discard("not_in_hand"),
                                  _discard("card_008", "card_007")])

        asyncio.run(lc._run_cleanup_discard(gs, "p1"))

        assert lc.sent_errors == ["ILLEGAL_ACTION"]
        assert lc.waits == 2
        assert len(gs.hands["p1"]) == 7
        assert "not_in_hand" not in gs.graveyards["p1"]

    def test_non_discard_pdu_rejected(self):
        gs = _make_gs(hand_size=9)
        lc = _make_lifecycle(gs, [
            {"type": "PRIORITY_PASS", "seq_num": 5},
            _discard("card_008", "card_007"),
        ])

        asyncio.run(lc._run_cleanup_discard(gs, "p1"))

        assert lc.sent_errors == ["ILLEGAL_ACTION"]
        assert len(gs.hands["p1"]) == 7

    def test_hand_within_limit_does_nothing(self):
        gs = _make_gs(hand_size=5)
        lc = _make_lifecycle(gs, [])

        asyncio.run(lc._run_cleanup_discard(gs, "p1"))

        assert lc.waits == 0
        assert len(gs.hands["p1"]) == 5
        assert lc.ended is None
