"""
client/dispatcher.py — Client PDU Dispatcher (Module 03: Client App)

Routes incoming server-to-client PDUs to the appropriate handler method.
Each handler updates the client's *visible_state* and triggers a re-render.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from shared.pdus import create_trigger_choice_response, create_trigger_order_response

if TYPE_CHECKING:
    from client.client import GameClient


class ClientDispatcher:
    """Dispatches incoming S→C PDUs to handler methods.

    Parameters
    ----------
    client :
        The ``GameClient`` whose state is updated.
    """

    def __init__(self, client: GameClient) -> None:
        self.client = client

    async def dispatch(self, pdu: dict[str, Any]) -> None:
        """Route a parsed PDU to the appropriate handler."""
        pdu_type = pdu.get("type", "")

        handler = {
            "GAME_STATE_UPDATE": self._handle_game_state_update,
            "PHASE_TRANSITION": self._handle_phase_transition,
            "PRIORITY_GRANT": self._handle_priority_grant,
            "STACK_PUSH": self._handle_stack_push,
            "STACK_RESOLVE": self._handle_stack_resolve,
            "TRIGGER_ORDER": self._handle_trigger_order,
            "TRIGGER_CHOICE": self._handle_trigger_choice,
            "COMBAT_DAMAGE_RESULT": self._handle_combat_damage_result,
            "GAME_OVER": self._handle_game_over,
            "ERROR": self._handle_error,
            "PONG": self._handle_pong,
        }.get(pdu_type)

        if handler is None:
            print(f"[WARN] Unknown server PDU type: {pdu_type}")
            return

        await handler(pdu)

    # ═══════════════════════════════════════════════════════════════════════════
    # Handlers
    # ═══════════════════════════════════════════════════════════════════════════

    async def _handle_game_state_update(
        self, pdu: dict[str, Any]
    ) -> None:
        """Replace visible state and update client lifecycle state."""

        if "seq_num" in pdu:
            self.client._current_priority_seq = pdu["seq_num"]
            
        state_obj = pdu.get("state", {})
        self.client.visible_state = state_obj
        
        phase = state_obj.get("phase", "")
        # Map server phase → client state.
        if phase == "LOBBY":
            self.client.state = "LOBBY"
        elif phase == "GAME_SETUP":
            self.client.state = "GAME_SETUP"
        elif phase == "MULLIGAN":
            self.client.state = "MULLIGAN"
        elif phase == "GAME_OVER":
            self.client.state = "GAME_OVER"
        elif phase in (
            "UNTAP", "UPKEEP", "DRAW", "PRECOMBAT_MAIN", "BEGIN_COMBAT",
            "DECLARE_ATTACKERS", "DECLARE_BLOCKERS", "ASSIGN_DAMAGE_ORDER",
            "FIRST_STRIKE_DAMAGE", "COMBAT_DAMAGE", "END_OF_COMBAT",
            "POSTCOMBAT_MAIN", "END_STEP", "CLEANUP",
        ):
            self.client.state = "IN_GAME"

        self._trigger_render()

    async def _handle_phase_transition(
        self, pdu: dict[str, Any]
    ) -> None:
        """Update visible state phase and turn."""
        vs = self.client.visible_state
        vs["phase"] = pdu.get("to_phase", vs.get("phase"))
        vs["from_phase"] = pdu.get("from_phase", vs.get("from_phase"))
        vs["turn"] = pdu.get("turn", vs.get("turn"))

        to_phase = pdu.get("to_phase", "")
        # Update client state if this is a lifecycle transition.
        if to_phase in ("LOBBY", "GAME_SETUP", "MULLIGAN", "GAME_OVER"):
            self.client.state = to_phase

        print(f"\n[Phase] {pdu.get('from_phase','?')} → {to_phase} "
              f"(Turn {pdu.get('turn','?')})")
        self._trigger_render()

    async def _handle_priority_grant(
        self, pdu: dict[str, Any]
    ) -> None:
        """Store the priority seq_num for echo in action PDUs."""
        seq = pdu.get("seq_num", 0)
        self.client._current_priority_seq = seq

        player_id = pdu.get("player_id", "")
        timeout = pdu.get("time_limit_ms", 60000)
        if player_id == self.client.config.player_id:
            print(f"\n[Priority] You have priority! ({timeout}ms)")
        else:
            print(f"\n[Priority] {player_id} has priority")

        self._trigger_render()

    async def _handle_stack_push(
        self, pdu: dict[str, Any]
    ) -> None:
        """Update local stack view."""
        stack = self.client.visible_state.setdefault("stack", [])
        stack.append({
            "stack_item_id": pdu.get("stack_item_id", ""),
            "item_type": pdu.get("item_type", ""),
            "source": pdu.get("source", ""),
            "targets": pdu.get("targets", []),
            "controller": pdu.get("controller", ""),
        })
        print(f"\n[Stack] +{pdu.get('source','?')} ({pdu.get('controller','?')})")
        self._trigger_render()

    async def _handle_stack_resolve(
        self, pdu: dict[str, Any]
    ) -> None:
        """Remove resolved item from local stack and print result."""
        sid = pdu.get("stack_item_id", "")
        result = pdu.get("result", "?")
        changes = pdu.get("state_changes", [])

        stack = self.client.visible_state.get("stack", [])
        self.client.visible_state["stack"] = [
            si for si in stack if si.get("stack_item_id") != sid
        ]

        print(f"\n[Stack] Resolve {sid}: {result}")
        for ch in changes:
            ct = ch.get("change_type", "?")
            tgt = ch.get("target", "?")
            amt = ch.get("amount", "")
            if ct == "DAMAGE":
                print(f"  → {amt} damage to {tgt}")
            elif ct == "LIFE_GAIN":
                print(f"  → {tgt} gains {amt} life")
            elif ct == "COUNTER":
                print(f"  → Countered {tgt}")
            elif ct == "PERMANENT_ENTERS":
                print(f"  → {ch.get('card_id','?')} enters under {ch.get('controller','?')}")
            elif ct == "DESTROY":
                print(f"  → Destroyed {tgt}")
            else:
                print(f"  → {ct}: {tgt}")
        self._trigger_render()

    async def _handle_trigger_order(
        self, pdu: dict[str, Any]
    ) -> None:
        """Prompt the player to order triggered abilities (RFC §8.11)."""
        trigger_ids = pdu.get("trigger_ids", [])
        print(f"\n[Trigger] Please order triggers: {trigger_ids}")
        print("Use: order-triggers <id1> <id2> ... (first resolves last)")

    async def _handle_trigger_choice(
        self, pdu: dict[str, Any]
    ) -> None:
        """Prompt the player to accept/reject a triggered ability."""
        tid = pdu.get("trigger_id", "")
        summary = pdu.get("effect_summary", "")
        requires_target = pdu.get("requires_target", False)
        legal_targets = pdu.get("legal_targets", [])

        print(f"\n[Trigger] {summary}")
        print(f"  Accept? (yes/no)", end=" ")
        if requires_target:
            print(f"Targets: {legal_targets}")
        # For now, auto-accept without target (simplified).

    async def _handle_combat_damage_result(
        self, pdu: dict[str, Any]
    ) -> None:
        """Update life totals and print damage summary."""
        life = pdu.get("life_totals", {})
        if life:
            self.client.visible_state["life_totals"] = life

        events = pdu.get("damage_events", [])
        died = pdu.get("creatures_died", [])

        print(f"\n[Combat] Results:")
        for ev in events:
            src = ev.get("source", "?")
            tgt = ev.get("target", "?")
            amt = ev.get("amount", 0)
            print(f"  {src} deals {amt} to {tgt}")
        if died:
            print(f"  Creatures died: {', '.join(died)}")

        if life:
            print(f"  Life totals: {life}")
        self._trigger_render()

    async def _handle_game_over(
        self, pdu: dict[str, Any]
    ) -> None:
        """Handle game over — display result and set state."""
        winner = pdu.get("winner_id", "?")
        loser = pdu.get("loser_id", "?")
        reason = pdu.get("reason", "?")
        self.client.visible_state["winner_id"] = winner
        self.client.visible_state["loser_id"] = loser
        self.client.visible_state["reason"] = reason
        self.client.state = "GAME_OVER"

        print(f"\n{'='*50}")
        print(f"  GAME OVER!")
        print(f"  Winner: {winner}")
        print(f"  Loser:  {loser}")
        print(f"  Reason: {reason}")
        print(f"{'='*50}")
        print("Send a new deck to play again (type 'ready' after 'deck ...')")
        self._trigger_render()

    async def _handle_error(
        self, pdu: dict[str, Any]
    ) -> None:
        """Display an error from the server — do NOT crash."""
        code = pdu.get("code", "?")
        msg = pdu.get("message", "")
        rejected = pdu.get("rejected_action", {})

        print(f"\n[ERROR] {code}: {msg}")
        if rejected:
            print(f"  Rejected action: {json.dumps(rejected)}")

    async def _handle_pong(
        self, pdu: dict[str, Any]
    ) -> None:
        """Forward PONG to the heartbeat manager."""
        if self.client.heartbeat is not None:
            self.client.heartbeat.on_pong(pdu)

    # ═══════════════════════════════════════════════════════════════════════════
    # Internal
    # ═══════════════════════════════════════════════════════════════════════════

    def _trigger_render(self) -> None:
        """Wake up the render loop."""
        if hasattr(self.client, "_render_event") and self.client._render_event is not None:
            self.client._render_event.set()
