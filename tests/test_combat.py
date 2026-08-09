"""
tests/test_combat.py — Unit tests for the combat system (Module 02).

Tests attacker/blocker declaration, damage computation, first strike,
trample, and creature death tracking.
"""

from server.game_state import GameState, Permanent
from server.combat import CombatManager


def _make_gs():
    """Create a minimal game state for combat testing."""
    gs = GameState()
    gs.player_ids = ["p1", "p2"]
    gs.life_totals = {"p1": 20, "p2": 20}
    gs.battlefield = {"p1": [], "p2": []}
    gs.graveyards = {"p1": [], "p2": []}
    gs.active_player = "p1"
    return gs


class TestDeclareAttackers:

    def test_set_attackers(self):
        gs = _make_gs()
        cm = CombatManager()
        goblin = Permanent(id="gg_001", card_def_id="goblin_guide",
                           controller="p1", power=2, toughness=2)
        gs.battlefield["p1"].append(goblin)

        changes = cm.set_attackers(gs, "p1", [
            {"creature_id": "gg_001", "target": "p2"},
        ])
        assert "gg_001" in cm.attackers
        # Attacker should be tapped (no vigilance)
        assert goblin.tapped is True

    def test_no_attackers(self):
        gs = _make_gs()
        cm = CombatManager()
        cm.set_attackers(gs, "p1", [])
        assert cm.attackers == {}


class TestDeclareBlockers:

    def test_set_blockers(self):
        gs = _make_gs()
        cm = CombatManager()
        cm.attackers = {"gg_001": "p2"}
        wall = Permanent(id="wall_001", card_def_id="wall_of_stone",
                         controller="p2", power=0, toughness=8)
        gs.battlefield["p2"].append(wall)

        changes = cm.set_blockers(gs, "p2", [
            {"creature_id": "wall_001", "blocking_id": "gg_001"},
        ])
        assert cm.blockers["wall_001"] == "gg_001"
        # Blockers should NOT be tapped
        assert wall.tapped is False


class TestDamageComputation:

    def test_unblocked_attacker(self):
        gs = _make_gs()
        cm = CombatManager()
        cm.attackers = {"gg_001": "p2"}
        goblin = Permanent(id="gg_001", card_def_id="goblin_guide",
                           controller="p1", power=2, toughness=2)
        gs.battlefield["p1"].append(goblin)

        result = cm.compute_combat_damage(gs)
        # Should deal 2 damage to p2
        assert any(
            e["source"] == "gg_001" and e["target"] == "p2" and e["amount"] == 2
            for e in result["damage_events"]
        )

    def test_blocked_attacker_kills_blocker(self):
        gs = _make_gs()
        cm = CombatManager()
        cm.attackers = {"gg_001": "p2"}
        cm.blockers = {"wall_001": "gg_001"}
        goblin = Permanent(id="gg_001", card_def_id="goblin_guide",
                           controller="p1", power=5, toughness=3)
        wall = Permanent(id="wall_001", card_def_id="wall_of_stone",
                         controller="p2", power=0, toughness=2)
        gs.battlefield["p1"].append(goblin)
        gs.battlefield["p2"].append(wall)

        result = cm.compute_combat_damage(gs)
        # Wall should take 5 damage, enough to kill it
        assert "wall_001" in result["creatures_died"]

    def test_first_strike_step(self):
        gs = _make_gs()
        cm = CombatManager()
        cm.attackers = {"fs_001": "p2", "nor_001": "p2"}
        fs = Permanent(id="fs_001", card_def_id="white_knight",
                       controller="p1", power=2, toughness=2,
                       abilities=[{"type": "keyword", "name": "first_strike"}])
        nor = Permanent(id="nor_001", card_def_id="grizzly_bears",
                        controller="p1", power=2, toughness=2)
        gs.battlefield["p1"].append(fs)
        gs.battlefield["p1"].append(nor)

        result = cm.compute_first_strike_damage(gs)
        # Only first strike creature should deal damage
        fs_events = [e for e in result["damage_events"] if e["source"] == "fs_001"]
        nor_events = [e for e in result["damage_events"] if e["source"] == "nor_001"]
        assert len(fs_events) > 0, "First strike creature should deal damage"
        assert len(nor_events) == 0, "Non-first-strike should NOT damage in FS step"

    def test_blocked_attacker_deals_no_overflow_to_player(self):
        """Spec §9.7: MTGNP 1.0 does not implement trample.

        A blocked attacker deals its full combat damage to its blocker(s)
        only — never to the defending player, even with the trample
        keyword present.
        """
        gs = _make_gs()
        cm = CombatManager()
        cm.attackers = {"trampler_001": "p2"}
        cm.blockers = {"wall_001": "trampler_001"}
        trampler = Permanent(id="trampler_001", card_def_id="leatherback_baloth",
                             controller="p1", power=4, toughness=4,
                             abilities=[{"type": "keyword", "name": "trample"}])
        wall = Permanent(id="wall_001", card_def_id="wall_of_stone",
                         controller="p2", power=0, toughness=2)
        gs.battlefield["p1"].append(trampler)
        gs.battlefield["p2"].append(wall)

        result = cm.compute_combat_damage(gs)

        # NO damage to the defending player (no trample overflow in MTGNP).
        player_dmg = [e for e in result["damage_events"]
                      if e["source"] == "trampler_001" and e["target"] == "p2"]
        assert player_dmg == []
        # The blocker takes lethal damage (2), assigned in damage order.
        wall_dmg = [e for e in result["damage_events"]
                    if e["source"] == "trampler_001" and e["target"] == "wall_001"]
        assert wall_dmg[0]["amount"] == 2
        assert "wall_001" in result["creatures_died"]
        # Life totals unchanged.
        assert gs.life_totals["p2"] == 20
