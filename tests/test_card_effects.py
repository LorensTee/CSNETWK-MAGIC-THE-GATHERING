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


class TestMindRot:
    """Mind Rot: target player discards two cards (RFC §7.6 effect)."""

    def test_target_player_discards_two(self):
        from server.card_effects import _effect_mind_rot

        gs = _make_gs()
        gs.hands["p2"] = ["card_a", "card_b", "card_c", "card_d"]

        changes = _effect_mind_rot(gs, "p1", ["p2"], [], {})

        assert len(gs.hands["p2"]) == 2
        assert len(gs.graveyards["p2"]) == 2
        assert set(gs.graveyards["p2"]) <= {"card_a", "card_b", "card_c", "card_d"}
        assert any(c.get("change_type") == "DISCARD" for c in changes)

    def test_no_target_is_noop(self):
        from server.card_effects import _effect_mind_rot

        gs = _make_gs()
        gs.hands["p2"] = ["card_a", "card_b"]
        changes = _effect_mind_rot(gs, "p1", [], [], {})
        assert changes == []
        assert gs.hands["p2"] == ["card_a", "card_b"]


class TestSwordsToPlowshares:
    """Swords to Plowshares: exile target creature; ITS CONTROLLER gains
    life equal to its power (not a flat +3 to the spell's controller)."""

    def test_creature_controller_gains_life_equal_to_power(self):
        from server.game_state import Permanent
        from server.card_effects import _effect_swords_to_plowshares

        gs = _make_gs()
        grizzly = Permanent(id="grizzly_001", card_def_id="grizzly_bears",
                            controller="p2", power=2, toughness=2)
        gs.battlefield["p2"].append(grizzly)

        changes = _effect_swords_to_plowshares(gs, "p1", ["grizzly_001"], [], {})

        # Exiled (per-player exile zone, instance id preserved).
        assert grizzly not in gs.battlefield["p2"]
        assert "grizzly_001" in getattr(gs, "exile", {}).get("p2", [])
        # The CREATURE'S controller gains power (= 2) life.
        assert gs.life_totals["p2"] == 22
        assert gs.life_totals["p1"] == 20
        assert any(c.get("change_type") == "LIFE_GAIN"
                   and c.get("target") == "p2" and c.get("amount") == 2
                   for c in changes)


class TestSpawnKeywords:
    """Permanents must carry their keyword abilities (vigilance/flying/haste
    are read from CardDef.abilities at spawn time)."""

    @classmethod
    def setup_class(cls):
        from server.card_loader import CardLoader
        cls.loader = CardLoader()
        cls.loader.load()

    def test_spawned_creature_carries_keyword_abilities(self):
        from server.card_effects import _apply_spawn_permanent

        gs = _make_gs()
        _apply_spawn_permanent(gs, "p1", "serra_angel_001", self.loader)

        perm = gs.battlefield["p1"][0]
        names = {a.get("name") for a in perm.abilities}
        assert "vigilance" in names
        assert "flying" in names
        assert perm.card_def_id == "serra_angel"
        assert perm.power == 4 and perm.toughness == 4

    def test_multiword_base_id_resolves_correctly(self):
        from server.card_effects import _apply_spawn_permanent

        gs = _make_gs()
        _apply_spawn_permanent(gs, "p1", "grizzly_bears_001", self.loader)

        perm = gs.battlefield["p1"][0]
        # Multi-word base id must not be truncated ('grizzly', not 'grizzly').
        assert perm.card_def_id == "grizzly_bears"
        assert perm.power == 2 and perm.toughness == 2

    def test_haste_skips_summoning_sickness(self):
        from server.card_effects import _apply_spawn_permanent

        gs = _make_gs()
        _apply_spawn_permanent(gs, "p1", "goblin_guide_001", self.loader)

        perm = gs.battlefield["p1"][0]
        assert perm.summoning_sick is False  # Haste → can attack immediately.

    def test_non_haste_creature_has_summoning_sickness(self):
        from server.card_effects import _apply_spawn_permanent

        gs = _make_gs()
        _apply_spawn_permanent(gs, "p1", "grizzly_bears_001", self.loader)

        assert gs.battlefield["p1"][0].summoning_sick is True
