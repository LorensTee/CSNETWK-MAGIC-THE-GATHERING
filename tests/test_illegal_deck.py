"""
tests/test_illegal_deck.py — RFC §11: an invalid deck list is answered
with ERROR ILLEGAL_DECK (not a GAME_OVER broadcast — GAME_OVER reasons are
WIN/LOSS/CONCEDE/DISCONNECT only).  The player may re-ready with a
corrected deck during GAME_SETUP.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from server.game_lifecycle import GameLifecycle
from server.game_state import GameState

LEGAL_DECK = ["mountain_001"] * 40


def _make_gs() -> GameState:
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.phase = "GAME_SETUP"
    gs.life_totals = {"p1": 0, "p2": 0}
    gs.libraries = {
        "p1": ["mountain_001"] * 20,
        "p2": ["mountain_001"] * 20,
    }
    gs.hands = {"p1": [], "p2": []}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    return gs


def _make_lifecycle(gs: GameState, deck_ok_results: list[bool]) -> GameLifecycle:
    lc = GameLifecycle.__new__(GameLifecycle)
    lc.gs = gs
    lc._game_over = asyncio.Event()
    lc._mulligan_kept = {}
    lc._mulligan_expected_seq = {}
    lc._deck_lists = {"p1": ["not_a_card"], "p2": LEGAL_DECK}
    lc._player_index = {"p1": 0, "p2": 1}
    lc._pending_pdu = {}
    lc.errors: list[str] = []
    lc.ended = None
    lc.gsu_sent = 0

    results = list(deck_ok_results)

    def is_legal_deck(deck):
        if results:
            return results.pop(0), "" if results else "Deck is empty."
        return True, ""

    async def wait_for_pdu(pid, timeout):
        return {"type": "PLAYER_READY", "player_id": pid,
                "deck_list": LEGAL_DECK}

    async def send_error(conn, code, message, rejected_action):
        lc.errors.append(code)

    async def end_game(gs, reason, winner, loser):
        lc.ended = (reason, winner, loser)

    async def send_to(pid, pdu):
        if pdu.get("type") == "GAME_STATE_UPDATE":
            lc.gsu_sent += 1

    lc.card_loader = SimpleNamespace(is_legal_deck=is_legal_deck)
    lc.priority_mgr = SimpleNamespace(
        config=SimpleNamespace(time_limit_ms=60000),
    )
    lc.wait_for_pdu = wait_for_pdu
    lc.send_error = send_error
    lc._end_game = end_game
    lc.send_to = send_to
    lc._connection_for = lambda pid: SimpleNamespace(
        player_id=pid, seq_num=1, verbose=False,
    )
    lc._opponent = lambda pid: "p2" if pid == "p1" else "p1"
    return lc


class TestIllegalDeck:

    def test_illegal_deck_sends_error_not_game_over(self):
        gs = _make_gs()
        # First legality check (p1's stored deck) fails, then the re-ready
        # deck passes.
        lc = _make_lifecycle(gs, deck_ok_results=[False, True])

        asyncio.run(lc._run_setup(gs, []))

        # ERROR ILLEGAL_DECK sent; NO GAME_OVER broadcast.
        assert "ILLEGAL_DECK" in lc.errors
        assert lc.ended is None
        # Setup completed: life totals reset, hands drawn, mulligan events.
        assert gs.life_totals == {"p1": 20, "p2": 20}
        assert len(gs.hands["p1"]) == 7
        assert gs.phase == "MULLIGAN"
        assert set(lc._mulligan_kept) == {"p1", "p2"}

    def test_reready_with_legal_deck_updates_stored_list(self):
        from server.card_loader import CardLoader

        gs = _make_gs()
        lc = _make_lifecycle(gs, deck_ok_results=[True])
        lc._deck_lists = {"p2": LEGAL_DECK}
        lc.card_loader = CardLoader()
        lc.card_loader.load()

        conn = SimpleNamespace(player_id="p1")
        asyncio.run(lc.handle_player_ready(
            conn,
            {"type": "PLAYER_READY", "player_id": "p1",
             "deck_list": LEGAL_DECK},
        ))

        # Corrected deck stored (re-ready during GAME_SETUP).
        assert lc._deck_lists["p1"] == LEGAL_DECK
        assert lc.errors == []
