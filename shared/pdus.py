from __future__ import annotations

from typing import Any

from shared.constants import (
    ALL_PDU_TYPES,
    C2S_PDU_TYPES,
    PRIORITY_BEARING_TYPES,
    S2C_PDU_TYPES,
)
from shared.framing import ProtocolError

PDU_TYPES: dict[str, dict[str, Any]] = {
    # client to server PDUs
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
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "player_id"],
    },
    "PING": {
        "direction": "C2S",
        "priority_bearing": False,
        "required_fields": ["type", "seq_num", "timestamp"],
    },
    # server to client PDUs
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

# CLIENT TO SERVER

# build a player ready pdu
def create_player_ready(
    *, seq_num: int, player_id: str, deck_list: list[str]
) -> dict[str, Any]:
    return {
        "type": "PLAYER_READY",
        "seq_num": seq_num,
        "player_id": player_id,
        "deck_list": deck_list,
    }


# build a mulligan choice pdu
def create_mulligan_choice(
    *, seq_num: int, keep: bool, cards_to_bottom: list[str]
) -> dict[str, Any]:
    return {
        "type": "MULLIGAN_CHOICE",
        "seq_num": seq_num,
        "keep": keep,
        "cards_to_bottom": cards_to_bottom,
    }


# build a priority pass pdu
def create_priority_pass(*, seq_num: int) -> dict[str, Any]:
    return {"type": "PRIORITY_PASS", "seq_num": seq_num}


# build a cast spell pdu
def create_cast_spell(
    *,
    seq_num: int,
    card_id: str,
    targets: list[str],
    mana_payment: dict[str, int],
) -> dict[str, Any]:
    return {
        "type": "CAST_SPELL",
        "seq_num": seq_num,
        "card_id": card_id,
        "targets": targets,
        "mana_payment": mana_payment,
    }


# build an activated ability pdu
def create_activate_ability(
    *,
    seq_num: int,
    source_id: str,
    ability_index: int,
    targets: list[str],
    cost_payment: dict[str, Any],
) -> dict[str, Any]:
    return {
        "type": "ACTIVATE_ABILITY",
        "seq_num": seq_num,
        "source_id": source_id,
        "ability_index": ability_index,
        "targets": targets,
        "cost_payment": cost_payment,
    }


# build a play land pdu
def create_play_land(*, seq_num: int, card_id: str) -> dict[str, Any]:
    return {"type": "PLAY_LAND", "seq_num": seq_num, "card_id": card_id}


# build an attackers declaration pdu
def create_declare_attackers(
    *, seq_num: int, attackers: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "type": "DECLARE_ATTACKERS",
        "seq_num": seq_num,
        "attackers": attackers,
    }


# build a blockers declaration pdu
def create_declare_blockers(
    *, seq_num: int, blockers: list[dict[str, str]]
) -> dict[str, Any]:
    return {
        "type": "DECLARE_BLOCKERS",
        "seq_num": seq_num,
        "blockers": blockers,
    }


# build a damage order pdu
def create_assign_damage_order(
    *, seq_num: int, attacker_id: str, blocker_order: list[str]
) -> dict[str, Any]:
    return {
        "type": "ASSIGN_DAMAGE_ORDER",
        "seq_num": seq_num,
        "attacker_id": attacker_id,
        "blocker_order": blocker_order,
    }


# build a discard pdu
def create_discard(
    *, seq_num: int, card_ids: list[str]
) -> dict[str, Any]:
    return {
        "type": "DISCARD",
        "seq_num": seq_num,
        "card_ids": card_ids,
    }


# build a trigger order response pdu
def create_trigger_order_response(
    *, seq_num: int, ordered_trigger_ids: list[str]
) -> dict[str, Any]:
    return {
        "type": "TRIGGER_ORDER_RESPONSE",
        "seq_num": seq_num,
        "ordered_trigger_ids": ordered_trigger_ids,
    }


# build a trigger choice response pdu
def create_trigger_choice_response(
    *,
    seq_num: int,
    trigger_id: str,
    accept: bool,
    chosen_target: str | None = None,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "type": "TRIGGER_CHOICE_RESPONSE",
        "seq_num": seq_num,
        "trigger_id": trigger_id,
        "accept": accept,
    }
    if chosen_target is not None:
        result["chosen_target"] = chosen_target
    return result


# build a concede pdu
def create_concede(*, seq_num: int, player_id: str) -> dict[str, Any]:
    return {
        "type": "CONCEDE",
        "seq_num": seq_num,
        "player_id": player_id,
    }


# build a PING pdu
def create_ping(*, seq_num: int, timestamp: int) -> dict[str, Any]:
    return {
        "type": "PING",
        "seq_num": seq_num,
        "timestamp": timestamp,
    }


# SERVER TO CLIENT

# build a game state update pdu
def create_game_state_update(
    *, seq_num: int, state: dict[str, Any]
) -> dict[str, Any]:
    return {"type": "GAME_STATE_UPDATE", "seq_num": seq_num, "state": state}


# build a phase transition pdu
def create_phase_transition(
    *,
    seq_num: int,
    from_phase: str,
    to_phase: str,
    active_player: str,
    turn: int,
) -> dict[str, Any]:
    return {
        "type": "PHASE_TRANSITION",
        "seq_num": seq_num,
        "from_phase": from_phase,
        "to_phase": to_phase,
        "active_player": active_player,
        "turn": turn,
    }


# build a priority grant pdu
def create_priority_grant(
    *, seq_num: int, player_id: str, time_limit_ms: int
) -> dict[str, Any]:
    return {
        "type": "PRIORITY_GRANT",
        "seq_num": seq_num,
        "player_id": player_id,
        "time_limit_ms": time_limit_ms,
    }


# build a stack push pdu
def create_stack_push(
    *,
    seq_num: int,
    stack_item_id: str,
    item_type: str,
    source: str,
    targets: list[str],
    controller: str,
) -> dict[str, Any]:
    return {
        "type": "STACK_PUSH",
        "seq_num": seq_num,
        "stack_item_id": stack_item_id,
        "item_type": item_type,
        "source": source,
        "targets": targets,
        "controller": controller,
    }


# build a stack resolve pdu
def create_stack_resolve(
    *,
    seq_num: int,
    stack_item_id: str,
    result: str,
    state_changes: list[dict[str, Any]],
) -> dict[str, Any]:
    return {
        "type": "STACK_RESOLVE",
        "seq_num": seq_num,
        "stack_item_id": stack_item_id,
        "result": result,
        "state_changes": state_changes,
    }


# build a trigger order pdu
def create_trigger_order(
    *, seq_num: int, player_id: str, trigger_ids: list[str]
) -> dict[str, Any]:
    return {
        "type": "TRIGGER_ORDER",
        "seq_num": seq_num,
        "player_id": player_id,
        "trigger_ids": trigger_ids,
    }


# build a trigger choice pdu
def create_trigger_choice(
    *,
    seq_num: int,
    trigger_id: str,
    source_id: str,
    effect_summary: str,
    requires_target: bool = False,
    legal_targets: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "type": "TRIGGER_CHOICE",
        "seq_num": seq_num,
        "trigger_id": trigger_id,
        "source_id": source_id,
        "effect_summary": effect_summary,
        "requires_target": requires_target,
        "legal_targets": legal_targets or [],
    }


# build a combat damage result pdu
def create_combat_damage_result(
    *,
    seq_num: int,
    damage_events: list[dict[str, Any]],
    life_totals: dict[str, int],
    creatures_died: list[str],
) -> dict[str, Any]:
    return {
        "type": "COMBAT_DAMAGE_RESULT",
        "seq_num": seq_num,
        "damage_events": damage_events,
        "life_totals": life_totals,
        "creatures_died": creatures_died,
    }


# build a game over pdu
def create_game_over(
    *,
    seq_num: int,
    winner_id: str,
    loser_id: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "type": "GAME_OVER",
        "seq_num": seq_num,
        "winner_id": winner_id,
        "loser_id": loser_id,
        "reason": reason,
    }


# build an error pdu
def create_error(
    *,
    seq_num: int,
    code: str,
    message: str,
    rejected_action: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "type": "ERROR",
        "seq_num": seq_num,
        "code": code,
        "message": message,
        "rejected_action": rejected_action or {},
    }


# build a PONG pdu
def create_pong(*, seq_num: int, timestamp: int) -> dict[str, Any]:
    return {
        "type": "PONG",
        "seq_num": seq_num,
        "timestamp": timestamp,
    }


# HELPERS

# check the pdu type 
def validate_pdu_type(pdu: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pdu_type = pdu.get("type")
    if pdu_type is None:
        errors.append("Missing 'type' field.")
    elif pdu_type not in ALL_PDU_TYPES:
        errors.append(f"Unknown PDU type '{pdu_type}'.")
    elif pdu_type in C2S_PDU_TYPES:
        pass
    else:
        pass
    return errors


# check that every required field is present
def validate_required_fields(pdu: dict[str, Any]) -> list[str]:
    errors: list[str] = []
    pdu_type = pdu.get("type")
    if pdu_type not in PDU_TYPES:
        return errors

    meta = PDU_TYPES[pdu_type]
    for field in meta["required_fields"]:
        if field == "type":
            continue
        if field not in pdu:
            errors.append(f"Missing required field '{field}' in {pdu_type} PDU.")
    return errors


# parse and validate a raw pdu payload
def parse_and_validate(raw: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    pdu_type = raw.get("type")
    if pdu_type is None:
        return None, "INVALID_JSON: missing 'type' field."

    if pdu_type not in ALL_PDU_TYPES:
        return None, f"UNKNOWN_TYPE: '{pdu_type}' is not a valid MTGNP PDU type."

    missing = validate_required_fields(raw)
    if missing:
        return None, f"ILLEGAL_ACTION: {'; '.join(missing)}"

    return raw, None


# compare the expected and actual sequence numbers
def validate_seq_num(expected: int, actual: int) -> bool:
    return expected == actual
