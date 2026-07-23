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
    pid = conn.player_id
    pdu_type = pdu.get("type", "")

    # ── 1. Priority-wait path ───────────────────────────────────────────
    # If the lifecycle is waiting for a PDU from this player (priority
    # window is active), resolve the future directly.
    if pid and pid in lifecycle._pending_pdu:
        future = lifecycle._pending_pdu.pop(pid)
        if not future.done():
            future.set_result(pdu)
            return

    # ── 2. Normal handler path ─────────────────────────────────────────
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
