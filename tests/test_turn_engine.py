"""
tests/test_turn_engine.py — Unit tests for the turn/phase engine (Module 02).

Tests the 14-phase sequence, mana-pool emptying at phase boundaries
(MTG rule: unspent mana empties as each step ends), and the turn-1
draw skip.
"""

import asyncio

from server.game_state import GameState
from server.mana import ManaPool
from server.turn_engine import TurnEngine


def _make_gs() -> GameState:
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.life_totals = {"p1": 20, "p2": 20}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    gs.libraries = {"p1": ["mountain_001"] * 10, "p2": ["mountain_001"] * 10}
    gs.mana_pools = {"p1": ManaPool(B=3), "p2": ManaPool()}
    return gs


class TestPhaseSkipping:
    """A phase handler may set gs._skip_to_phase to jump ahead.

    Program-states.md step 18: with no attackers declared the server skips
    Declare Blockers, Assign Damage Order, and Combat Damage, advancing
    directly to End of Combat.
    """

    def test_skip_to_phase_jumps_ahead(self):
        gs = _make_gs()
        seen: list[str] = []

        async def handler(gs, ap, nap, phase):
            seen.append(phase)
            if phase == "DECLARE_ATTACKERS":
                gs._skip_to_phase = "END_OF_COMBAT"

        asyncio.run(TurnEngine(phase_handler=handler).run_turn(gs, "p1", "p2"))

        assert "DECLARE_ATTACKERS" in seen
        assert "END_OF_COMBAT" in seen
        # Skipped combat steps must not run.
        assert "DECLARE_BLOCKERS" not in seen
        assert "ASSIGN_DAMAGE_ORDER" not in seen
        assert "FIRST_STRIKE_DAMAGE" not in seen
        assert "COMBAT_DAMAGE" not in seen
        # The phases AFTER the skip target still run.
        assert "POSTCOMBAT_MAIN" in seen
        assert "END_STEP" in seen
        # Skip flag consumed (cleared to None).
        assert getattr(gs, "_skip_to_phase", None) is None

    def test_transition_broadcast_to_skip_target(self):
        gs = _make_gs()
        transitions: list[tuple[str, str]] = []

        async def handler(gs, ap, nap, phase):
            if phase == "DECLARE_ATTACKERS":
                gs._skip_to_phase = "END_OF_COMBAT"

        async def advance(gs, from_phase, to_phase):
            transitions.append((from_phase, to_phase))

        engine = TurnEngine(phase_handler=handler, advance_handler=advance)
        asyncio.run(engine.run_turn(gs, "p1", "p2"))

        # The walkthrough shows a single DECLARE_ATTACKERS → END_OF_COMBAT
        # transition (no intermediate combat-step transitions).
        assert ("DECLARE_ATTACKERS", "END_OF_COMBAT") in transitions
        assert ("DECLARE_ATTACKERS", "DECLARE_BLOCKERS") not in transitions


class TestManaPoolEmptying:

    def test_pools_empty_at_end_of_turn(self):
        gs = _make_gs()
        asyncio.run(TurnEngine().run_turn(gs, "p1", "p2"))
        # Unspent mana must not survive the turn (emptied per phase).
        assert gs.mana_pools["p1"].B == 0
        assert gs.mana_pools["p2"].B == 0

    def test_phase_order_matches_spec(self):
        from shared.constants import IN_GAME_PHASES

        expected = [
            "UNTAP", "UPKEEP", "DRAW", "PRECOMBAT_MAIN", "BEGIN_COMBAT",
            "DECLARE_ATTACKERS", "DECLARE_BLOCKERS", "ASSIGN_DAMAGE_ORDER",
            "FIRST_STRIKE_DAMAGE", "COMBAT_DAMAGE", "END_OF_COMBAT",
            "POSTCOMBAT_MAIN", "END_STEP", "CLEANUP",
        ]
        assert list(IN_GAME_PHASES) == expected

    def test_turn_counter_increments(self):
        gs = _make_gs()
        assert gs.turn == 0
        asyncio.run(TurnEngine().run_turn(gs, "p1", "p2"))
        assert gs.turn == 1
        assert gs.active_player == "p1"
