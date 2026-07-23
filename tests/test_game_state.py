"""
tests/test_game_state.py — Unit tests for game state (Module 02).

Tests GameState creation, Permanent/StackItem dataclasses,
serialization helpers, and build_visible_state hand-hiding.
"""

from server.game_state import (
    GameState,
    Permanent,
    StackItem,
    serialize_permanent,
    serialize_stack_item,
    build_visible_state,
)


class TestGameState:

    def test_initial_state(self):
        gs = GameState()
        assert gs.phase == "LOBBY"
        assert gs.turn == 0
        assert gs.active_player is None
        assert gs.life_totals == {}
        assert gs.stack == []
        assert gs.land_played_this_turn is False

    def test_add_player(self):
        gs = GameState()
        gs.player_ids = ["player_1", "player_2"]
        gs.life_totals = {"player_1": 20, "player_2": 20}
        assert gs.life_totals["player_1"] == 20


class TestPermanent:

    def test_creature_defaults(self):
        p = Permanent(
            id="goblin_guide_001",
            card_def_id="goblin_guide",
            controller="player_1",
            power=2, toughness=2,
        )
        assert p.tapped is False
        assert p.damage == 0
        assert p.summoning_sick is True

    def test_non_creature(self):
        p = Permanent(
            id="mountain_001",
            card_def_id="mountain",
            controller="player_1",
        )
        assert p.power == 0
        assert p.toughness == 0


class TestSerialize:

    def test_serialize_permanent_creature(self):
        p = Permanent(
            id="goblin_guide_001",
            card_def_id="goblin_guide",
            controller="player_1",
            power=2, toughness=2, damage=1,
            summoning_sick=False,
        )
        d = serialize_permanent(p)
        assert d["id"] == "goblin_guide_001"
        assert d["power"] == 2
        assert d["toughness"] == 2
        assert d["damage"] == 1
        assert d["summoning_sick"] is False

    def test_serialize_permanent_non_creature(self):
        p = Permanent(
            id="mountain_001",
            card_def_id="mountain",
            controller="player_1",
        )
        d = serialize_permanent(p)
        assert d["id"] == "mountain_001"
        # Non-creature serializes only id and tapped
        assert "power" not in d

    def test_serialize_stack_item(self):
        si = StackItem(
            stack_item_id="stk_01",
            item_type="SPELL",
            source="lightning_bolt_001",
            controller="player_1",
            targets=["player_2"],
        )
        d = serialize_stack_item(si)
        assert d["stack_item_id"] == "stk_01"
        assert d["item_type"] == "SPELL"
        assert d["targets"] == ["player_2"]


class TestBuildVisibleState:

    def test_hides_opponent_hand(self):
        gs = GameState()
        gs.player_ids = ["player_1", "player_2"]
        gs.life_totals = {"player_1": 20, "player_2": 20}
        gs.hands = {"player_1": ["bolt_001"], "player_2": ["swamp_001"]}
        gs.libraries = {"player_1": [], "player_2": []}
        gs.battlefield = {"player_1": [], "player_2": []}
        gs.graveyards = {"player_1": [], "player_2": []}
        gs.phase = "PRECOMBAT_MAIN"
        gs.turn = 3
        gs.active_player = "player_1"

        vs = build_visible_state(gs, "player_1")

        # Player 1 sees their own full hand
        assert vs["hand"] == ["bolt_001"]
        # Player 1 sees only hand COUNT for opponent
        assert vs["hand_counts"] == {"player_2": 1}
        # Opponent's actual cards are NOT in the dict
        assert "swamp_001" not in str(vs["hand"])

    def test_library_counts(self):
        gs = GameState()
        gs.player_ids = ["p1", "p2"]
        gs.life_totals = {"p1": 20, "p2": 20}
        gs.hands = {"p1": [], "p2": []}
        gs.libraries = {"p1": [f"card_{i}" for i in range(10)],
                        "p2": [f"card_{i}" for i in range(5)]}
        gs.battlefield = {"p1": [], "p2": []}
        gs.graveyards = {"p1": [], "p2": []}
        gs.phase = "DRAW"
        gs.turn = 1
        gs.active_player = "p1"

        vs = build_visible_state(gs, "p1")
        assert vs["library_counts"]["p1"] == 10
        assert vs["library_counts"]["p2"] == 5
