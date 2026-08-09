"""
tests/test_card_effects.py — Unit tests for card effect resolution (Module 02).

Tests spell effects that mutate game state directly: mana production
(Dark Ritual, spec §7.5), creature spawns, and discard/damage effects.
"""

from server.game_state import GameState
from server.card_effects import _effect_dark_ritual


def _make_gs() -> GameState:
    """Minimal game state with per-player mana pools."""
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.life_totals = {"p1": 20, "p2": 20}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    gs.mana_pools = {}
    return gs


class TestDarkRitual:
    """Dark Ritual adds {B}{B}{B} to the caster's mana pool (§7.5)."""

    def test_adds_three_black_mana_to_casters_pool(self):
        gs = _make_gs()
        changes = _effect_dark_ritual(gs, "p1", [], [], {})

        pool = gs.mana_pools["p1"]
        assert pool.B == 3
        assert pool.W == pool.U == pool.R == pool.G == pool.C == 0
        # An ADD_MANA state change is emitted for broadcasting.
        assert any(
            c.get("change_type") == "ADD_MANA"
            and c.get("player") == "p1"
            and c.get("mana") == {"B": 3}
            for c in changes
        )

    def test_mana_is_per_player(self):
        gs = _make_gs()
        _effect_dark_ritual(gs, "p1", [], [], {})

        assert gs.mana_pools["p1"].B == 3
        # The opponent's pool must not receive the mana.
        assert "p2" not in gs.mana_pools or gs.mana_pools["p2"].B == 0

    def test_dark_ritual_does_not_crash_without_existing_pool(self):
        gs = _make_gs()
        _effect_dark_ritual(gs, "p1", [], [], {})
        # Second cast adds to the same pool (3 + 3 = 6).
        _effect_dark_ritual(gs, "p1", [], [], {})
        assert gs.mana_pools["p1"].B == 6
