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
from shared.pdus import create_priority_grant

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

# Client-to-server action PDUs that are only legal while the sender holds
# priority (RFC §5.4).  MULLIGAN_CHOICE is deliberately excluded: it is
# answered on the handler path with its own GAME_STATE_UPDATE seq echo
# (RFC §5.4), and PLAYER_READY/PING/PONG/CONCEDE have their own rules.
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

        # 2. The engine unwinds gracefully: _end_game set _game_over, so
        # every wait_for_pdu race resolves the game-over branch, which
        # raises GameOverInterrupt — the engine returns normally and the
        # lifecycle resets for the next game.  (No future.cancel() here:
        # CancelledError would propagate through the whole engine stack
        # and kill the read loops, stranding the next game.)
        return

    # ── 2. Priority-wait path & STALE_ACTION Defense ────────────────────
    if pid and pid in lifecycle._pending_pdu and pdu_type not in ("PING", "PONG"):
        
        # RUBRIC REQUIREMENT: Server rejects stale seq_nums.  Compare
        # against the GRANTED token (conn.grant_token), not conn.seq_num:
        # the latter advances on every server broadcast, so comparing
        # against it would reject the retry echo of a re-issued grant
        # (same token) and ping-pong the player into a PriorityTimeout.
        token_seq = getattr(conn, "grant_token", None)
        if token_seq is not None and client_seq != token_seq:
            # RFC §11.3: reject with STALE_ACTION and re-issue the CURRENT
            # PRIORITY_GRANT with the same seq_num so the player can retry.
            # The pending future stays alive; a retry with the correct echo
            # still resolves it (no lockout until PriorityTimeout).
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

    # ── 2b. Out-of-window actions → NOT_YOUR_PRIORITY (RFC §11) ─────────
    # A priority-bearing action PDU that arrives while the sender has no
    # pending priority window must be answered with NOT_YOUR_PRIORITY and
    # discarded (never silently dropped), leaving game state unchanged.
    if pid and pdu_type in _PRIORITY_BEARING_TYPES:
        await lifecycle.send_error(
            conn,
            "NOT_YOUR_PRIORITY",
            f"Action '{pdu_type}' submitted when {pid} does not hold priority.",
            pdu,
        )
        return

    # ── 3. Normal handler path ─────────────────────────────────────────
    pdu["_player_id"] = pid

    handler_name = _HANDLER_MAP.get(pdu_type)
    if handler_name is None:
        # Unknown type — send error.  The type string is client-supplied:
        # repr-quote it so ANSI/terminal escapes cannot be injected into
        # the verbose log.
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
