"""
server/dispatcher.py — PDU Dispatcher (Module 02: Server Engine)

Routes incoming PDUs from ``ServerConnection.read_loop`` to the appropriate
handler.  This is the **sole** PDU reader — no other code calls ``recv_pdu``.

The dispatcher supports two routing paths:

1. **Priority-wait path**: If ``lifecycle._pending_pdu`` has a future for the
   sending player, the PDU resolves that future (used by the priority manager).
2. **Handler path**: Otherwise the PDU is routed to the lifecycle handler
   matching its type.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from server.connection import ServerConnection

if TYPE_CHECKING:
    from server.game_lifecycle import GameLifecycle


_HANDLER_MAP: dict[str, str] = {
    # LOBBY
    "PLAYER_READY": "handle_player_ready",
    # MULLIGAN
    "MULLIGAN_CHOICE": "handle_mulligan_choice",
    # IN_GAME — priority-bearing actions
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
    # Any phase
    "CONCEDE": "handle_concede",
    "PING": "handle_ping",
}


async def dispatch(
    lifecycle: GameLifecycle,
    conn: ServerConnection,
    pdu: dict[str, Any],
) -> None:
    """Route one incoming PDU.

    Parameters
    ----------
    lifecycle :
        The active ``GameLifecycle``.
    conn :
        The ``ServerConnection`` that received this PDU.
    pdu :
        The parsed PDU dict.
    """

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
        print(f"🚨 [INTERRUPT] {pid} is conceding! Nuking the game engine...")
        
        # 1. Broadcast the GAME_OVER and set the game_over flag
        winner_id = lifecycle._opponent(pid)
        if winner_id:
            await lifecycle._end_game(lifecycle.gs, "CONCEDE", winner_id, pid)
        
        # 2. DERAIL THE ENGINE
        # By canceling the future instead of resolving it, we force an 
        # asyncio.CancelledError inside the engine's wait loop. This instantly 
        # kills the current phase and forces the engine to exit cleanly!
        if pid in lifecycle._pending_pdu:
            future = lifecycle._pending_pdu.pop(pid)
            if not future.done():
                future.cancel()  # <--- The magic bullet
                
        # Also cancel the opponent's future just in case the engine was waiting on them
        opponent_id = lifecycle._opponent(pid)
        if opponent_id and opponent_id in lifecycle._pending_pdu:
            future = lifecycle._pending_pdu.pop(opponent_id)
            if not future.done():
                future.cancel()

        return

    # ── 2. Priority-wait path & STALE_ACTION Defense ────────────────────
    if pid and pid in lifecycle._pending_pdu and pdu_type not in ("PING", "PONG"):
        
        # RUBRIC REQUIREMENT: Server rejects stale seq_nums
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

    # ── 3. Normal handler path ─────────────────────────────────────────
    pdu["_player_id"] = pid

    handler_name = _HANDLER_MAP.get(pdu_type)
    if handler_name is None:
        # Unknown type — send error.
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
