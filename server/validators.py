# server validators py action validation stuff
# check actions before changing game state or send error

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from shared.constants import IN_GAME_PHASES, PRIORITY_BEARING_TYPES

if TYPE_CHECKING:
    from server.card_loader import CardLoader
    from server.game_state import GameState


# type alias for validation result

ValidationResult = tuple[bool, str | None, str]
"""``(ok, error_code, message)`` where *ok* is ``True`` on success."""


# helper stuff
def _is_sorcery_speed(card_type: str, card_def: Any) -> bool:
    """Return ``True`` if the card can only be cast at sorcery speed."""
    if card_type == "Sorcery":
        return True
    # check if card is sorcery
    return False

# check if main phase
def _is_main_phase(phase: str) -> bool:
    """Return ``True`` if *phase* is a main phase."""
    return phase in ("PRECOMBAT_MAIN", "POSTCOMBAT_MAIN")

# check if its an instant
def _is_instant_speed(card_type: str) -> bool:
    """Return ``True`` if the card type can be cast at instant speed."""
    return card_type in ("Instant",) or card_type == "Instant"

# see if player actually has the card
def _player_owns_card(state: GameState, player: str, card_id: str) -> bool:
    """Return ``True`` if *card_id* is in *player*'s hand."""
    return card_id in state.hands.get(player, [])

# see if creature is on field and not tapped
def _player_has_untapped_creature(
    state: GameState, player: str, creature_id: str
) -> bool:
    """Return ``True`` if *creature_id* exists on the battlefield for *player*
    and is untapped."""
    for perm in state.battlefield.get(player, []):
        if perm.id == creature_id:
            return not perm.tapped
    return False

# see if player owns the thing
def _player_controls_permanent(
    state: GameState, player: str, permanent_id: str
) -> bool:
    """Return ``True`` if *player* controls a permanent with this ID."""
    return any(p.id == permanent_id for p in state.battlefield.get(player, []))


# actual validation functions
# target rules for specific cards so they dont break
_CARD_TARGET_RULES: dict[str, dict] = {
    "naturalize": {"types": ("artifact", "enchantment")},
    "terror": {"types": ("creature",), "not_color": ("B",),
               "not_types": ("artifact",)},
    "doom_blade": {"types": ("creature",), "not_color": ("B",)},
    "negate": {"zone": "spell", "not_types": ("creature",)},
    "raise_dead": {"zone": "graveyard", "types": ("creature",)},
    "gravedigger": {"zone": "graveyard", "types": ("creature",)},
}


def _find_perm_anywhere(state: GameState, permanent_id: str):
    # look everywhere for the card
    for perms in state.battlefield.values():
        for perm in perms:
            if perm.id == permanent_id:
                return perm
    return None


def _strip_instance_suffix(card_id: str) -> str:
    # remove numbers at the end of card id
    if "_" in card_id:
        parts = card_id.rsplit("_", 1)
        if parts[1].isdigit():
            return parts[0]
    return card_id

# check if targets are valid
def _check_specific_targets(
    state: GameState,
    rule: dict,
    targets: list[str],
    loader: CardLoader,
) -> ValidationResult:
    # make sure types and colors match
    zone = rule.get("zone", "battlefield")
    for tgt in targets:
        card = None
        if zone == "battlefield":
            perm = _find_perm_anywhere(state, tgt)
            if perm is None:
                return False, "ILLEGAL_TARGET", (
                    f"'{tgt}' is not a valid permanent target."
                )
            if loader is not None:
                card = loader.get_card(getattr(perm, "card_def_id", ""))
        elif zone == "graveyard":
            in_gy = any(tgt in gy for gy in state.graveyards.values())
            if not in_gy:
                return False, "ILLEGAL_TARGET", (
                    f"'{tgt}' is not in a graveyard."
                )
            if loader is not None:
                card = loader.get_card(_strip_instance_suffix(tgt))
        elif zone == "spell":
            si = next((s for s in state.stack
                       if getattr(s, "stack_item_id", "") == tgt), None)
            if si is None:
                return False, "ILLEGAL_TARGET", (
                    f"'{tgt}' is not a spell on the stack."
                )
            if loader is not None:
                card = loader.get_card(
                    _strip_instance_suffix(getattr(si, "source", ""))
                )

        if card is not None:
            ctype = (getattr(card, "card_type", "") or "").lower()
            # check type
            if rule.get("types") and not any(
                req in ctype for req in rule["types"]
            ):
                return False, "ILLEGAL_TARGET", (
                    f"'{tgt}' does not satisfy the target requirement "
                    f"({' or '.join(rule['types'])})."
                )
            for ntype in rule.get("not_types", ()):
                if ntype in ctype:
                    return False, "ILLEGAL_TARGET", (
                        f"'{tgt}' is not a valid target."
                    )
            color = getattr(card, "color", "") or ""
            if color in rule.get("not_color", ()):
                return False, "ILLEGAL_TARGET", (
                    f"'{tgt}' is not a valid target (colour restriction)."
                )
    return True, None, ""

# validate casting a spell
def validate_cast_spell(
    state: GameState,
    player: str,
    card_id: str,
    targets: list[str],
    mana_payment: dict[str, int],
    card_loader: CardLoader | None = None,
) -> ValidationResult:
    # checks hand mana timing and targets
    from server.card_loader import CardLoader as _CardLoader
    from server.mana import can_pay

    # 1 check hand
    if not _player_owns_card(state, player, card_id):
        return False, "ILLEGAL_ACTION", f"Card '{card_id}' is not in your hand."

    # 2 load card
    loader: CardLoader = card_loader or _CardLoader()
    card_def = loader.get_card(card_id)
    if card_def is None:
        return False, "ILLEGAL_ACTION", f"Unknown card '{card_id}'."

    # 3 timing checks
    if _is_sorcery_speed(card_def.card_type, card_def):
        if not _is_main_phase(state.phase):
            return False, "WRONG_PHASE", "Sorceries can only be cast during a main phase."
        if len(state.stack) > 0:
            return False, "WRONG_PHASE", "Sorceries can only be cast with an empty stack."

    # 4 check mana
    from server.mana import ManaPool
    pool = state.mana_pools.get(player, ManaPool.empty())
    if not can_pay(mana_payment, card_def.mana_cost, pool):
        return False, "INSUFFICIENT_MANA", (
            f"Insufficient mana. Spell requires {card_def.mana_cost}, "
            f"but your available pool is {pool}."
        )

    # 5 target stuff
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

    base_id = getattr(card_def, "card_id_base", "") or card_id
    rule = _CARD_TARGET_RULES.get(base_id)

    # check card targets
    if rule is not None:
        ok, code, msg = _check_specific_targets(state, rule, targets, loader)
        if not ok:
            return False, code, msg
    elif requires_target and targets:
        # normal checks
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
                # check if anything
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
    # validate land playing
	# check active player phase land count stack and if u have the land

    # 1 check active player
    if state.active_player != player:
        return False, "NOT_YOUR_PRIORITY", "You are not the active player."
    # 2 check main phase
    if not _is_main_phase(state.phase):
        return False, "WRONG_PHASE", "Lands can only be played during a main phase."
    # 3 check land count
    if state.land_played_this_turn:
        return False, "ILLEGAL_ACTION", "You have already played a land this turn."
    # 4 check stack
    if len(state.stack) > 0:
        return False, "ILLEGAL_ACTION", "Cannot play a land while the stack is non-empty."
    # 5 check hand
    if not _player_owns_card(state, player, card_id):
        return False, "ILLEGAL_ACTION", f"Card '{card_id}' is not in your hand."
    # 6 check if actual land
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
    # validate ability activation
    if not _player_controls_permanent(state, player, source_id):
        return False, "ILLEGAL_ACTION", f"Permanent '{source_id}' is not under your control."

    perm = None
    for p in state.battlefield.get(player, []):
        if p.id == source_id:
            perm = p
            break

    if perm is None:
        return False, "ILLEGAL_ACTION", f"Permanent '{source_id}' not found."

    # check if card is urs and not sick if tap needed
    requires_tap = cost_payment.get("tap", False)
    if requires_tap and perm.summoning_sick:
        # check haste if sick
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
    # validate attackers
	# check active player phase untapped sick and target
    if state.active_player != player:
        return False, "NOT_YOUR_PRIORITY", "You are not the active player."
    if state.phase != "DECLARE_ATTACKERS":
        return False, "WRONG_PHASE", "Can only declare attackers in the Declare Attackers step."

    opponent = _get_opponent(state, player)

    for entry in attackers:
        cid = entry.get("creature_id", "")
        target = entry.get("target", "")

        # creature check
        perm = None
        for p in state.battlefield.get(player, []):
            if p.id == cid:
                perm = p
                break
        if perm is None:
            return False, "ILLEGAL_ACTION", f"'{cid}' is not on your battlefield."

        # tap check
        if perm.tapped:
            return False, "ILLEGAL_ACTION", f"'{cid}' is tapped and cannot attack."

        # sick check
        if perm.summoning_sick:
            from server.card_loader import CardLoader
            loader = CardLoader()
            card_def = loader.get_card(cid)
            if card_def is None or "haste" not in str(card_def.abilities):
                return False, "ILLEGAL_ACTION", f"'{cid}' has summoning sickness and cannot attack."

        # target check
        if target != opponent:
            return False, "ILLEGAL_TARGET", f"Invalid attack target '{target}'."

    return True, None, ""


def validate_block(
    state: GameState,
    player: str,
    blockers: list[dict[str, str]],
) -> ValidationResult:
    # validate blockers
	# check non active player phase untapped and 1 block per creature

    if state.active_player == player:
        return False, "NOT_YOUR_PRIORITY", "You are not the defending player."
    if state.phase != "DECLARE_BLOCKERS":
        return False, "WRONG_PHASE", "Can only declare blockers in the Declare Blockers step."

    # track assigned blockers
    assigned_blockers: set[str] = set()

    for entry in blockers:
        cid = entry.get("creature_id", "")
        blocking = entry.get("blocking_id", "")

        # blocker check
        perm = None
        for p in state.battlefield.get(player, []):
            if p.id == cid:
                perm = p
                break
        if perm is None:
            return False, "ILLEGAL_ACTION", f"'{cid}' is not on your battlefield."

		# tap check
        if perm.tapped:
            return False, "ILLEGAL_ACTION", f"'{cid}' is tapped and cannot block."

        # 1 block limit check
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
    # validate mulligan choice
	# check keep or bottom rules based on mulligan count
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
    # validate cleanup step discard
	# check hand size and make sure discarded cards are in hand
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
    # validate player ready deck list
	# check deck legal rules like 1-50 cards
    ok, msg = card_loader.is_legal_deck(deck_list)
    if not ok:
        return False, "ILLEGAL_DECK", msg
    return True, None, ""


def validate_target(
    state: GameState,
    target_id: str,
    legal_targets: list[str],
) -> ValidationResult:
    # validate target
	# check if target in legal targets list
    if target_id not in legal_targets:
        return False, "ILLEGAL_TARGET", f"'{target_id}' is not a legal target."
    return True, None, ""


# Internal helpers 

# get opponent id
def _get_opponent(state: GameState, player: str) -> str | None:
    for pid in state.player_ids:
        if pid != player:
            return pid
    return None
