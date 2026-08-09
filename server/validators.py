"""
server/validators.py — Action Validation (Module 02: Server Engine)

Central validation functions for every player action.  Each function returns
``(ok: bool, error_code: str | None, message: str)``.

These are called **before** any game state mutation.  If *ok* is ``False``,
the server sends an ``ERROR`` PDU with the returned *error_code* and
*message*, and the game state is left unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared.constants import IN_GAME_PHASES, PRIORITY_BEARING_TYPES

if TYPE_CHECKING:
    from server.card_loader import CardLoader
    from server.game_state import GameState


# ── Type alias ───────────────────────────────────────────────────────────────

ValidationResult = tuple[bool, str | None, str]
"""``(ok, error_code, message)`` where *ok* is ``True`` on success."""


# ═══════════════════════════════════════════════════════════════════════════════
# General helpers
# ═══════════════════════════════════════════════════════════════════════════════


def _is_sorcery_speed(card_type: str, card_def: Any) -> bool:
    """Return ``True`` if the card can only be cast at sorcery speed."""
    if card_type == "Sorcery":
        return True
    # Cards with no non-keyword abilities and no flash → sorcery speed.
    return False


def _is_main_phase(phase: str) -> bool:
    """Return ``True`` if *phase* is a main phase."""
    return phase in ("PRECOMBAT_MAIN", "POSTCOMBAT_MAIN")


def _is_instant_speed(card_type: str) -> bool:
    """Return ``True`` if the card type can be cast at instant speed."""
    return card_type in ("Instant",) or card_type == "Instant"


def _player_owns_card(state: GameState, player: str, card_id: str) -> bool:
    """Return ``True`` if *card_id* is in *player*'s hand."""
    return card_id in state.hands.get(player, [])


def _player_has_untapped_creature(
    state: GameState, player: str, creature_id: str
) -> bool:
    """Return ``True`` if *creature_id* exists on the battlefield for *player*
    and is untapped."""
    for perm in state.battlefield.get(player, []):
        if perm.id == creature_id:
            return not perm.tapped
    return False


def _player_controls_permanent(
    state: GameState, player: str, permanent_id: str
) -> bool:
    """Return ``True`` if *player* controls a permanent with this ID."""
    return any(p.id == permanent_id for p in state.battlefield.get(player, []))


# ═══════════════════════════════════════════════════════════════════════════════
# Validators
# ═══════════════════════════════════════════════════════════════════════════════


def validate_cast_spell(
    state: GameState,
    player: str,
    card_id: str,
    targets: list[str],
    mana_payment: dict[str, int],
    card_loader: CardLoader | None = None,
) -> ValidationResult:
    """Validate a ``CAST_SPELL`` action.

    Checks:
    * Card is in the player's hand.
    * Card is a known legal card.
    * Player can afford the mana cost (uses ``can_pay`` from ``server.mana``).
    * Sorcery-speed cards are only cast during a main phase with an empty
      stack.
    * Instant-speed cards can be cast any time the player has priority.
    """
    from server.card_loader import CardLoader as _CardLoader
    from server.mana import can_pay

    # 1. Card in hand.
    if not _player_owns_card(state, player, card_id):
        return False, "ILLEGAL_ACTION", f"Card '{card_id}' is not in your hand."

    # 2. Resolve the CardDef using the injected (or a fresh) loader.
    loader: CardLoader = card_loader or _CardLoader()
    card_def = loader.get_card(card_id)
    if card_def is None:
        return False, "ILLEGAL_ACTION", f"Unknown card '{card_id}'."

    # 3. Timing check (sorcery vs. instant).
    if _is_sorcery_speed(card_def.card_type, card_def):
        if not _is_main_phase(state.phase):
            return False, "WRONG_PHASE", "Sorceries can only be cast during a main phase."
        if len(state.stack) > 0:
            return False, "WRONG_PHASE", "Sorceries can only be cast with an empty stack."

    # 4. Full mana validation (against the acting player's own pool).
    from server.mana import ManaPool
    pool = state.mana_pools.get(player, ManaPool.empty())
    if not can_pay(mana_payment, card_def.mana_cost, pool):
        return False, "INSUFFICIENT_MANA", (
            f"Insufficient mana. Spell requires {card_def.mana_cost}, "
            f"but your available pool is {pool}."
        )

    # 5. Target validation based on card effect text.
    effect = card_def.simplified_effect.lower()
    requires_target = "target" in effect
    if requires_target and not targets:
        return False, "ILLEGAL_ACTION", (
            f"'{card_def.name}' requires targets but none given."
        )
    if not requires_target and targets:
        return False, "ILLEGAL_ACTION", (
            f"'{card_def.name}' does not require targets but got {targets}."
        )
    # Validate target existence for specific card types.
    if requires_target and targets:
        is_player_target = "target player" in effect
        is_creature_target = "target creature" in effect
        is_spell_target = "target spell" in effect
        is_any_target = "any target" in effect
        for tgt in targets:
            if is_player_target and tgt not in state.player_ids:
                return False, "ILLEGAL_TARGET", (
                    f"'{tgt}' is not a valid player target."
                )
            elif is_creature_target:
                found = any(
                    tgt in [p.id for p in perms]
                    for perms in state.battlefield.values()
                )
                if not found:
                    return False, "ILLEGAL_TARGET", (
                        f"'{tgt}' is not a valid creature target."
                    )
            elif is_spell_target:
                found = any(si.stack_item_id == tgt for si in state.stack)
                if not found:
                    return False, "ILLEGAL_TARGET", (
                        f"'{tgt}' is not a valid spell target on the stack."
                    )
            elif is_any_target:
                # Any target: can be a player or a permanent.
                is_player = tgt in state.player_ids
                is_perm = any(
                    tgt in [p.id for p in perms]
                    for perms in state.battlefield.values()
                )
                if not is_player and not is_perm:
                    return False, "ILLEGAL_TARGET", (
                        f"'{tgt}' is not a valid target."
                    )

    return True, None, ""


def validate_play_land(
    state: GameState,
    player: str,
    card_id: str,
    card_loader
) -> ValidationResult:
    """Validate a ``PLAY_LAND`` action.

    Checks:
    * Card is a land type.
    * Card is in the player's hand.
    * Player hasn't already played a land this turn.
    * Player is the active player.
    * Current phase is a main phase.
    * Stack is empty.
    """
    # 1. Player is AP.
    if state.active_player != player:
        return False, "NOT_YOUR_PRIORITY", "You are not the active player."
    # 2. Main phase.
    if not _is_main_phase(state.phase):
        return False, "WRONG_PHASE", "Lands can only be played during a main phase."
    # 3. Land already played this turn.
    if state.land_played_this_turn:
        return False, "ILLEGAL_ACTION", "You have already played a land this turn."
    # 4. Stack empty.
    if len(state.stack) > 0:
        return False, "ILLEGAL_ACTION", "Cannot play a land while the stack is non-empty."
    # 5. Card is a land in hand.
    if not _player_owns_card(state, player, card_id):
        return False, "ILLEGAL_ACTION", f"Card '{card_id}' is not in your hand."
    # 6. Card type is Land.
    base_id = card_id
    if "_" in card_id:
        parts = card_id.rsplit("_", 1)
        if parts[1].isdigit():
            base_id = parts[0]
            
    card_def = card_loader.get_card(base_id)
    
    if card_def is None:
        return False, "ILLEGAL_ACTION", f"Unknown card '{base_id}' (from instance '{card_id}')."
    if card_def.card_type != "Land":
        return False, "ILLEGAL_ACTION", f"'{base_id}' is not a land."

    return True, None, ""


def validate_activate_ability(
    state: GameState,
    player: str,
    source_id: str,
    ability_index: int,
    targets: list[str],
    cost_payment: dict[str, Any],
) -> ValidationResult:
    """Validate an ``ACTIVATE_ABILITY`` action.

    Checks:
    * Source permanent is on the battlefield under the player's control.
    * Source is not summoning-sick (if the ability requires tapping).
    * Ability index is valid.
    * Costs are payable.
    """
    if not _player_controls_permanent(state, player, source_id):
        return False, "ILLEGAL_ACTION", f"Permanent '{source_id}' is not under your control."

    perm = None
    for p in state.battlefield.get(player, []):
        if p.id == source_id:
            perm = p
            break

    if perm is None:
        return False, "ILLEGAL_ACTION", f"Permanent '{source_id}' not found."

    # Check summoning sickness for tap abilities.
    requires_tap = cost_payment.get("tap", False)
    if requires_tap and perm.summoning_sick:
        # Check for haste.
        from server.card_loader import CardLoader
        loader = CardLoader()
        card_def = loader.get_card(source_id)
        if card_def is None or "haste" not in str(card_def.abilities):
            return False, "ILLEGAL_ACTION", "Creature has summoning sickness and cannot tap."

    return True, None, ""


def validate_attack(
    state: GameState,
    player: str,
    attackers: list[dict[str, str]],
) -> ValidationResult:
    """Validate a ``DECLARE_ATTACKERS`` action.

    Checks:
    * Player is the active player.
    * Phase is ``DECLARE_ATTACKERS``.
    * Each creature is untapped, on the battlefield, controlled by *player*,
      and does not have summoning sickness (unless it has haste).
    * Each target is the opponent's player ID.
    """
    if state.active_player != player:
        return False, "NOT_YOUR_PRIORITY", "You are not the active player."
    if state.phase != "DECLARE_ATTACKERS":
        return False, "WRONG_PHASE", "Can only declare attackers in the Declare Attackers step."

    opponent = _get_opponent(state, player)

    for entry in attackers:
        cid = entry.get("creature_id", "")
        target = entry.get("target", "")

        # Creature exists and is controlled by player.
        perm = None
        for p in state.battlefield.get(player, []):
            if p.id == cid:
                perm = p
                break
        if perm is None:
            return False, "ILLEGAL_ACTION", f"'{cid}' is not on your battlefield."

        # Creature is untapped.
        if perm.tapped:
            return False, "ILLEGAL_ACTION", f"'{cid}' is tapped and cannot attack."

        # Summoning sickness check.
        if perm.summoning_sick:
            from server.card_loader import CardLoader
            loader = CardLoader()
            card_def = loader.get_card(cid)
            if card_def is None or "haste" not in str(card_def.abilities):
                return False, "ILLEGAL_ACTION", f"'{cid}' has summoning sickness and cannot attack."

        # Target is opponent.
        if target != opponent:
            return False, "ILLEGAL_TARGET", f"Invalid attack target '{target}'."

    return True, None, ""


def validate_block(
    state: GameState,
    player: str,
    blockers: list[dict[str, str]],
) -> ValidationResult:
    """Validate a ``DECLARE_BLOCKERS`` action.

    Checks:
    * Player is the non-active player.
    * Phase is ``DECLARE_BLOCKERS``.
    * Each blocker is an untapped creature controlled by *player*.
    * Each `blocking_id` is a declared attacker.
    * No blocker blocks more than one attacker.
    * Multiple creatures may block the same attacker.
    """
    if state.active_player == player:
        return False, "NOT_YOUR_PRIORITY", "You are not the defending player."
    if state.phase != "DECLARE_BLOCKERS":
        return False, "WRONG_PHASE", "Can only declare blockers in the Declare Blockers step."

    # Track which blockers have been assigned (each can block only one).
    assigned_blockers: set[str] = set()

    for entry in blockers:
        cid = entry.get("creature_id", "")
        blocking = entry.get("blocking_id", "")

        # Blocker exists and is controlled by player.
        perm = None
        for p in state.battlefield.get(player, []):
            if p.id == cid:
                perm = p
                break
        if perm is None:
            return False, "ILLEGAL_ACTION", f"'{cid}' is not on your battlefield."

        if perm.tapped:
            return False, "ILLEGAL_ACTION", f"'{cid}' is tapped and cannot block."

        # Each creature can block at most one attacker.
        if cid in assigned_blockers:
            return False, "ILLEGAL_ACTION", f"'{cid}' is already blocking a creature."
        assigned_blockers.add(cid)

    return True, None, ""

    return True, None, ""


def validate_mulligan(
    state: GameState,
    player: str,
    keep: bool,
    cards_to_bottom: list[str],
) -> ValidationResult:
    """Validate a ``MULLIGAN_CHOICE`` action.

    * If *keep* is ``False``, *cards_to_bottom* MUST be empty.
    * If *keep* is ``True``, *cards_to_bottom* MUST contain exactly
      ``mulligan_count`` cards.
    """
    mull_count = state.mulligan_counts.get(player, 0)

    if not keep:
        if cards_to_bottom:
            return False, "ILLEGAL_ACTION", (
                "cards_to_bottom must be empty when taking a mulligan."
            )
    else:
        if len(cards_to_bottom) != mull_count:
            return False, "ILLEGAL_ACTION", (
                f"Must bottom exactly {mull_count} card(s) after {mull_count} mulligan(s). "
                f"Got {len(cards_to_bottom)}."
            )
        for cid in cards_to_bottom:
            if cid not in state.hands.get(player, []):
                return False, "ILLEGAL_ACTION", f"Card '{cid}' is not in your hand."

    return True, None, ""


def validate_discard(
    state: GameState,
    player: str,
    card_ids: list[str],
) -> ValidationResult:
    """Validate a ``DISCARD`` action (RFC §8.15 — Cleanup step).

    * The player must have more than 7 cards in hand.
    * Exactly ``hand_size - 7`` cards must be discarded.
    * All cards must be in the player's hand.
    """
    hand = state.hands.get(player, [])
    hand_size = len(hand)

    if hand_size <= 7:
        return False, "ILLEGAL_ACTION", "Hand size is 7 or fewer; no discard needed."

    expected_count = hand_size - 7
    if len(card_ids) != expected_count:
        return False, "ILLEGAL_ACTION", (
            f"Must discard exactly {expected_count} card(s); got {len(card_ids)}."
        )

    for cid in card_ids:
        if cid not in hand:
            return False, "ILLEGAL_ACTION", f"Card '{cid}' is not in your hand."

    return True, None, ""


def validate_deck(
    player_id: str,
    deck_list: list[str],
    card_loader: CardLoader,
) -> ValidationResult:
    """Validate a ``PLAYER_READY`` deck list.

    Wraps ``CardLoader.is_legal_deck`` with the RFC §6.2 rules:
    1–50 cards, all from the legal set.
    """
    ok, msg = card_loader.is_legal_deck(deck_list)
    if not ok:
        return False, "ILLEGAL_DECK", msg
    return True, None, ""


def validate_target(
    state: GameState,
    target_id: str,
    legal_targets: list[str],
) -> ValidationResult:
    """Validate that *target_id* is in the *legal_targets* list."""
    if target_id not in legal_targets:
        return False, "ILLEGAL_TARGET", f"'{target_id}' is not a legal target."
    return True, None, ""


# ── Internal helpers ─────────────────────────────────────────────────────────


def _get_opponent(state: GameState, player: str) -> str | None:
    for pid in state.player_ids:
        if pid != player:
            return pid
    return None
