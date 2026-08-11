from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.connection import ServerConnection

if TYPE_CHECKING:
    from server.game_lifecycle import GameLifecycle


_HANDLER_MAP: dict[str, str] = {
    # lobby
    "PLAYER_READY": "handle_player_ready",
    # mull
    "MULLIGAN_CHOICE": "handle_mulligan_choice",
    # IN_GAME
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
    # any phase
    "CONCEDE": "handle_concede",
    "PING": "handle_ping",
}


async def dispatch(
    lifecycle: GameLifecycle,
    conn: ServerConnection,
    pdu: dict[str, Any],
) -> None:
    """route 1 incoming PDU"""
    if "type" not in pdu or "seq_num" not in pdu:
        await lifecycle.send_error(
            conn,
            "MALFORMED_PDU",
            "Every PDU must include 'type' and 'seq_num' fields.",
            pdu
        )
        return
    
    pid = conn.player_id
    pdu_type = pdu.get("type", "")
    client_seq = pdu["seq_num"]

    if pdu_type == "CONCEDE":
        print(f"[INTERRUPT] {pid} is conceding! Nuking the game engine...")
        
        #broadcast GAME_OVER and set the flag
        winner_id = lifecycle._opponent(pid)
        if winner_id:
            await lifecycle._end_game(lifecycle.gs, "CONCEDE", winner_id, pid)
        
        # derail
        if pid in lifecycle._pending_pdu:
            future = lifecycle._pending_pdu.pop(pid)
            if not future.done():
                future.cancel()
                
        # cancel opps future
        opponent_id = lifecycle._opponent(pid)
        if opponent_id and opponent_id in lifecycle._pending_pdu:
            future = lifecycle._pending_pdu.pop(opponent_id)
            if not future.done():
                future.cancel()

        return

    if pid and pid in lifecycle._pending_pdu and pdu_type not in ("PING", "PONG"):
        
        # SERVER REJECTS STALE SEQ_NUMS
        if client_seq < conn.seq_num:
            await lifecycle.send_error(
                conn,
                "STALE_ACTION",
                f"Action is stale. PDU seq_num {client_seq} is older than server seq_num {conn.seq_num}.",
                pdu
            )
            return

        future = lifecycle._pending_pdu.pop(pid)
        if not future.done():
            future.set_result(pdu)
            return

    pdu["_player_id"] = pid

    handler_name = _HANDLER_MAP.get(pdu_type)
    if handler_name is None:
        if pdu_type:
            await lifecycle.send_error(
                conn,
                "UNKNOWN_TYPE",
                f"Unrecognised PDU type '{pdu_type}'.",
                pdu,
            )
        return

    handler = getattr(lifecycle, handler_name, None)
    if handler is not None:
        await handler(conn, pdu)
