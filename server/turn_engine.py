"""
server/turn_engine.py — Turn & Phase Engine (Module 02: Server Engine)

Drives the 14-phase turn sequence (UNTAP → UPKEEP → DRAW → … → CLEANUP).

This module provides the phase-ordering logic and auto-phase handlers.
The caller (``GameLifecycle``) is responsible for broadcasting PDUs,
running priority windows, and handling combat sub-steps — this module
returns control at each phase boundary so the lifecycle can intervene.
"""

from __future__ import annotations

from typing import Any, Callable, Coroutine

from server.game_state import GameState
from shared.constants import IN_GAME_PHASES


PhaseCallback = Callable[
    [GameState, str, str, str],
    Coroutine[Any, Any, None],
]
"""Signature: ``async def callback(gs, ap_id, nap_id, phase)``."""

AdvanceCallback = Callable[
    [GameState, str, str],
    Coroutine[Any, Any, None],
]
"""Signature: ``async def callback(gs, from_phase, to_phase)``."""


class TurnEngine:
    """Manages one full turn for a player.

    Iterates through the 14 phases in order.  For each phase it calls a
    *phase_handler* callback that the lifecycle provides, allowing the
    lifecycle to run priority windows, combat sub-steps, etc.

    Parameters
    ----------
    phase_handler :
        Async callback invoked for each phase.
        Signature: ``async def handler(gs, ap_id, nap_id, phase)``.
        The handler should mutate *gs.phase* as needed and return when
        the phase is complete.
    advance_handler :
        Async callback to broadcast a ``PHASE_TRANSITION``.
        Signature: ``async def handler(gs, from_phase, to_phase)``.
    """

    def __init__(
        self,
        phase_handler: PhaseCallback | None = None,
        advance_handler: PhaseCallback | None = None,
    ) -> None:
        self._phase_handler = phase_handler
        self._advance_handler = advance_handler

    async def run_turn(
        self,
        gs: GameState,
        ap_id: str,
        nap_id: str,
    ) -> None:
        """Run one full turn.

        Iterates through all 14 phases.  Returns when the phase would
        loop back to UNTAP (i.e. after CLEANUP).
        """
        # Increment turn counter for this new turn.
        gs.turn += 1

        # Turn 1: the first player (AP) skips their draw step.
        skip_draw = (gs.turn == 1)

        for i, phase in enumerate(IN_GAME_PHASES):
            # Update phase.
            prev_phase = gs.phase
            gs.phase = phase

            # Broadcast transition.
            if self._advance_handler is not None and prev_phase != phase:
                await self._advance_handler(gs, prev_phase, phase)

            # ── Auto phases (no priority, no handler call) ─────────────
            if phase == "UNTAP":
                self._handle_untap(gs, ap_id)
                continue

            if phase == "CLEANUP":
                self._handle_cleanup(gs, ap_id)
                # If cleanup signals discard needed, invoke the phase handler
                # so the lifecycle can open a priority window for DISCARD.
                if gs._cleanup_discard_for is not None:
                    if self._phase_handler is not None:
                        await self._phase_handler(gs, ap_id, nap_id, phase)
                continue

            # ── Draw step with possible skip ────────────────────────────
            if phase == "DRAW":
                if not skip_draw:
                    draw_ok = self._handle_draw(gs, ap_id)
                    if not draw_ok:
                        # Library empty — signal game loss.
                        gs._draw_failed_for = ap_id
                # Still call the handler so the lifecycle can run priority.

            # ── Invoke the lifecycle's phase handler ─────────────────────
            if self._phase_handler is not None:
                await self._phase_handler(gs, ap_id, nap_id, phase)

    # ── Auto-phase logic ─────────────────────────────────────────────────────

    @staticmethod
    def _handle_untap(gs: GameState, ap_id: str) -> None:
        """Untap all AP's permanents; reset land drop flag."""
        for perm in gs.battlefield.get(ap_id, []):
            perm.tapped = False
            perm.summoning_sick = False
        gs.land_played_this_turn = False

    @staticmethod
    def _handle_draw(gs: GameState, ap_id: str) -> bool:
        """AP draws 1 card from library to hand.

        Returns ``True`` if the draw succeeded, ``False`` if the library
        was empty (DECK_EMPTY condition).
        """
        lib = gs.libraries.get(ap_id, [])
        if not lib:
            return False  # Library empty — would cause game loss.
        card = lib.pop(0)
        gs.hands.setdefault(ap_id, []).append(card)
        return True

    @staticmethod
    def _handle_cleanup(gs: GameState, ap_id: str) -> None:
        """Cleanup: clear damage across all permanents, check hand size."""
        for perms in gs.battlefield.values():
            for perm in perms:
                perm.damage = 0
        # If hand exceeds 7, signal lifecycle to request DISCARD.
        hand = gs.hands.get(ap_id, [])
        if len(hand) > 7:
            gs._cleanup_discard_for = ap_id
        elif gs._cleanup_discard_for == ap_id:
            gs._cleanup_discard_for = None
