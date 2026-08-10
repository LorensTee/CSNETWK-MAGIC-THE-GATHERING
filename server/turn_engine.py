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
    # manages one full turn for a player
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
        #run one full turn through all phases,set the active player, n reset turn state
        gs.active_player = ap_id
        gs.land_played_this_turn = False

        # incerement turn count
        gs.turn += 1

        #first player skips draw on turn 1
        skip_draw = (gs.turn == 1)

        i = 0
        while i < len(IN_GAME_PHASES):
            phase = IN_GAME_PHASES[i]

            #next phase
            prev_phase = gs.phase
            gs.phase = phase

            #give priority back to the active player at phase start
            gs.priority_holder = ap_id

            # empty floating mana at phase boundaries
            for pid in list(gs.mana_pools):
                gs.mana_pools[pid] = gs.mana_pools[pid].empty()

            #broadcast phase change
            if self._advance_handler is not None and prev_phase != phase:
                await self._advance_handler(gs, prev_phase, phase)

            #auto phases do not wait for normal priority
            if phase == "UNTAP":
                self._handle_untap(gs, ap_id)
                gs.priority_holder = None
                i += 1
                continue

            if phase == "CLEANUP":
                self._handle_cleanup(gs, ap_id)
                if getattr(gs, '_cleanup_discard_for', None) is not None:
                    if self._phase_handler is not None:
                        await self._phase_handler(gs, ap_id, nap_id, phase)
                i += 1
                continue

            #draw step can be skipped on turn 1
            if phase == "DRAW":
                if not skip_draw:
                    draw_ok = self._handle_draw(gs, ap_id)
                    if not draw_ok:
                        gs._draw_failed_for = ap_id

            # let the lifecycle handle priority and phase logic
            if self._phase_handler is not None:
                await self._phase_handler(gs, ap_id, nap_id, phase)

            # phase handlers can jump ahead to a later phase
            skip_to = getattr(gs, "_skip_to_phase", None)
            if skip_to:
                gs._skip_to_phase = None
                if skip_to in IN_GAME_PHASES and IN_GAME_PHASES.index(skip_to) > i:
                    i = IN_GAME_PHASES.index(skip_to)
                    continue
            i += 1

    @staticmethod
    def _handle_untap(gs: GameState, ap_id: str) -> None:
        #untap all active players permanents and reset the land drop flag
        for perm in gs.battlefield.get(ap_id, []):
            perm.tapped = False
            perm.summoning_sick = False
        gs.land_played_this_turn = False

    @staticmethod
    def _handle_draw(gs: GameState, ap_id: str) -> bool:
        # draw 1 card from the active player's library
        lib = gs.libraries.get(ap_id, [])
        if not lib:
            return False
        card = lib.pop(0)
        gs.hands.setdefault(ap_id, []).append(card)
        return True

    @staticmethod
    def _handle_cleanup(gs: GameState, ap_id: str) -> None:
        # clear damage and trigger discard if the hand is too large
        for perms in gs.battlefield.values():
            for perm in perms:
                perm.damage = 0
        hand = gs.hands.get(ap_id, [])
        if len(hand) > 7:
            gs._cleanup_discard_for = ap_id
        elif gs._cleanup_discard_for == ap_id:
            gs._cleanup_discard_for = None
