"""
shared/pdus.py — PDU Definitions & Factory Functions (Module 01: Network Protocol)

Provides the type registry, factory functions, and validation helpers for all
25 MTGNP 1.0 PDU types (RFC §10).

Every PDU in the system is created via one of the ``create_<type>(...)``
factories in this module.  This guarantees that PDU dicts always contain the
correct field names and types required by the protocol.
"""

from __future__ import annotations

from typing import Any

from shared.constants import (
    ALL_PDU_TYPES,
    C2S_PDU_TYPES,
    PRIORITY_BEARING_TYPES,
    S2C_PDU_TYPES,
)
from shared.framing import ProtocolError

# ── PDU Type Registry ────────────────────────────────────────────────────────

# Each entry: {"direction": "C2S" | "S2C", "priority_bearing": bool,
#              "required_fields": [list of field names that MUST be present]}

PDU_TYPES: dict[str, dict[str, Any]] = {
    # ── Client-to-Server ────────────────────────────────────────────────
    "PLAYER_READY": {
        "direction": "C2S",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "player_id", "deck_list"],
    },
    "MULLIGAN_CHOICE": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "keep", "cards_to_bottom"],
    },
    "PRIORITY_PASS": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num"],
    },
    "CAST_SPELL": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "card_id", "targets", "mana_payment"],
    },
    "ACTIVATE_ABILITY": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": [
            "type", "seq_num", "source_id", "ability_index", "targets", "cost_payment",
        ],
    },
    "PLAY_LAND": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "card_id"],
    },
    "DECLARE_ATTACKERS": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "attackers"],
    },
    "DECLARE_BLOCKERS": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "blockers"],
    },
    "ASSIGN_DAMAGE_ORDER": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "attacker_id", "blocker_order"],
    },
    "DISCARD": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "card_ids"],
    },
    "TRIGGER_ORDER_RESPONSE": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "ordered_trigger_ids"],
    },
    "TRIGGER_CHOICE_RESPONSE": {
        "direction": "C2S",
        "priority_bearing": True,
        "required_fields": ["type", "seq_num", "trigger_id", "accept"],
    },
    "CONCEDE": {
        "direction": "C2S",
        "priority_bearing": False,  # special — echoes most recent server PDU
        "required_fields": ["type", "seq_num", "player_id"],
    },
    "PING": {
        "direction": "C2S",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "timestamp"],
    },
    # ── Server-to-Client ────────────────────────────────────────────────
    "GAME_STATE_UPDATE": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "state"],
    },
    "PHASE_TRANSITION": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": [
            "type", "seq_num", "from_phase", "to_phase", "active_player", "turn",
        ],
    },
    "PRIORITY_GRANT": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "player_id", "time_limit_ms"],
    },
    "STACK_PUSH": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": [
            "type", "seq_num", "stack_item_id", "item_type",
            "source", "targets", "controller",
        ],
    },
    "STACK_RESOLVE": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "stack_item_id", "result", "state_changes"],
    },
    "TRIGGER_ORDER": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "player_id", "trigger_ids"],
    },
    "TRIGGER_CHOICE": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": [
            "type", "seq_num", "trigger_id", "source_id",
            "effect_summary", "requires_target", "legal_targets",
        ],
    },
    "COMBAT_DAMAGE_RESULT": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": [
            "type", "seq_num", "damage_events", "life_totals", "creatures_died",
        ],
    },
    "GAME_OVER": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "winner_id", "loser_id", "reason"],
    },
    "ERROR": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "code", "message", "rejected_action"],
    },
    "PONG": {
        "direction": "S2C",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "timestamp"],
    },
}


# ── Factory Functions ────────────────────────────────────────────────────────

# C2S factories


def create_player_ready(
    *, seq_num: int, player_id: str, deck_list: list[str]
) -> dict[str, Any]:
    """Build a PLAYER_READY PDU (RFC §10.2.1)."""
    return {
        "type": "PLAYER_READY",
        "seq_num": seq_num,
        "player_id": player_id,
        "deck_list": deck_list,
    }


def create_mulligan_choice(
    *, seq_num: int, keep: bool, cards_to_bottom: list[str]
) -> dict[str, Any]:
    """Build a MULLIGAN_CHOICE PDU (RFC §10.2.3)."""
    return {
        "type": "MULLIGAN_CHOICE",
        "seq_num": seq_num,
        "keep": keep,
        "cards_to_bottom": cards_to_bottom,
    }


def create_priority_pass(*, seq_num: int) -> dict[str, Any]:
    """Build a PRIORITY_PASS PDU (RFC §10.2.6)."""
    return {"type": "PRIORITY_PASS", "seq_num": seq_num}


def create_cast_spell(
    *,
    seq_num: int,
    card_id: str,
    targets: list[str],
    mana_payment: dict[str, int],
) -> dict[str, Any]:
    """Build a CAST_SPELL PDU (RFC §10.2.7)."""
    return {
        "type": "CAST_SPELL",
        "seq_num": seq_num,
        "card_id": card_id,
        "targets": targets,
        "mana_payment": mana_payment,
    }


def create_activate_ability(
    *,
    seq_num: int,
    source_id: str,
    ability_index: int,
    targets: list[str],
    cost_payment: dict[str, Any],
) -> dict[str, Any]:
    """Build an ACTIVATE_ABILITY PDU (RFC §10.2.8)."""
    return {
        "type": "ACTIVATE_ABILITY",
        "seq_num": seq_num,
        "source_id": source_id,
        "ability_index": ability_index,
        "targets": targets,
        "cost_payment": cost_payment,
    }


def create_play_land(*, seq_num: int, card_id: str) -> dict[str, Any]:
    """Build a PLAY_LAND PDU (RFC §10.2.19)."""
    return {"type": "PLAY_LAND", "seq_num": seq_num, "card_id": card_id}


def create_declare_attackers(
    *, seq_num: int, attackers: list[dict[str, str]]
) -> dict[str, Any]:
    """Build a DECLARE_ATTACKERS PDU (RFC §10.2.15).

    Each attacker entry: {"creature_id": str, "target": str}
    """
    return {
        "type": "DECLARE_ATTACKERS",
        "seq_num": seq_num,
        "attackers": attackers,
    }


def create_declare_blockers(
    *, seq_num: int, blockers: list[dict[str, str]]
) -> dict[str, Any]:
    """Build a DECLARE_BLOCKERS PDU (RFC §10.2.16).

    Each blocker entry: {"creature_id": str, "blocking_id": str}
    """
    return {
        "type": "DECLARE_BLOCKERS",
        "seq_num": seq_num,
        "blockers": blockers,
    }


def create_assign_damage_order(
    *, seq_num: int, attacker_id: str, blocker_order: list[str]
) -> dict[str, Any]:
    """Build an ASSIGN_DAMAGE_ORDER PDU (RFC §10.2.17)."""
    return {
        "type": "ASSIGN_DAMAGE_ORDER",
        "seq_num": seq_num,
        "attacker_id": attacker_id,
        "blocker_order": blocker_order,
    }


def create_discard(
    *, seq_num: int, card_ids: list[str]
) -> dict[str, Any]:
    """Build a DISCARD PDU (RFC §10.2.20)."""
    return {
        "type": "DISCARD",
        "seq_num": seq_num,
        "card_ids": card_ids,
    }


def create_trigger_order_response(
    *, seq_num: int, ordered_trigger_ids: list[str]
) -> dict[str, Any]:
    """Build a TRIGGER_ORDER_RESPONSE PDU (RFC §10.2.11)."""
    return {
        "type": "TRIGGER_ORDER_RESPONSE",
        "seq_num": seq_num,
        "ordered_trigger_ids": ordered_trigger_ids,
    }


def create_trigger_choice_response(
    *,
    seq_num: int,
    trigger_id: str,
    accept: bool,
    chosen_target: str | None = None,
) -> dict[str, Any]:
    """Build a TRIGGER_CHOICE_RESPONSE PDU (RFC §10.2.13).

    If *accept* is True and the server indicated *requires_target=True*,
    then *chosen_target* must be a valid player_id or permanent id.
    """
    result: dict[str, Any] = {
        "type": "TRIGGER_CHOICE_RESPONSE",
        "seq_num": seq_num,
        "trigger_id": trigger_id,
        "accept": accept,
    }
    if chosen_target is not None:
        result["chosen_target"] = chosen_target
    return result


def create_concede(*, seq_num: int, player_id: str) -> dict[str, Any]:
    """Build a CONCEDE PDU (RFC §10.2.21)."""
    return {
        "type": "CONCEDE",
        "seq_num": seq_num,
        "player_id": player_id,
    }


def create_ping(*, seq_num: int, timestamp: int) -> dict[str, Any]:
    """Build a PING PDU (RFC §10.2.24)."""
    return {
        "type": "PING",
        "seq_num": seq_num,
        "timestamp": timestamp,
    }


# S2C factories


def create_game_state_update(
    *, seq_num: int, state: dict[str, Any]
) -> dict[str, Any]:
    """Build a GAME_STATE_UPDATE PDU (RFC §10.2.2)."""
    return {"type": "GAME_STATE_UPDATE", "seq_num": seq_num, "state": state}


def create_phase_transition(
    *,
    seq_num: int,
    from_phase: str,
    to_phase: str,
    active_player: str,
    turn: int,
) -> dict[str, Any]:
    """Build a PHASE_TRANSITION PDU (RFC §10.2.4)."""
    return {
        "type": "PHASE_TRANSITION",
        "seq_num": seq_num,
        "from_phase": from_phase,
        "to_phase": to_phase,
        "active_player": active_player,
        "turn": turn,
    }


def create_priority_grant(
    *, seq_num: int, player_id: str, time_limit_ms: int
) -> dict[str, Any]:
    """Build a PRIORITY_GRANT PDU (RFC §10.2.5)."""
    return {
        "type": "PRIORITY_GRANT",
        "seq_num": seq_num,
        "player_id": player_id,
        "time_limit_ms": time_limit_ms,
    }


def create_stack_push(
    *,
    seq_num: int,
    stack_item_id: str,
    item_type: str,
    source: str,
    targets: list[str],
    controller: str,
) -> dict[str, Any]:
    """Build a STACK_PUSH PDU (RFC §10.2.9)."""
    return {
        "type": "STACK_PUSH",
        "seq_num": seq_num,
        "stack_item_id": stack_item_id,
        "item_type": item_type,
        "source": source,
        "targets": targets,
        "controller": controller,
    }


def create_stack_resolve(
    *,
    seq_num: int,
    stack_item_id: str,
    result: str,
    state_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a STACK_RESOLVE PDU (RFC §10.2.14)."""
    return {
        "type": "STACK_RESOLVE",
        "seq_num": seq_num,
        "stack_item_id": stack_item_id,
        "result": result,
        "state_changes": state_changes,
    }


def create_trigger_order(
    *, seq_num: int, player_id: str, trigger_ids: list[str]
) -> dict[str, Any]:
    """Build a TRIGGER_ORDER PDU (RFC §10.2.10)."""
    return {
        "type": "TRIGGER_ORDER",
        "seq_num": seq_num,
        "player_id": player_id,
        "trigger_ids": trigger_ids,
    }


def create_trigger_choice(
    *,
    seq_num: int,
    trigger_id: str,
    source_id: str,
    effect_summary: str,
    requires_target: bool = False,
    legal_targets: list[str] | None = None,
) -> dict[str, Any]:
    """Build a TRIGGER_CHOICE PDU (RFC §10.2.12)."""
    return {
        "type": "TRIGGER_CHOICE",
        "seq_num": seq_num,
        "trigger_id": trigger_id,
        "source_id": source_id,
        "effect_summary": effect_summary,
        "requires_target": requires_target,
        "legal_targets": legal_targets or [],
    }


def create_combat_damage_result(
    *,
    seq_num: int,
    damage_events: list[dict[str, Any]],
    life_totals: dict[str, int],
    creatures_died: list[str],
) -> dict[str, Any]:
    """Build a COMBAT_DAMAGE_RESULT PDU (RFC §10.2.18)."""
    return {
        "type": "COMBAT_DAMAGE_RESULT",
        "seq_num": seq_num,
        "damage_events": damage_events,
        "life_totals": life_totals,
        "creatures_died": creatures_died,
    }


def create_game_over(
    *,
    seq_num: int,
    winner_id: str,
    loser_id: str,
    reason: str,
) -> dict[str, Any]:
    """Build a GAME_OVER PDU (RFC §10.2.22).

    *reason* must be one of: LIFE_ZERO, DECK_EMPTY, CONCEDE, DISCONNECT.
    """
    return {
        "type": "GAME_OVER",
        "seq_num": seq_num,
        "winner_id": winner_id,
        "loser_id": loser_id,
        "reason": reason,
    }


def create_error(
    *,
    seq_num: int,
    code: str,
    message: str,
    rejected_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build an ERROR PDU (RFC §10.2.23).

    *rejected_action* should be a copy of the PDU that was rejected, or an
    empty dict if the original PDU could not be parsed.
    """
    return {
        "type": "ERROR",
        "seq_num": seq_num,
        "code": code,
        "message": message,
        "rejected_action": rejected_action or {},
    }


def create_pong(*, seq_num: int, timestamp: int) -> dict[str, Any]:
    """Build a PONG PDU (RFC §10.2.25).

    *seq_num* and *timestamp* are copied from the PING being replied to.
    """
    return {
        "type": "PONG",
        "seq_num": seq_num,
        "timestamp": timestamp,
    }


# ── Validation Helpers ───────────────────────────────────────────────────────


def validate_pdu_type(pdu: dict[str, Any]) -> list[str]:
    """Check that ``pdu["type"]`` is a known PDU type string.

    Returns a list of error messages (empty = valid).
    """
    errors: list[str] = []
    pdu_type = pdu.get("type")
    if pdu_type is None:
        errors.append("Missing 'type' field.")
    elif pdu_type not in ALL_PDU_TYPES:
        errors.append(f"Unknown PDU type '{pdu_type}'.")
    elif pdu_type in C2S_PDU_TYPES:
        pass  # known C2S type
    else:
        pass  # known S2C type
    return errors


def validate_required_fields(pdu: dict[str, Any]) -> list[str]:
    """Check that all required fields for the PDU type are present.

    Returns a list of error messages (empty = valid).  Does *not* check
    field *types*, only presence.
    """
    errors: list[str] = []
    pdu_type = pdu.get("type")
    if pdu_type not in PDU_TYPES:
        # If the type is unknown we can't validate its fields — return
        # early so callers get a single clear error.
        return errors

    meta = PDU_TYPES[pdu_type]
    for field in meta["required_fields"]:
        if field == "type":
            continue  # already checked above
        if field not in pdu:
            errors.append(f"Missing required field '{field}' in {pdu_type} PDU.")
    return errors


def parse_and_validate(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    """Top-level parse-then-validate for an incoming PDU.

    Parameters
    ----------
    raw : dict
        The parsed JSON object from *decode_frame*.

    Returns
    -------
    (pdu, None) on success.
    (None, error_message) on failure — callers should send an ERROR PDU
    with the appropriate code.

    Notes
    -----
    - If the type is unknown → ``"UNKNOWN_TYPE"``.
    - If required fields are missing → ``"ILLEGAL_ACTION"``.
    """
    # 1. Check type exists
    pdu_type = raw.get("type")
    if pdu_type is None:
        return None, "INVALID_JSON: missing 'type' field."

    # 2. Check type is known
    if pdu_type not in ALL_PDU_TYPES:
        return None, f"UNKNOWN_TYPE: '{pdu_type}' is not a valid MTGNP PDU type."

    # 3. Check required fields
    missing = validate_required_fields(raw)
    if missing:
        return None, f"ILLEGAL_ACTION: {'; '.join(missing)}"

    return raw, None


def validate_seq_num(expected: int, actual: int) -> bool:
    """Return *True* iff *actual* equals *expected*.

    Used by the server to enforce the priority-token seq_num rule
    (RFC §7.3 and §10.1).
    """
    return expected == actual
