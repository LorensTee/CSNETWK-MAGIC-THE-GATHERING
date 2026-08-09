"""
tests/test_card_effects.py — Unit tests for card effect resolution (Module 02).

Tests spell effects that mutate game state directly: mana production
(Dark Ritual, spec §7.5), creature spawns, and discard/damage effects.
"""

from types import SimpleNamespace

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


class _FakeLoader:
    """Minimal CardLoader: base id → SimpleNamespace CardDef."""

    def __init__(self, defs: dict[str, dict]) -> None:
        self._defs = defs

    def get_card(self, base_id: str):
        d = self._defs.get(base_id)
        if d is None:
            return None
        return SimpleNamespace(
            card_id_base=base_id,
            color=d.get("color", ""),
            power=d.get("power", 0),
            toughness=d.get("toughness", 0),
            card_type=d.get("card_type", "Creature"),
            abilities=d.get("abilities", []),
        )


class TestGrayMerchant:
    """Gray Merchant of Asphodel enters the battlefield and drains (devotion)."""

    def test_enters_and_drains(self):
        from server.game_state import Permanent
        from server.card_effects import _effect_gray_merchant

        gs = _make_gs()
        gs.battlefield["p1"] = [
            Permanent(id="swamp_001", card_def_id="swamp",
                      controller="p1", power=0, toughness=0),
            Permanent(id="wall_001", card_def_id="wall_of_stone",
                      controller="p1", power=0, toughness=8),
        ]
        loader = _FakeLoader({
            "swamp": {"color": "B"},
            "wall_of_stone": {"color": ""},
            "gray_merchant": {"color": "B", "power": 2, "toughness": 2},
        })

        changes = _effect_gray_merchant(
            gs, "p1", [], [],
            {"card_id": "gray_merchant_001", "card_loader": loader},
        )

        # The creature itself must be on the battlefield (ETB spawn).
        spawned = [p for p in gs.battlefield["p1"]
                   if p.id == "gray_merchant_001"]
        assert len(spawned) == 1
        assert spawned[0].card_def_id == "gray_merchant"
        assert spawned[0].power == 2 and spawned[0].toughness == 2
        # Devotion to black == 1 (swamp); opponent loses 1, controller gains 1.
        assert gs.life_totals["p2"] == 19
        assert gs.life_totals["p1"] == 21
        assert any(c.get("change_type") == "LIFE_LOSS" for c in changes)


class TestGravedigger:
    """Gravedigger enters the battlefield and returns a creature from the
    graveyard to hand."""

    def test_enters_and_returns(self):
        from server.game_state import Permanent
        from server.card_effects import _effect_gravedigger

        gs = _make_gs()
        gs.graveyards["p1"] = ["grizzly_bears_001"]
        loader = _FakeLoader({
            "grizzly_bears": {"power": 2, "toughness": 2},
            "gravedigger": {"power": 2, "toughness": 2},
        })

        changes = _effect_gravedigger(
            gs, "p1", ["grizzly_bears_001"], [],
            {"card_id": "gravedigger_001", "card_loader": loader},
        )

        # Creature spawns.
        spawned = [p for p in gs.battlefield["p1"]
                   if p.id == "gravedigger_001"]
        assert len(spawned) == 1
        # Target returned from graveyard to hand (instance id preserved).
        assert "grizzly_bears_001" not in gs.graveyards["p1"]
        assert "grizzly_bears_001" in gs.hands["p1"]
        assert any(c.get("change_type") == "RETURN_FROM_GRAVEYARD"
                   for c in changes)
