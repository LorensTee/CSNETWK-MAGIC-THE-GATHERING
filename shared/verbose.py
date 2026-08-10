from __future__ import annotations

from typing import Any

from shared.pdus import PDU_TYPES

import json

# summarize PDU for logging purposes
def _summarize(pdu: dict[str, Any]) -> str:
    pdu_type = pdu.get("type", "?")
    seq = pdu.get("seq_num", "?")

    parts: list[str] = []

    if pdu_type == "GAME_STATE_UPDATE":
        state = pdu.get("state", {})
        phase = state.get("phase", "?")
        life = state.get("life_totals", {})
        parts.append(f"phase={phase}")
        if life:
            life_str = "{" + ",".join(f"{k}:{v}" for k, v in life.items()) + "}"
            parts.append(f"life={life_str}")

    elif pdu_type == "PHASE_TRANSITION":
        parts.append(f"from={pdu.get('from_phase', '?')}")
        parts.append(f"to={pdu.get('to_phase', '?')}")
        parts.append(f"turn={pdu.get('turn', '?')}")

    elif pdu_type == "PRIORITY_GRANT":
        parts.append(f"player={pdu.get('player_id', '?')}")
        parts.append(f"timeout={pdu.get('time_limit_ms', '?')}ms")

    elif pdu_type == "STACK_PUSH":
        parts.append(f"id={pdu.get('stack_item_id', '?')}")
        parts.append(f"source={pdu.get('source', '?')}")
        parts.append(f"controller={pdu.get('controller', '?')}")

    elif pdu_type == "STACK_RESOLVE":
        parts.append(f"id={pdu.get('stack_item_id', '?')}")
        parts.append(f"result={pdu.get('result', '?')}")

    elif pdu_type == "CAST_SPELL":
        parts.append(f"card={pdu.get('card_id', '?')}")
        targets = pdu.get("targets", [])
        if targets:
            parts.append(f"→{targets}")

    elif pdu_type == "PLAY_LAND":
        parts.append(f"card={pdu.get('card_id', '?')}")

    elif pdu_type == "DECLARE_ATTACKERS":
        attackers = pdu.get("attackers", [])
        parts.append(f"count={len(attackers)}")

    elif pdu_type == "DECLARE_BLOCKERS":
        blockers = pdu.get("blockers", [])
        parts.append(f"count={len(blockers)}")

    elif pdu_type == "ACTIVATE_ABILITY":
        parts.append(f"source={pdu.get('source_id', '?')}")
        parts.append(f"ability={pdu.get('ability_index', '?')}")

    elif pdu_type == "MULLIGAN_CHOICE":
        parts.append(f"keep={pdu.get('keep', '?')}")

    elif pdu_type == "DISCARD":
        card_ids = pdu.get("card_ids", [])
        parts.append(f"count={len(card_ids)}")

    elif pdu_type == "PRIORITY_PASS":
        pass  # Nothing extra beyond seq_num.

    elif pdu_type == "PLAYER_READY":
        parts.append(f"id={pdu.get('player_id', '?')}")
        deck = pdu.get("deck_list", [])
        parts.append(f"deck_size={len(deck)}")

    elif pdu_type == "CONCEDE":
        parts.append(f"player={pdu.get('player_id', '?')}")

    elif pdu_type == "GAME_OVER":
        parts.append(f"winner={pdu.get('winner_id', '?')}")
        parts.append(f"loser={pdu.get('loser_id', '?')}")
        parts.append(f"reason={pdu.get('reason', '?')}")

    elif pdu_type == "ERROR":
        parts.append(f"code={pdu.get('code', '?')}")
        msg = pdu.get("message", "")
        if msg:
            parts.append(f"msg={msg[:60]}")

    elif pdu_type == "COMBAT_DAMAGE_RESULT":
        events = pdu.get("damage_events", [])
        parts.append(f"events={len(events)}")
        life = pdu.get("life_totals", {})
        if life:
            life_str = "{" + ",".join(f"{k}:{v}" for k, v in life.items()) + "}"
            parts.append(f"life={life_str}")

    elif pdu_type in ("PING", "PONG"):
        parts.append(f"ts={pdu.get('timestamp', '?')}")

    elif pdu_type == "TRIGGER_ORDER":
        parts.append(f"triggers={pdu.get('trigger_ids', [])}")

    elif pdu_type == "TRIGGER_CHOICE":
        parts.append(f"trigger={pdu.get('trigger_id', '?')}")

    elif pdu_type == "TRIGGER_ORDER_RESPONSE":
        parts.append("ordered")

    elif pdu_type == "TRIGGER_CHOICE_RESPONSE":
        parts.append(f"accept={pdu.get('accept', '?')}")

    elif pdu_type == "ASSIGN_DAMAGE_ORDER":
        parts.append(f"attacker={pdu.get('attacker_id', '?')}")

    suffix = " | " + " ".join(parts) if parts else ""
    return f"{pdu_type} seq={seq}{suffix}"

# format a PDU sent
def format_pdu_sent(direction_label: str, pdu: dict[str, Any]) -> str:

    summary = f"[{direction_label}] {_summarize(pdu)}"
    raw_json = json.dumps(pdu, indent=2)

    return f"{summary}\n{raw_json}"

# format a received PDU
def format_pdu_received(direction_label: str, pdu: dict[str, Any]) -> str:
    summary = f"[{direction_label}] {_summarize(pdu)}"
    raw_json = json.dumps(pdu, indent=2)

    return f"{summary}\n{raw_json}"
