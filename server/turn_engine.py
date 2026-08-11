from __future__ import annotations

from typing import Any, Callable, Coroutine

from server.game_state import GameState
from shared.constants import IN_GAME_PHASES


PhaseCallback = Callable[
    [GameState, str, str, str],
    Coroutine[Any, Any, None],
]

AdvanceCallback = Callable[
    [GameState, str, str],
    Coroutine[Any, Any, None],
]


class TurnEngine:
    """manages 1 full turn for a player"""
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
        """run 1 full turn"""
        #declare the active player
        gs.active_player = ap_id
        
        # reset the land drop
        gs.land_played_this_turn = False 

        # increment turn counter
        gs.turn += 1

        # first player skips draw
        skip_draw = (gs.turn == 1)

        for i, phase in enumerate(IN_GAME_PHASES):
            #update phase
            prev_phase = gs.phase
            gs.phase = phase

            # reset priority to the AP at the start of every phase
            gs.priority_holder = ap_id

            #broadcast transition
            if self._advance_handler is not None and prev_phase != phase:
                await self._advance_handler(gs, prev_phase, phase)

            if phase == "UNTAP":
                self._handle_untap(gs, ap_id)
                gs.priority_holder = None 
                continue

            if phase == "CLEANUP":
                self._handle_cleanup(gs, ap_id)
                if getattr(gs, '_cleanup_discard_for', None) is not None:
                    if self._phase_handler is not None:
                        await self._phase_handler(gs, ap_id, nap_id, phase)
                continue

            if phase == "DRAW":
                if not skip_draw:
                    draw_ok = self._handle_draw(gs, ap_id)
                    if not draw_ok:
                        gs._draw_failed_for = ap_id

            if self._phase_handler is not None:
                await self._phase_handler(gs, ap_id, nap_id, phase)

    @staticmethod
    def _handle_untap(gs: GameState, ap_id: str) -> None:
        """untap all AP's permanents; reset land drop flag"""
        for perm in gs.battlefield.get(ap_id, []):
            perm.tapped = False
            perm.summoning_sick = False
        gs.land_played_this_turn = False

    @staticmethod
    def _handle_draw(gs: GameState, ap_id: str) -> bool:
        """AP draws 1 card from library to hand"""
        lib = gs.libraries.get(ap_id, [])
        if not lib:
            return False
        card = lib.pop(0)
        gs.hands.setdefault(ap_id, []).append(card)
        return True

    @staticmethod
    def _handle_cleanup(gs: GameState, ap_id: str) -> None:
        """clear damage across all permanents, check hand size"""
        for perms in gs.battlefield.values():
            for perm in perms:
                perm.damage = 0
        # if hand exceeds 7, signal lifecycle to discard
        hand = gs.hands.get(ap_id, [])
        if len(hand) > 7:
            gs._cleanup_discard_for = ap_id
        elif gs._cleanup_discard_for == ap_id:
            gs._cleanup_discard_for = None
