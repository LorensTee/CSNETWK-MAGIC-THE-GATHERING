# authoritative game state module
# tracks zones life totals turn structure and fog of war filtering

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from server.mana import ManaPool

# permanent dataclass
# represents card instance on battlefield with status and creature stats
@dataclass
class Permanent:

    id: str
    card_def_id: str
    controller: str
    tapped: bool = False
    damage: int = 0
    power: int = 0
    toughness: int = 0
    summoning_sick: bool = True
    enchanted_by: list[str] = field(default_factory=list)
    abilities: list[dict[str, Any]] = field(default_factory=list)

# stack item dataclass
# represents spell or ability on stack awaiting resolution
@dataclass
class StackItem:
    
	stack_item_id: str
    item_type: str
    source: str
    controller: str
    targets: list[str] = field(default_factory=list)
    card_def: Any = None

# game state dataclass
# single authoritative state containing all zones pools and turn flags
@dataclass
class GameState:

    phase: str = "LOBBY"
    turn: int = 0
    active_player: str | None = None
    priority_holder: str | None = None
    life_totals: dict[str, int] = field(default_factory=lambda: {})
    libraries: dict[str, list[str]] = field(default_factory=dict)
    hands: dict[str, list[str]] = field(default_factory=dict)
    battlefield: dict[str, list[Permanent]] = field(default_factory=dict)
    graveyards: dict[str, list[str]] = field(default_factory=dict)
    stack: list[StackItem] = field(default_factory=list)
    land_played_this_turn: bool = False
    mulligan_counts: dict[str, int] = field(default_factory=dict)
    mana_pools: dict[str, ManaPool] = field(default_factory=dict)
    players_ready: int = 0
    waiting_for: list[str] = field(default_factory=list)
    player_ids: list[str] = field(default_factory=list)
    stack_counter: int = 0
    _draw_failed_for: str | None = None
    _cleanup_discard_for: str | None = None

# serialize permanent instance to json safe dict
def serialize_permanent(p: Permanent) -> dict[str, Any]:
    base: dict[str, Any] = {"id": p.id, "tapped": p.tapped}
    if p.toughness > 0 or p.power > 0:
        base["damage"] = p.damage
        base["power"] = p.power
        base["toughness"] = p.toughness
        base["summoning_sick"] = p.summoning_sick
        if p.abilities:
            base["abilities"] = p.abilities
    return base

# serialize stack item instance to json safe dict
def serialize_stack_item(si: StackItem) -> dict[str, Any]:
    return {
        "stack_item_id": si.stack_item_id,
        "item_type": si.item_type,
        "source": si.source,
        "targets": si.targets,
        "controller": si.controller,
    }

# build personalized visible game state for specific viewer filtering opponent hand
def build_visible_state(
    gs: GameState,
    for_player: str,
) -> dict[str, Any]:
    opponent = _opponent(gs, for_player)
    hand_counts: dict[str, int] = {}
    if opponent is not None and opponent in gs.hands:
        hand_counts[opponent] = len(gs.hands[opponent])

    return {
        "viewer_id": for_player,
        "opponent_id": opponent,
        "turn": gs.turn,
        "active_player": gs.active_player,
        "phase": gs.phase,
        "priority_holder": gs.priority_holder,
        "life_totals": dict(gs.life_totals),
        "stack": [serialize_stack_item(si) for si in gs.stack],
        "battlefield": {
            pid: [serialize_permanent(p) for p in gs.battlefield.get(pid, [])]
            for pid in gs.player_ids
        },
        "graveyard": {
            pid: list(gs.graveyards.get(pid, []))
            for pid in gs.player_ids
        },
        "hand": list(gs.hands.get(for_player, [])),
        "hand_counts": hand_counts,
        "library_counts": {
            pid: len(gs.libraries.get(pid, []))
            for pid in gs.player_ids
        },
        "land_played_this_turn": gs.land_played_this_turn,
    }

# find opponent player id relative to viewer
def _opponent(gs: GameState, player_id: str) -> str | None:
    for pid in gs.player_ids:
        if pid != player_id:
            return pid
    return None
