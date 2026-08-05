"""
server/game_state.py — Authoritative Game State (Module 02: Server Engine)

Defines the core data structures that represent the complete game state on the
server.  The server is the SOLE source of truth — every zone, life total, turn
counter, and phase transition is recorded here.

The ``build_visible_state()`` function is the critical "information filtering"
layer: it produces a per-player view of the state that hides the opponent's
hand (showing only a count) while revealing all public zones.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from server.mana import ManaPool


@dataclass
class Permanent:
    """A permanent on the battlefield (land, creature, enchantment, artifact).

    Non-creature permanents have only *id*, *tapped*, and *controller*.
    Creatures additionally carry *power*, *toughness*, *damage*, and
    *summoning_sick*.
    """

    id: str
    """card instance ID, e.g. ``'goblin_guide_001'``."""

    card_def_id: str
    """Base card ID, e.g. ``'goblin_guide'``."""

    controller: str
    """Player ID of the permanent's controller."""

    tapped: bool = False

    # ── Creature-only fields ────────────────────────────────────────────

    damage: int = 0
    """Marked damage this turn (compared against *toughness* at cleanup)."""

    power: int = 0
    """Current power (may be modified by effects)."""

    toughness: int = 0
    """Current toughness (may be modified by effects)."""

    summoning_sick: bool = True
    """``True`` if the creature entered under this player's control this turn."""

    # ── Effects / modifications ─────────────────────────────────────────

    enchanted_by: list[str] = field(default_factory=list)
    """card instance IDs of auras enchanting this permanent."""

    # Ability flags (parsed from CardDef.abilities at resolution time).
    # These are included for quick lookup during validation/combat.
    abilities: list[dict[str, Any]] = field(default_factory=list)
    """List of ability dicts from CardDef.abilities that apply to this
    permanent instance.  Each dict has 'type' and 'name' keys.
    Used during combat (haste, flying, etc.) and during effect resolution."""


@dataclass
class StackItem:
    """A single item on the stack (spell, ability, or triggered ability)."""

    stack_item_id: str
    """Unique ID, e.g. ``'stk_01'``, ``'stk_02'``."""

    item_type: str
    """One of ``'SPELL'``, ``'ABILITY'``, or ``'TRIGGER_ABILITY'``."""

    source: str
    """card_id of the card that produced this item."""

    controller: str
    """Player ID of the player who controls this stack item."""

    targets: list[str] = field(default_factory=list)
    """Target player_ids or permanent IDs (or empty if no targets)."""

    # Internal (not serialised to clients):
    card_def: Any = None
    """Reference to the ``CardDef`` for effect resolution."""


@dataclass
class GameState:
    """The single authoritative game state.

    All zones, counters, and metadata required to describe a game in progress.
    Mutated exclusively by the server game engine.
    """

    # ── Identification ──────────────────────────────────────────────────

    phase: str = "LOBBY"
    """Current phase or lifecycle state."""

    turn: int = 0
    """Turn number (0 during MULLIGAN, 1+ during IN_GAME)."""

    active_player: str | None = None
    """Player ID of the active player (AP) this turn."""

    priority_holder: str | None = None
    """Player ID who currently holds priority, or ``None`` during UNTAP/CLEANUP."""

    # ── Scoring ─────────────────────────────────────────────────────────

    life_totals: dict[str, int] = field(default_factory=lambda: {})

    # ── Zones ───────────────────────────────────────────────────────────

    libraries: dict[str, list[str]] = field(default_factory=dict)
    """Each player's library as an ordered list; index 0 = top card."""

    hands: dict[str, list[str]] = field(default_factory=dict)
    """Each player's hand; the opponent's hand is hidden via *hand_counts*."""

    battlefield: dict[str, list[Permanent]] = field(default_factory=dict)
    """Each player's permanents on the battlefield."""

    graveyards: dict[str, list[str]] = field(default_factory=dict)
    """Each player's graveyard; index 0 = first buried, last = most recent."""

    stack: list[StackItem] = field(default_factory=list)
    """Items on the stack; index -1 = top, index 0 = bottom."""

    # ── State flags ─────────────────────────────────────────────────────

    land_played_this_turn: bool = False
    """``True`` if the AP has already played a land this turn."""

    mulligan_counts: dict[str, int] = field(default_factory=dict)
    """Number of mulligans taken per player."""

    mana_pool: ManaPool = field(default_factory=ManaPool.empty)
    """Floating mana available to the current active player."""

    # ── Lobby / setup ───────────────────────────────────────────────────

    players_ready: int = 0
    """Count of players who submitted a valid ``PLAYER_READY``."""

    waiting_for: list[str] = field(default_factory=list)
    """Player IDs not yet ready in the lobby."""

    player_ids: list[str] = field(default_factory=list)
    """Ordered list of player IDs by connection order (index 0 = player 1)."""

    # ── Internal counters ───────────────────────────────────────────────

    stack_counter: int = 0
    """Auto-incremented to generate unique ``stack_item_id`` values."""

    # ── Dynamic state flags (set at runtime by turn engine) ──────────────

    _draw_failed_for: str | None = None
    """If set, the player ID whose draw step failed due to empty library."""

    _cleanup_discard_for: str | None = None
    """If set, the player ID whose hand exceeds 7 during Cleanup."""


# ── Serialisation helpers ────────────────────────────────────────────────────


def serialize_permanent(p: Permanent) -> dict[str, Any]:
    """Convert a *Permanent* to a JSON-safe dict for ``GAME_STATE_UPDATE``.

    Creatures include *power*, *toughness*, *damage*, and *summoning_sick*.
    Non-creatures include only *id* and *tapped*.
    """
    base: dict[str, Any] = {"id": p.id, "tapped": p.tapped}
    # A permanent is a creature if it has meaningful power/toughness or
    # the card definition says so.  We use a simple heuristic here:
    # if the Permanent was constructed with non-zero toughness, treat it
    # as a creature.
    if p.toughness > 0 or p.power > 0:
        base["damage"] = p.damage
        base["power"] = p.power
        base["toughness"] = p.toughness
        base["summoning_sick"] = p.summoning_sick
        if p.abilities:
            base["abilities"] = p.abilities
    return base


def serialize_stack_item(si: StackItem) -> dict[str, Any]:
    """Convert a *StackItem* to a JSON-safe dict for ``GAME_STATE_UPDATE``."""
    return {
        "stack_item_id": si.stack_item_id,
        "item_type": si.item_type,
        "source": si.source,
        "targets": si.targets,
        "controller": si.controller,
    }


def build_visible_state(
    gs: GameState,
    for_player: str,
) -> dict[str, Any]:
    """Build a personalised visible-state dict for *for_player*.

    The opponent's hand is shown only as a count via *hand_counts*.
    All other zones are fully visible.
    """
    opponent = _opponent(gs, for_player)
    # Determine which hand the opponent gets (if any).
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


def _opponent(gs: GameState, player_id: str) -> str | None:
    """Return the opponent's player ID, or ``None`` if there is only one player."""
    for pid in gs.player_ids:
        if pid != player_id:
            return pid
    return None
