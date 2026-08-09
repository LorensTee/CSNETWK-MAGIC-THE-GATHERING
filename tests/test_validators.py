"""
tests/test_validators.py — Unit tests for CAST_SPELL validation (Module 02).

Target-type enforcement: Naturalize (artifact/enchantment), Terror and
Doom Blade (nonblack creature, Terror also nonartifact), Negate
(noncreature spell), Raise Dead / Gravedigger (creature in graveyard).
"""

from server.card_loader import CardLoader
from server.game_state import GameState, Permanent
from server.mana import ManaPool
from server.stack import StackManager
from server.validators import validate_cast_spell

LOADER = CardLoader()
LOADER.load()  # CardLoader does not auto-load in __init__


def _make_gs() -> GameState:
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.phase = "PRECOMBAT_MAIN"
    gs.life_totals = {"p1": 20, "p2": 20}
    gs.hands = {"p1": [], "p2": []}
    gs.libraries = {"p1": [], "p2": []}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    gs.mana_pools = {
        "p1": ManaPool(W=2, U=2, B=2, R=2, G=2, C=2),
        "p2": ManaPool(),
    }
    return gs


def _put(gs: GameState, card: str) -> None:
    gs.hands["p1"].append(card)


def _cast(gs: GameState, card: str, targets: list[str],
          payment: dict[str, int] | None = None) -> tuple[bool, str, str]:
    # Payment defaults to the full cost from the pool.
    if payment is None:
        cd = LOADER.get_card(card)
        cost = cd.mana_cost if cd is not None else {}
        payment = dict(cost)
    return validate_cast_spell(gs, "p1", card, targets, payment, LOADER)


class TestNaturalize:

    def test_accepts_artifact_target(self):
        gs = _make_gs()
        _put(gs, "naturalize")
        gs.battlefield["p2"].append(
            Permanent(id="sol_ring_001", card_def_id="sol_ring",
                      controller="p2", power=0, toughness=0))
        ok, code, msg = _cast(gs, "naturalize", ["sol_ring_001"])
        assert ok, (code, msg)

    def test_rejects_creature_target(self):
        gs = _make_gs()
        _put(gs, "naturalize")
        gs.battlefield["p2"].append(
            Permanent(id="grizzly_001", card_def_id="grizzly_bears",
                      controller="p2", power=2, toughness=2))
        ok, code, _ = _cast(gs, "naturalize", ["grizzly_001"])
        assert not ok
        assert code == "ILLEGAL_TARGET"


class TestTerror:

    def test_accepts_nonblack_creature(self):
        gs = _make_gs()
        _put(gs, "terror")
        gs.battlefield["p2"].append(
            Permanent(id="grizzly_001", card_def_id="grizzly_bears",
                      controller="p2", power=2, toughness=2))
        ok, code, msg = _cast(gs, "terror", ["grizzly_001"])
        assert ok, (code, msg)

    def test_rejects_black_creature(self):
        gs = _make_gs()
        _put(gs, "terror")
        gs.battlefield["p2"].append(
            Permanent(id="black_knight_001", card_def_id="black_knight",
                      controller="p2", power=2, toughness=2))
        ok, code, _ = _cast(gs, "terror", ["black_knight_001"])
        assert not ok
        assert code == "ILLEGAL_TARGET"


class TestDoomBlade:

    def test_rejects_black_creature(self):
        gs = _make_gs()
        _put(gs, "doom_blade")
        gs.battlefield["p2"].append(
            Permanent(id="royal_assassin_001", card_def_id="royal_assassin",
                      controller="p2", power=1, toughness=1))
        ok, code, _ = _cast(gs, "doom_blade", ["royal_assassin_001"])
        assert not ok
        assert code == "ILLEGAL_TARGET"

    def test_accepts_nonblack_creature(self):
        gs = _make_gs()
        _put(gs, "doom_blade")
        gs.battlefield["p2"].append(
            Permanent(id="grizzly_001", card_def_id="grizzly_bears",
                      controller="p2", power=2, toughness=2))
        ok, code, msg = _cast(gs, "doom_blade", ["grizzly_001"])
        assert ok, (code, msg)


class TestNegate:

    @staticmethod
    def _spell_on_stack(gs: GameState, card: str) -> str:
        cd = LOADER.get_card(card)
        sm = StackManager()
        si = sm.push(gs, "SPELL", card, "p1", [], cd)
        return si.stack_item_id

    def test_rejects_creature_spell(self):
        gs = _make_gs()
        _put(gs, "negate")
        sid = self._spell_on_stack(gs, "grizzly_bears")
        ok, code, _ = _cast(gs, "negate", [sid])
        assert not ok
        assert code == "ILLEGAL_TARGET"

    def test_accepts_noncreature_spell(self):
        gs = _make_gs()
        _put(gs, "negate")
        sid = self._spell_on_stack(gs, "lightning_bolt")
        ok, code, msg = _cast(gs, "negate", [sid])
        assert ok, (code, msg)


class TestRaiseDead:

    def test_accepts_graveyard_creature(self):
        gs = _make_gs()
        _put(gs, "raise_dead")
        gs.graveyards["p1"] = ["grizzly_bears_001"]
        ok, code, msg = _cast(gs, "raise_dead", ["grizzly_bears_001"])
        assert ok, (code, msg)

    def test_rejects_battlefield_only_target(self):
        gs = _make_gs()
        _put(gs, "raise_dead")
        gs.battlefield["p1"].append(
            Permanent(id="grizzly_bears_001", card_def_id="grizzly_bears",
                      controller="p1", power=2, toughness=2))
        ok, code, _ = _cast(gs, "raise_dead", ["grizzly_bears_001"])
        assert not ok
        assert code == "ILLEGAL_TARGET"
