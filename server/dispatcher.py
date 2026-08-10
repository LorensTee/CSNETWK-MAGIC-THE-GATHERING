from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.connection import ServerConnection
from shared.pdus import create_priority_grant

if TYPE_CHECKING:
    from server.game_lifecycle import GameLifecycle


_HANDLER_MAP: dict[str, str] = {
    "PLAYER_READY": "handle_player_ready",
    "MULLIGAN_CHOICE": "handle_mulligan_choice",
    "PRIORITY_PASS": "handle_priority_pass",
    "CAST_SPELL": "handle_cast_spell",
    "ACTIVATE_ABILITY": "handle_activate_ability",
    "PLAY_LAND": "handle_play_land",
    "DECLARE_ATTACKERS": "handle_declare_attackers",
    "DECLARE_BLOCKERS": "handle_declare_blockers",
    "ASSIGN_DAMAGE_ORDER": "handle_assign_damage_order",
    "DISCARD": "handle_discard",
    "TRIGGER_ORDER_RESPONSE": "handle_trigger_order_response",
    "TRIGGER_CHOICE_RESPONSE": "handle_trigger_choice_response",
    "CONCEDE": "handle_concede",
    "PING": "handle_ping",
}

#action PDUs that only work if the sender holds priority
_PRIORITY_BEARING_TYPES = frozenset({
    "CAST_SPELL",
    "ACTIVATE_ABILITY",
    "PRIORITY_PASS",
    "DECLARE_ATTACKERS",
    "DECLARE_BLOCKERS",
    "ASSIGN_DAMAGE_ORDER",
    "PLAY_LAND",
    "DISCARD",
    "TRIGGER_ORDER_RESPONSE",
    "TRIGGER_CHOICE_RESPONSE",
})


async def dispatch(
    lifecycle: GameLifecycle,
    conn: ServerConnection,
    pdu: dict[str, Any],
) -> None:
    #route one incoming PDU to the right handler
    if "type" not in pdu or "seq_num" not in pdu:
        await lifecycle.send_error(
            conn,
            "MALFORMED_PDU",
            "Every PDU must include 'type' and 'seq_num' fields.",
            pdu
        )
        return

    #type must be a string
    if not isinstance(pdu.get("type"), str):
        await lifecycle.send_error(
            conn,
            "MALFORMED_PDU",
            "The 'type' field must be a string.",
            pdu
        )
        return

    pid = conn.player_id
    pdu_type = pdu.get("type", "")
    client_seq = pdu["seq_num"]

    if pdu_type == "CONCEDE":
        print(f"[INTERRUPT] {pid} is conceding! Nuking the game engine...")

        #end game and mark opponent as winner
        winner_id = lifecycle._opponent(pid)
        if winner_id:
            await lifecycle._end_game(lifecycle.gs, "CONCEDE", winner_id, pid)

        return

    # handle any action that is still waiting for priority
    if pid and pid in lifecycle._pending_pdu and pdu_type not in ("PING", "PONG"):

        #compare against current grant token instead of the global counter
        token_seq = getattr(conn, "grant_token", None)
        if token_seq is not None and client_seq != token_seq:
            #reject stale actions n reissue the current priority grant
            await lifecycle.send_error(
                conn,
                "STALE_ACTION",
                f"Action is stale. PDU seq_num {client_seq} does not match "
                f"the granted token {token_seq}.",
                pdu
            )
            grant_pdu = create_priority_grant(
                seq_num=token_seq,
                player_id=pid,
                time_limit_ms=lifecycle.config.time_limit_ms,
            )
            await conn.send_pdu_explicit(grant_pdu, token_seq)
            return

        future = lifecycle._pending_pdu.pop(pid)
        if not future.done():
            future.set_result(pdu)
            return

    if pid and pdu_type in _PRIORITY_BEARING_TYPES:
        await lifecycle.send_error(
            conn,
            "NOT_YOUR_PRIORITY",
            f"Action '{pdu_type}' submitted when {pid} does not hold priority.",
            pdu,
        )
        return

    # normal handler path
    pdu["_player_id"] = pid

    handler_name = _HANDLER_MAP.get(pdu_type)
    if handler_name is None:
        #unknown type, so reject
        if pdu_type:
            await lifecycle.send_error(
                conn,
                "UNKNOWN_TYPE",
                f"Unrecognised PDU type {pdu_type!r}.",
                pdu,
            )
        return

    handler = getattr(lifecycle, handler_name, None)
    if handler is not None:
        await handler(conn, pdu)
