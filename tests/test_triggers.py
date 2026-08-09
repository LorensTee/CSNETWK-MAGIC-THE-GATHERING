"""
tests/test_triggers.py — RFC §8.6 triggered abilities.

Goblin Guide (ATTACKS: reveal top card; land → hand), Monastery Swiftspear
(CAST_NONCREATURE_SPELL: prowess pump).  Triggers are placed on the stack
as TRIGGER_ABILITY items and resolve like spells — WITHOUT re-spawning the
source creature.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from server.card_effects import (
    _effect_goblin_guide_trigger,
    _effect_monastery_swiftspear_trigger,
    check_triggers,
)
from server.card_loader import CardLoader
from server.game_lifecycle import GameLifecycle
from server.game_state import GameState, Permanent
from server.stack import StackManager

LOADER = CardLoader()
LOADER.load()


def _make_gs() -> GameState:
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.life_totals = {"p1": 20, "p2": 20}
    gs.hands = {"p1": [], "p2": []}
    gs.libraries = {"p1": [], "p2": []}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    gs.mana_pools = {}
    return gs


class TestTriggerEffects:

    def test_goblin_guide_reveals_land_to_hand(self):
        gs = _make_gs()
        gs.libraries["p2"] = ["mountain_001", "grizzly_bears_001"]

        changes = _effect_goblin_guide_trigger(
            gs, "p1", [], [], {"card_loader": LOADER},
        )

        assert "mountain_001" in gs.hands["p2"]
        assert gs.libraries["p2"] == ["grizzly_bears_001"]
        assert any(c.get("change_type") == "REVEAL"
                   and c.get("card_id") == "mountain_001" for c in changes)

    def test_goblin_guide_reveals_nonland_to_graveyard(self):
        gs = _make_gs()
        gs.libraries["p2"] = ["grizzly_bears_001"]

        _effect_goblin_guide_trigger(gs, "p1", [], [], {"card_loader": LOADER})

        assert "grizzly_bears_001" in gs.graveyards["p2"]
        assert gs.hands["p2"] == []
        assert gs.libraries["p2"] == []

    def test_swiftspear_trigger_pumps_source(self):
        gs = _make_gs()
        gs.battlefield["p1"] = [
            Permanent(id="swiftspear_001", card_def_id="monastery_swiftspear",
                      controller="p1", power=1, toughness=2),
        ]

        changes = _effect_monastery_swiftspear_trigger(
            gs, "p1", [], [], {"source_permanent": "swiftspear_001"},
        )

        perm = gs.battlefield["p1"][0]
        assert perm.temp_power == 1
        assert perm.temp_toughness == 1
        assert any(c.get("change_type") == "PUMP" for c in changes)


class TestTriggerStack:

    def test_push_trigger_creates_trigger_ability_item(self):
        gs = _make_gs()
        sm = StackManager()

        si = sm.push_trigger(gs, "goblin_guide_trigger", "p1",
                             targets=["p2"], source_permanent="gg_001")

        assert si.item_type == "TRIGGER_ABILITY"
        assert gs.stack[-1].stack_item_id == si.stack_item_id
        assert getattr(si, "source_permanent", "") == "gg_001"

    def test_resolving_trigger_does_not_respawn_creature(self):
        gs = _make_gs()
        gs.libraries["p2"] = ["mountain_001"]
        sm = StackManager()
        sm.push_trigger(gs, "goblin_guide_trigger", "p1",
                        targets=["p2"], source_permanent="gg_001")

        result, changes = sm.resolve_top(gs, card_loader=LOADER)

        assert result == "RESOLVED"
        assert gs.battlefield["p1"] == []  # no double-spawn
        assert "mountain_001" in gs.hands["p2"]

    def test_check_triggers_finds_event_permanents(self):
        gs = _make_gs()
        gs.battlefield["p1"] = [
            Permanent(id="gg_001", card_def_id="goblin_guide",
                      controller="p1", power=2, toughness=2),
            Permanent(id="gg_002", card_def_id="goblin_guide",
                      controller="p1", power=2, toughness=2),
        ]

        triggers = check_triggers(gs, "ATTACKS", "gg_001", "p1")

        assert len(triggers) == 2  # registry scans all matching permanents
        assert all(t["requires_target"] is False for t in triggers)


class TestLifecycleHooks:

    def _make_lifecycle(self, gs: GameState) -> GameLifecycle:
        lc = GameLifecycle.__new__(GameLifecycle)
        lc.gs = gs
        lc.stack_mgr = StackManager()
        lc.combat_mgr = SimpleNamespace(attackers={})
        lc.pushed: list[dict] = []

        async def broadcast(pdu):
            lc.pushed.append(dict(pdu))

        lc.broadcast = broadcast
        return lc

    def test_attack_hook_pushes_only_attacking_goblin(self):
        gs = _make_gs()
        gs.battlefield["p1"] = [
            Permanent(id="gg_001", card_def_id="goblin_guide",
                      controller="p1", power=2, toughness=2),
            Permanent(id="gg_002", card_def_id="goblin_guide",
                      controller="p1", power=2, toughness=2),
        ]
        lc = self._make_lifecycle(gs)
        lc.combat_mgr.attackers = {"gg_001": "p2"}  # only gg_001 attacks

        asyncio.run(lc._maybe_push_attack_triggers(gs, "p1", "p2"))

        triggers = [i for i in gs.stack if i.item_type == "TRIGGER_ABILITY"]
        assert len(triggers) == 1  # non-attacking goblin does NOT trigger
        assert triggers[0].source == "goblin_guide_trigger"
        assert lc.pushed and lc.pushed[0]["item_type"] == "TRIGGER_ABILITY"

    def test_cast_hook_skips_creature_spells(self):
        gs = _make_gs()
        gs.battlefield["p1"] = [
            Permanent(id="swift_001", card_def_id="monastery_swiftspear",
                      controller="p1", power=1, toughness=2),
        ]
        lc = self._make_lifecycle(gs)

        asyncio.run(lc._maybe_push_cast_triggers(
            gs, "p1", LOADER.get_card("grizzly_bears"),
        ))

        assert gs.stack == []  # creature cast does not trigger prowess

    def test_cast_hook_pushes_prowess_trigger(self):
        gs = _make_gs()
        gs.battlefield["p1"] = [
            Permanent(id="swift_001", card_def_id="monastery_swiftspear",
                      controller="p1", power=1, toughness=2),
        ]
        lc = self._make_lifecycle(gs)

        asyncio.run(lc._maybe_push_cast_triggers(
            gs, "p1", LOADER.get_card("lightning_bolt"),
        ))

        assert len(gs.stack) == 1
        assert gs.stack[0].item_type == "TRIGGER_ABILITY"
        assert getattr(gs.stack[0], "source_permanent", "") == "swift_001"
