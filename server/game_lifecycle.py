"""
server/game_lifecycle.py — Game Lifecycle FSM (Module 02: Server Engine)

The core state machine that drives the MTGNP game lifecycle:

    LOBBY → GAME_SETUP → MULLIGAN → IN_GAME → GAME_OVER → LOBBY (loop)

Each state has a dedicated ``_run_<state>()`` coroutine.  The lifecycle also
owns the handler methods for all 14 client-to-server PDU types, which the
dispatcher calls when a PDU arrives.

**IMPORTANT — single-reader architecture:**
The ``read_loop`` in each ``ServerConnection`` is the **sole** PDU reader.
During priority windows the ``PriorityManager`` does NOT call ``recv_pdu``
directly — it awaits a future resolved by the dispatcher.  This avoids
races between concurrent readers on the same TCP stream.
"""

from __future__ import annotations

import asyncio
import random
from typing import Any

from server.card_effects import resolve_effect
from server.card_loader import CardLoader
from server.combat import CombatManager
from server.config import ServerConfig
from server.connection import ServerConnection
from server.game_state import GameState, build_visible_state, Permanent
from server.mana import ManaPool, deduct_mana, empty_pool_dict
from server.mulligan import process_mulligan_choice
from server.priority import ConnectionLost, PriorityManager, PriorityTimeout
from server.stack import StackManager
from server.turn_engine import TurnEngine
from server.validators import (
    validate_cast_spell,
    validate_play_land,
    validate_attack,
    validate_block,
    validate_mulligan,
    validate_discard,
    validate_deck,
)
from shared.constants import C2S_PDU_TYPES, DEFAULT_TIME_LIMIT_MS
from shared.pdus import (
    create_error,
    create_game_over,
    create_game_state_update,
    create_phase_transition,
    create_pong,
    create_stack_push,
    create_stack_resolve,
    parse_and_validate,
)

from server.stack import check_state_based_actions


class GameOverInterrupt(Exception):
    """Raised inside the engine when the game ends while awaiting a PDU.

    The CONCEDE / disconnect / timeout paths broadcast GAME_OVER and set
    ``_game_over``; every engine await that is racing that event must
    unwind *gracefully* (``return``), never propagate ``CancelledError``
    or re-broadcast GAME_OVER.
    """


def _retrieve_task_exception(task: "asyncio.Task") -> None:
    """Done-callback: swallow a task's exception so Python does not print
    'Task exception was never retrieved' for fire-and-forget tasks."""
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"[server] fire-and-forget task failed: {exc!r}",
              file=__import__("sys").stderr)


class GameLifecycle:
    """The game lifecycle state machine.

    Parameters
    ----------
    config :
        Server configuration.
    card_loader :
        Loaded card catalog.
    connections :
        Two-element list of ``ServerConnection`` instances, in connection order.
    """

    def __init__(
        self,
        config: ServerConfig,
        card_loader: CardLoader,
        connections: list[ServerConnection],
    ) -> None:
        self.config = config
        self.card_loader = card_loader
        self.connections = connections

        # Derived.
        self.gs = GameState()
        self.stack_mgr = StackManager()
        self.priority_mgr = PriorityManager(config)
        self.combat_mgr = CombatManager()
        self.turn_engine = TurnEngine(
            phase_handler=self._on_phase,
            advance_handler=self._on_advance,
        )

        # ── Priority-wait futures ────────────────────────────────────────
        # The dispatcher uses these to route PDUs during priority windows.
        # Keyed by player_id.  Only one future per player at a time.
        self._pending_pdu: dict[str, asyncio.Future[dict]] = {}

        # Last _end_game task spawned from _check_game_over (kept alive so
        # it cannot be GC'd mid-run).
        self._pending_end_task: asyncio.Task | None = None

        # Lock for game-state mutations.
        self._lock = asyncio.Lock()

        # Mapping of player_id → player index (0 or 1).
        self._player_index: dict[str, int] = {}

        # Event signalling the game has ended.
        self._game_over = asyncio.Event()

        # Stored deck lists, keyed by player_id (set in handle_player_ready).
        self._deck_lists: dict[str, list[str]] = {}

        # Per-player asyncio.Event that fires when the player keeps their hand.
        self._mulligan_kept: dict[str, asyncio.Event] = {}

        # Seq_num tracking for MULLIGAN_CHOICE echo validation.
        self._mulligan_expected_seq: dict[str, int] = {}

    # ── Priority-wait helper ─────────────────────────────────────────────

    async def wait_for_pdu(
        self, player_id: str, timeout: float
    ) -> dict[str, Any]:
        """Wait for the next PDU from *player_id* with a timeout.

        Called by the priority manager during IN_GAME.  The dispatcher
        resolves the future when a matching PDU arrives via the read_loop.

        Raises
        ------
        asyncio.TimeoutError
            If the player does not respond within *timeout* seconds.
        """
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_pdu[player_id] = future
        game_over_task = asyncio.create_task(self._game_over.wait())
        try:
            done, _ = await asyncio.wait(
                {asyncio.ensure_future(future), game_over_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=timeout,
            )
            if future in done:
                pdu = future.result()
                pdu["_player_id"] = player_id
                return pdu
            # The game ended (watchdog/priority timeout or concede) —
            # unwind the engine gracefully.  A genuine priority timeout
            # surfaces as asyncio.TimeoutError from the race's *timeout*;
            # a set game-over event surfaces here.
            if self._game_over.is_set():
                raise GameOverInterrupt()
            raise asyncio.TimeoutError()
        finally:
            game_over_task.cancel()
            # Clean up the future reference on timeout or cancellation.
            if self._pending_pdu.get(player_id) is future:
                self._pending_pdu.pop(player_id, None)

    # ═══════════════════════════════════════════════════════════════════════════
    # Public entry point
    # ═══════════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """Run the full game lifecycle (LOBBY → … → GAME_OVER → loop)."""
        conns = self.connections
        if len(conns) < 2:
            return  # Not enough connections — should not happen.

        # Clear the server-assigned temp ids (player_1/player_2, set at
        # accept time for logging) so the first LOBBY counts PLAYER_READYs
        # from scratch.  Per-game clearing happens in _end_game; the lobby
        # itself must NOT clear conn.player_id, or READYs that arrived
        # while the previous game was unwinding would be discarded.
        for conn in conns:
            conn.player_id = None

        # Start read loops for both connections (sole PDU readers) ONCE
        # for the lifecycle's whole lifetime.  They must NOT be recreated
        # per game: cancelling a read_loop mid-readexactly (TaskGroup
        # exit) leaves the StreamReader's internal waiter dangling, so
        # the next read raises "readexactly() called while another
        # coroutine is already waiting" and the next game hangs.
        read_tasks = [asyncio.create_task(conn.read_loop()) for conn in conns]
        try:
            while True:
                self._game_over.clear()
                gs = self.gs

                # LOBBY
                await self._run_lobby(gs, conns)
                if self._game_over.is_set():
                    await self._run_game_over(gs, conns)
                    continue

                # GAME_SETUP
                await self._run_setup(gs, conns)
                if self._game_over.is_set():
                    await self._run_game_over(gs, conns)
                    continue

                # MULLIGAN
                await self._run_mulligan(gs, conns)
                if self._game_over.is_set():
                    await self._run_game_over(gs, conns)
                    continue

                # IN_GAME loop
                await self._run_in_game(gs, conns)
                await self._run_game_over(gs, conns)
        finally:
            for task in read_tasks:
                task.cancel()

    # ═══════════════════════════════════════════════════════════════════════════
    # Lifecycle state runners
    # ═══════════════════════════════════════════════════════════════════════════

    async def _run_lobby(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        """LOBBY: wait for two PLAYER_READY PDUs.

        The ready-state (``players_ready``, ``conn.player_id``,
        ``player_ids``, deck lists) is reset by ``_end_game`` *before* the
        GAME_OVER broadcast, so READYs that arrive in response to
        GAME_OVER are always counted for the next game.  Resetting here
        instead would wipe READYs that arrived while the previous game's
        engine was still unwinding, deadlocking the next lobby.
        """
        gs.phase = "LOBBY"

        while gs.players_ready < 2 and not self._game_over.is_set():
            await asyncio.sleep(0.1)  # Yield — dispatcher updates state.

        # A disconnect during LOBBY must not stall the lifecycle: the
        # watchdog sets _game_over, which unblocks this loop.
        if self._game_over.is_set():
            return

        # Assign player IDs in connection order.
        for conn in conns:
            if conn.player_id:
                gs.player_ids.append(conn.player_id)
                self._player_index[conn.player_id] = len(gs.player_ids) - 1

    async def _run_setup(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        """GAME_SETUP: shuffle, draw 7, coin flip for first player."""
        gs.phase = "GAME_SETUP"

        # Validate both deck lists using stored lists.
        timeout_s = self.priority_mgr.config.time_limit_ms / 1000.0
        for pid in gs.player_ids:
            while True:
                deck = self._deck_lists.get(pid, [])
                ok, msg = self.card_loader.is_legal_deck(deck)
                if ok:
                    break
                # RFC §11: an invalid deck is answered with ERROR ILLEGAL_DECK
                # (GAME_OVER reasons are WIN/LOSS/CONCEDE/DISCONNECT only).
                await self.send_error(
                    self._connection_for(pid), "ILLEGAL_DECK", msg,
                    {"type": "PLAYER_READY"},
                )
                # Wait for a corrected deck (re-ready handled in
                # handle_player_ready); a disconnect ends the game.
                try:
                    response = await asyncio.wait_for(
                        self.wait_for_pdu(pid, timeout_s), timeout=timeout_s
                    )
                except GameOverInterrupt:
                    return
                except (asyncio.TimeoutError, ConnectionError, ConnectionLost):
                    await self._end_game(
                        gs, "DISCONNECT", self._opponent(pid) or "", pid
                    )
                    return
                if response and response.get("type") == "PLAYER_READY":
                    deck = response.get("deck_list", [])
                    ok2, msg2 = self.card_loader.is_legal_deck(deck)
                    if ok2:
                        self._deck_lists[pid] = deck
                    else:
                        await self.send_error(
                            self._connection_for(pid), "ILLEGAL_DECK", msg2,
                            response,
                        )
                # Non-PLAYER_READY PDUs (e.g. PING) loop back to re-check.

        # Broadcast GAME_SETUP state (per program-states.md Step 4).
        for pid in gs.player_ids:
            gsu = create_game_state_update(seq_num=0, state={
                "phase": "GAME_SETUP",
                "players_ready": 2,
                "waiting_for": [],
            })
            await self.send_to(pid, gsu)

        # Populate this game's zones from the stored deck lists.  READYs
        # only store deck lists (handle_player_ready) so that the
        # _run_game_over reset can never wipe the next game's zones.
        for pid in gs.player_ids:
            gs.libraries[pid] = list(self._deck_lists.get(pid, []))
            gs.hands[pid] = []
            gs.graveyards[pid] = []
            gs.battlefield[pid] = []

        # Initialise mulligan events for this game session.
        for pid in gs.player_ids:
            self._mulligan_kept[pid] = asyncio.Event()

        # Life totals to 20.
        for pid in gs.player_ids:
            gs.life_totals[pid] = 20

        # Shuffle and draw 7 for each player.
        for pid in gs.player_ids:
            random.shuffle(gs.libraries[pid])
            gs.hands[pid] = []
            for _ in range(7):
                if gs.libraries[pid]:
                    gs.hands[pid].append(gs.libraries[pid].pop(0))

        # Coin flip for first player.
        first_player = random.choice(gs.player_ids)
        gs.active_player = first_player

        # Broadcast setup complete — transition to MULLIGAN phase.
        gs.phase = "MULLIGAN"
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

    async def _run_mulligan(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        """MULLIGAN: each player decides keep or mulligan independently."""
        gs.phase = "MULLIGAN"
        gs.mulligan_counts = {pid: 0 for pid in gs.player_ids}

        # Wait for both players to keep (each handler sets its own event).
        kept_tasks = [
            self._mulligan_kept[pid].wait()
            for pid in gs.player_ids
            if pid in self._mulligan_kept
        ]
        if kept_tasks:
            # A disconnect during MULLIGAN must not stall the lifecycle:
            # race the keep-waits against the game-over event.
            # NOTE: wait for *all* keeps (RFC §6.2) — the mulligan may
            # only end once every player has kept.  Two naive variants
            # are both wrong: FIRST_COMPLETED ends the mulligan on the
            # first keep (the second player's keep then races the turn
            # start), and ALL_COMPLETED over the union also waits for the
            # never-completing game-over task (deadlock).  Loop with
            # FIRST_COMPLETED until no keep-wait remains pending.
            keep_futures = {asyncio.ensure_future(t) for t in kept_tasks}
            game_over_task = asyncio.create_task(self._game_over.wait())
            try:
                while keep_futures:
                    done, pending = await asyncio.wait(
                        keep_futures | {game_over_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )
                    if game_over_task in done:
                        for fut in keep_futures:
                            fut.cancel()  # Discard the pending keep-waits.
                        return  # Game over interrupted the mulligan.
                    keep_futures = pending - {game_over_task}
                return  # All players kept.
            finally:
                game_over_task.cancel()

    async def _run_in_game(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        """IN_GAME: run turns until the game ends.
        The phase is preserved from the prior state (MULLIGAN) so the
        first PHASE_TRANSITION correctly shows from_phase=MULLIGAN.
        """

        while not self._game_over.is_set():
            ap_id = gs.active_player or gs.player_ids[0]
            nap_id = self._opponent(ap_id) or gs.player_ids[1]

            ap_conn = conns[self._player_index[ap_id]]
            nap_conn = conns[self._player_index[nap_id]]

            # Run one turn via the turn engine.
            await self.turn_engine.run_turn(gs, ap_id, nap_id)

            # Swap active player.
            if not self._game_over.is_set():
                gs.active_player = nap_id

    async def _run_game_over(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        """GAME_OVER: reset game state for the next game.

        The ready-state (``players_ready``, ``conn.player_id``,
        ``player_ids``, deck lists) is reset in ``_end_game`` — NOT here —
        so PLAYER_READYs arriving in response to the GAME_OVER broadcast
        are counted for the next game instead of being wiped mid-unwind.
        Only the in-game zones are reset here.
        """
        gs.phase = "LOBBY"
        gs.turn = 0
        gs.stack.clear()
        gs.hands.clear()
        gs.libraries.clear()
        gs.graveyards.clear()
        gs.battlefield.clear()
        gs.life_totals.clear()
        gs.land_played_this_turn = False
        gs.mulligan_counts.clear()
        gs.stack_counter = 0
        gs.mana_pools = {}
        gs._draw_failed_for = None
        gs._cleanup_discard_for = None
        self.stack_mgr.clear_cache()
        self.combat_mgr.reset()

    # ═══════════════════════════════════════════════════════════════════════════
    # Turn engine callbacks
    # ═══════════════════════════════════════════════════════════════════════════

    async def _on_phase(
        self, gs: GameState, ap_id: str, nap_id: str, phase: str
    ) -> None:
        """Called by TurnEngine for each non-auto phase."""
        gs.phase = phase

        if phase == "DECLARE_BLOCKERS":
            gs.priority_holder = nap_id
        else:
            gs.priority_holder = ap_id

        # Broadcast a GAME_STATE_UPDATE first.
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

        # ── Combat sub-steps ─────────────────────────────────────────────
        if phase == "DECLARE_ATTACKERS":
            # Let the priority manager handle the UI prompt properly!
            try:
                both, action = await self.priority_mgr.run_priority_window(
                    self._connection_for(ap_id),
                    self._connection_for(nap_id),
                    ap_id, nap_id,
                    read_pdu=self.wait_for_pdu,
                )
            except GameOverInterrupt:
                return
            
            # If they typed "attack ..." or "no attacks", this catches it!
            if action and action.get("type") == "DECLARE_ATTACKERS":
                attackers = action.get("attackers", [])
                self.combat_mgr.set_attackers(gs, ap_id, attackers)

                # RFC §11: every illegal declaration MUST be answered with
                # ERROR ILLEGAL_ACTION (e.g. attacking with a tapped or
                # summoning-sick creature) instead of a silent drop.
                if self.combat_mgr.rejected_attackers:
                    reasons = "; ".join(
                        f"{r['creature_id']}: {r['reason']}"
                        for r in self.combat_mgr.rejected_attackers
                    )
                    await self.send_error(
                        self._connection_for(ap_id),
                        "ILLEGAL_ACTION",
                        f"Invalid attackers: {reasons}.",
                        action,
                    )

                # RFC §8.6.1: attack triggers (Goblin Guide) go on the stack.
                await self._maybe_push_attack_triggers(gs, ap_id, nap_id)
                
            await self._broadcast_game_state(gs) 

            if not self.combat_mgr.attackers:
                # Program-states.md step 18: with no attackers declared, skip
                # Declare Blockers, Assign Damage Order, and Combat Damage,
                # advancing directly to End of Combat.
                gs._skip_to_phase = "END_OF_COMBAT"
                return

            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        if phase == "DECLARE_BLOCKERS":
            try:
                both, action = await self.priority_mgr.run_priority_window(
                    self._connection_for(nap_id),
                    self._connection_for(ap_id),
                    nap_id, ap_id,
                    read_pdu=self.wait_for_pdu,
                )
            except GameOverInterrupt:
                return
            
            if action and action.get("type") == "DECLARE_BLOCKERS":
                blockers = action.get("blockers", [])
                self.combat_mgr.set_blockers(gs, nap_id, blockers)
                
            await self._broadcast_game_state(gs)
                
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        if phase == "ASSIGN_DAMAGE_ORDER":
            # Ask AP via priority window to order blockers for each
            # multi-blocked attacker (RFC §8.3).
            multi_blocked = [
                a_id for a_id in self.combat_mgr.attackers
                if len([b for b, a in self.combat_mgr.blockers.items()
                       if a == a_id]) > 1
            ]
            for a_id in multi_blocked:
                expected_blockers = {
                    b for b, a in self.combat_mgr.blockers.items()
                    if a == a_id
                }
                conn = self._connection_for(ap_id)
                try:
                    response = await self.priority_mgr.grant_priority(
                        conn, ap_id, read_pdu=self.wait_for_pdu,
                    )
                except GameOverInterrupt:
                    return
                except (PriorityTimeout, ConnectionLost):
                    await self._end_game(
                        gs, "DISCONNECT",
                        self._opponent(ap_id) or "", ap_id,
                    )
                    return
                if response and response.get("type") == "ASSIGN_DAMAGE_ORDER":
                    r_aid = response.get("attacker_id", "")
                    order = response.get("blocker_order", [])
                    if r_aid == a_id and set(order) == expected_blockers:
                        self.combat_mgr.set_damage_order(a_id, order)
                        continue
                # Fallback: auto-assign if player didn't provide valid order.
                self.combat_mgr.set_damage_order(a_id, list(expected_blockers))
            return

        if phase == "FIRST_STRIKE_DAMAGE":
            # RFC §9.6: this step is OPTIONAL — it only occurs if at least
            # one attacking or blocking creature has first/double strike.
            if not self.combat_mgr.has_first_strike_participants(gs):
                gs._skip_to_phase = "COMBAT_DAMAGE"
                return

            result = self.combat_mgr.compute_first_strike_damage(gs)
            
            check_state_based_actions(gs, self.card_loader)
            
            await self._broadcast_combat_result(gs, result)
            
            await self._broadcast_game_state(gs)

            # Priority window after first strike damage.
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        if phase == "COMBAT_DAMAGE":
            # 1. Do the damage math
            result = self.combat_mgr.compute_combat_damage(gs)
            
            # 2. Run the sweep (mutates `gs` by moving dead creatures to graveyard)
            check_state_based_actions(gs, self.card_loader)
            
            # 3. Broadcast the combat results (like damage numbers)
            await self._broadcast_combat_result(gs, result)
            
            # 4. Broadcast the FULL updated game state to update the clients' UI!
            await self._broadcast_game_state(gs)
            
            self.combat_mgr.reset()
            
            # Priority window after combat damage.
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        # ── CLEANUP discard handling (RFC §7.8) ─────────────────────────
        # No priority window at cleanup: the server sends GAME_STATE_UPDATE
        # and awaits DISCARD directly (see _run_cleanup_discard).
        if phase == "CLEANUP" and gs._cleanup_discard_for is not None:
            await self._run_cleanup_discard(gs, gs._cleanup_discard_for)
            return

        # ── Phases with priority ────────────────────────────────────────
        if phase == "CLEANUP":
            # RFC §7.8: no priority is given at cleanup (and no triggers
            # fire in MTGNP 1.0).  Hand-size discard is handled above.
            return

        await self._run_priority_loop(gs, ap_id, nap_id)

    async def _run_cleanup_discard(self, gs: GameState, pid: str) -> None:
        """RFC §7.8 cleanup: no priority window.

        While the player's hand exceeds 7, send GAME_STATE_UPDATE and await
        a DISCARD PDU (echoing that GSU's seq_num); reject invalid discards
        with ERROR ILLEGAL_ACTION; repeat until the hand is ≤ 7.  Then
        broadcast the final state to BOTH players.
        """
        conn = self._connection_for(pid)
        timeout_s = self.priority_mgr.config.time_limit_ms / 1000.0

        while len(gs.hands.get(pid, [])) > 7:
            # Send the updated state first; the DISCARD echoes this seq.
            vs = build_visible_state(gs, pid)
            gsu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, gsu)

            try:
                response = await asyncio.wait_for(
                    self.wait_for_pdu(pid, timeout_s), timeout=timeout_s
                )
            except GameOverInterrupt:
                return
            except (asyncio.TimeoutError, PriorityTimeout, ConnectionLost,
                    ConnectionError):
                await self._end_game(
                    gs, "DISCONNECT", self._opponent(pid) or "", pid
                )
                return

            if not response or response.get("type") != "DISCARD":
                await self.send_error(
                    conn, "ILLEGAL_ACTION",
                    "Expected a DISCARD PDU during cleanup.", response or {},
                )
                continue

            card_ids = response.get("card_ids", [])
            ok, code, msg = validate_discard(gs, pid, card_ids)
            if not ok:
                await self.send_error(conn, code or "ILLEGAL_ACTION", msg, response)
                continue

            hand = gs.hands.get(pid, [])
            for cid in card_ids:
                if cid in hand:
                    hand.remove(cid)
                    gs.graveyards.setdefault(pid, []).append(cid)

        gs._cleanup_discard_for = None

        # RFC §7.8: broadcast the final state to BOTH players.
        for p in gs.player_ids:
            vs = build_visible_state(gs, p)
            gsu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(p, gsu)

    async def _on_advance(
        self, gs: GameState, from_phase: str, to_phase: str
    ) -> None:
        """Broadcast a PHASE_TRANSITION PDU."""
        ap_id = gs.active_player or (gs.player_ids[0] if gs.player_ids else "")
        pdu = create_phase_transition(
            seq_num=0,
            from_phase=from_phase,
            to_phase=to_phase,
            active_player=ap_id,
            turn=gs.turn,
        )
        await self.broadcast(pdu)

    async def _run_priority_loop(
        self, gs: GameState, ap_id: str, nap_id: str
    ) -> None:
        """Loop priority windows until both pass on empty stack (advance)
        or both pass on non-empty stack (resolve top)."""

        actor: str | None = None

        while not self._game_over.is_set():

            sba_changes = check_state_based_actions(gs, self.card_loader)
            if sba_changes:
                await self._broadcast_game_state(gs)
                if self._check_game_over(gs):
                    return

            # RFC §8.1.3: a player who casts a spell / activates an ability
            # retains priority — the next window opens with THEM, not
            # automatically with the Active Player.
            first_id, second_id = ap_id, nap_id
            if actor is not None:
                first_id, second_id = actor, (nap_id if actor == ap_id else ap_id)

            needs_broadcast = (gs.priority_holder != first_id)
            gs.priority_holder = first_id

            if needs_broadcast:
                await self._broadcast_game_state(gs)

            async def flip_to_second():
                gs.priority_holder = second_id
                await self._broadcast_game_state(gs)

            try:
                both_passed, action, actor = await self.priority_mgr.run_priority_window(
                    self._connection_for(first_id),
                    self._connection_for(second_id),
                    first_id, second_id,
                    read_pdu=self.wait_for_pdu,
                    on_ap_pass_cb=flip_to_second,
                )
            except GameOverInterrupt:
                return
            except PriorityTimeout as exc:
                winner = second_id if exc.player_id == first_id else first_id
                await self._end_game(gs, "DISCONNECT", winner, exc.player_id)
                return
            except ConnectionLost as exc:
                winner = second_id if exc.player_id == first_id else first_id
                await self._end_game(gs, "DISCONNECT", winner, exc.player_id)
                return

            if both_passed:
                if self.stack_mgr.is_empty(gs):
                    if self._check_game_over(gs):
                        return
                    break  # Advance to next phase.
                else:
                    # Resolve top of stack — capture ID before popping.
                    resolved_id = gs.stack[-1].stack_item_id if gs.stack else ""
                    result, changes = self.stack_mgr.resolve_top(
                        gs, card_loader=self.card_loader,
                    )
                    pdu = create_stack_resolve(
                        seq_num=0,
                        stack_item_id=resolved_id,
                        result=result,
                        state_changes=changes,
                    )
                    await self.broadcast(pdu)
                    # Also broadcast updated game state.
                    await self._broadcast_game_state(gs)
                    if self._check_game_over(gs):
                        return
            elif action is not None:
                await self._process_action(gs, ap_id, nap_id, action)
                if self._check_game_over(gs):
                    return

    # ═══════════════════════════════════════════════════════════════════════════
    # Broadcast helpers
    # ═══════════════════════════════════════════════════════════════════════════

    async def _broadcast_game_state(self, gs: GameState) -> None:
        """Send personalised GAME_STATE_UPDATE to each player."""
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

    async def _broadcast_combat_result(
        self, gs: GameState, result: dict[str, Any]
    ) -> None:
        """Broadcast COMBAT_DAMAGE_RESULT and update life totals."""
        new_life = result.get("life_totals", {})
        gs.life_totals.update(new_life)

        from shared.pdus import create_combat_damage_result
        pdu = create_combat_damage_result(
            seq_num=0,
            damage_events=result.get("damage_events", []),
            life_totals=gs.life_totals,
            creatures_died=result.get("creatures_died", []),
        )
        await self.broadcast(pdu)
        # Also broadcast updated game state.
        await self._broadcast_game_state(gs)
        self._check_game_over(gs)

    # ═══════════════════════════════════════════════════════════════════════════
    # Action processing
    # ═══════════════════════════════════════════════════════════════════════════

    async def _process_action(
        self, gs: GameState, ap_id: str, nap_id: str, action: dict[str, Any]
    ) -> None:
        """Process an action taken during a priority window."""
        atype = action.get("type", "")
        print(f"\nBRAIN RECEIVED IT: {action}")
        pid = action.get("_player_id", ap_id)

        if atype == "CAST_SPELL":
            card_id = action.get("card_id", "")
            targets = action.get("targets", [])
            mana_payment = action.get("mana_payment", {})

            ok, code, msg = validate_cast_spell(
                gs, pid, card_id, targets, mana_payment,
                card_loader=self.card_loader,
            )
            if not ok:
                await self.send_error(
                    self._connection_for(pid),
                    code or "ILLEGAL_ACTION", msg, action,
                )
                return

            try:
                pool = gs.mana_pools.setdefault(pid, ManaPool.empty())
                gs.mana_pools[pid] = deduct_mana(mana_payment, pool)
            except ValueError:
                await self.send_error(
                    self._connection_for(pid),
                    "INSUFFICIENT_MANA", "Failed to deduct mana.", action,
                )
                return

            hand = gs.hands.get(pid, [])
            if card_id in hand:
                hand.remove(card_id)

            card_def = self.card_loader.get_card(card_id)
            si = self.stack_mgr.push(
                gs, "SPELL", card_id, pid, targets, card_def
            )
            pdu = create_stack_push(
                seq_num=0,
                stack_item_id=si.stack_item_id,
                item_type="SPELL",
                source=card_id,
                targets=targets,
                controller=pid,
            )
            await self.broadcast(pdu)

            # RFC §8.6.1: prowess triggers (Monastery Swiftspear) fire when
            # a noncreature spell is cast.
            await self._maybe_push_cast_triggers(gs, pid, card_def)

        elif atype == "PLAY_LAND":
            print("\n🚪 ENTERED PLAY_LAND BLOCK")
            try:
                card_id = action.get("card", action.get("card_id", ""))

                pid = action.get("player_id") or gs.priority_holder
                
                ok, code, msg = validate_play_land(gs, pid, card_id, self.card_loader)
                
                if not ok:
                    await self.send_error(
                        self._connection_for(pid),
                        code or "ILLEGAL_ACTION", msg, action,
                    )
                    return

                # 2. Remove from hand
                hand = gs.hands.get(pid, [])
                if card_id in hand:
                    hand.remove(card_id)
                
                # 3. Strip instance ID to load stats safely
                base_id = card_id
                if "_" in card_id:
                    parts = card_id.rsplit("_", 1)
                    if parts[1].isdigit():
                        base_id = parts[0]
                        
                cd = self.card_loader.get_card(base_id)
                power = cd.power if cd and getattr(cd, 'power', None) else 0
                toughness = cd.toughness if cd and getattr(cd, 'toughness', None) else 0
                
                def_id = base_id
                if cd:
                    for attr in ['card_def_id', 'id', 'name', 'card_id']:
                        if hasattr(cd, attr) and getattr(cd, attr):
                            def_id = getattr(cd, attr)
                            break

                perm = Permanent(
                    id=card_id,
                    card_def_id=def_id,
                    controller=pid,
                    tapped=False,
                    power=power or 0,
                    toughness=toughness or 0,
                    summoning_sick=False,
                )
                
                gs.battlefield.setdefault(pid, []).append(perm)
                gs.land_played_this_turn = True
                
                print(f"\n✅ SUCCESS: Added {card_id} to board!")
                
                # 5. Broadcast the new state to clients
                await self._broadcast_game_state(gs)
                
            except Exception as e:
                import traceback
                print(f"\n🚨 FATAL ENGINE CRASH IN PLAY_LAND:")
                traceback.print_exc()

        elif atype == "ACTIVATE_ABILITY":
            source_id = action.get("source_id", "")
            ability_index = action.get("ability_index", 0)
            
            # 1. Find the permanent on the battlefield
            perm = None
            for p in gs.battlefield.get(pid, []):
                if p.id == source_id:
                    perm = p
                    break
            
            if not perm:
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION", f"Permanent '{source_id}' not found.", action,
                )
                return

            if perm.tapped:
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION", f"'{source_id}' is already tapped.", action,
                )
                return

            # 2. Get the card definition to find what it produces
            base_id = source_id
            if "_" in source_id:
                parts = source_id.rsplit("_", 1)
                if parts[1].isdigit():
                    base_id = parts[0]
            cd = self.card_loader.get_card(base_id)
            
            if not cd or ability_index >= len(cd.abilities):
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION", f"Invalid ability index {ability_index}.", action,
                )
                return
                
            ability = cd.abilities[ability_index]
            
            # 3. Apply the cost (tapping)
            if ability.get("requires_tap"):
                perm.tapped = True

            # 4. Generate the mana! (into the activating player's own pool)
            produces = ability.get("produces", {})
            if not produces:
                # Not a mana ability.  MTGNP 1.0 does not implement combat
                # tap-abilities (Prodigal Sorcerer, Royal Assassin, ...);
                # answer explicitly instead of silently tapping with no
                # effect — and roll the tap back.
                perm.tapped = False
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION",
                    f"Ability '{ability.get('name', '')}' on '{source_id}' is "
                    "not implemented in MTGNP 1.0.",
                    action,
                )
                return

            pool = gs.mana_pools.setdefault(pid, ManaPool.empty())
            for color, amount in produces.items():
                current = getattr(pool, color, 0)
                setattr(pool, color, current + amount)

            print(f"💧 MANA ADDED! Pool is now: W:{pool.W} U:{pool.U} B:{pool.B} R:{pool.R} G:{pool.G} C:{pool.C}")
            
            # Broadcast the state update so the client sees the tapped land
            await self._broadcast_game_state(gs)

    # ═══════════════════════════════════════════════════════════════════════════
    # Triggered abilities (RFC §8.6.1)
    # ═══════════════════════════════════════════════════════════════════════════

    async def _maybe_push_attack_triggers(
        self, gs: GameState, ap_id: str, nap_id: str
    ) -> None:
        """Put ATTACKS triggers (Goblin Guide) on the stack for every
        attacking permanent whose registry entry fires on that event."""
        from server.card_effects import check_triggers

        for cid in list(self.combat_mgr.attackers):
            perm = None
            for perms in gs.battlefield.values():
                for p in perms:
                    if p.id == cid:
                        perm = p
                        break
            if perm is None:
                continue
            for trg in check_triggers(gs, "ATTACKS", cid, ap_id):
                if trg["source"] != cid:
                    continue  # only the attacking permanent triggers
                si = self.stack_mgr.push_trigger(
                    gs, f"{perm.card_def_id}_trigger", ap_id,
                    targets=[nap_id], source_permanent=cid,
                )
                pdu = create_stack_push(
                    seq_num=0,
                    stack_item_id=si.stack_item_id,
                    item_type="TRIGGER_ABILITY",
                    source=perm.card_def_id,
                    targets=[nap_id],
                    controller=ap_id,
                )
                await self.broadcast(pdu)

    async def _maybe_push_cast_triggers(
        self, gs: GameState, pid: str, card_def: Any
    ) -> None:
        """Put CAST_NONCREATURE_SPELL triggers (Monastery Swiftspear
        prowess) on the stack when a noncreature spell is cast."""
        from server.card_effects import check_triggers

        ctype = getattr(card_def, "card_type", "") if card_def else ""
        if "creature" in ctype.lower():
            return
        for trg in check_triggers(gs, "CAST_NONCREATURE_SPELL", "", pid):
            si = self.stack_mgr.push_trigger(
                gs, "monastery_swiftspear_trigger", pid,
                targets=[], source_permanent=trg["source"],
            )
            pdu = create_stack_push(
                seq_num=0,
                stack_item_id=si.stack_item_id,
                item_type="TRIGGER_ABILITY",
                source="monastery_swiftspear",
                targets=[],
                controller=pid,
            )
            await self.broadcast(pdu)

    # ═══════════════════════════════════════════════════════════════════════════
    # PDU handler methods (called by dispatcher)
    # ═══════════════════════════════════════════════════════════════════════════

    # ── LOBBY ────────────────────────────────────────────────────────────

    async def handle_player_ready(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process PLAYER_READY in LOBBY state."""
        player_id = pdu.get("player_id", "")
        deck_list = pdu.get("deck_list", [])

        # 1. RECONNECT BYPASS — only applies while a game is LIVE.  Once
        # GAME_OVER has been broadcast (_game_over set), PLAYER_READYs are
        # for the next game even though the engine is still unwinding
        # (gs.phase is still the last in-game phase).
        if self.gs.phase != "LOBBY" and not self._game_over.is_set():
            # If this socket already has an ID assigned by the Reconnect Watcher, 
            # they are just rejoining. Silently ignore this amnesia packet.
            if conn.player_id is not None:
                # GAME_SETUP re-ready: the player was told their deck is
                # illegal (ERROR ILLEGAL_DECK) and is retrying with a
                # corrected list (RFC §11).
                if self.gs.phase == "GAME_SETUP":
                    deck_list = pdu.get("deck_list", [])
                    ok, msg = self.card_loader.is_legal_deck(deck_list)
                    if ok:
                        self._deck_lists[conn.player_id] = deck_list
                        print(f"[LOBBY] {conn.player_id} re-readied with a corrected deck.")
                    else:
                        await self.send_error(
                            conn, "ILLEGAL_DECK", msg, pdu,
                        )
                return
                
            # Otherwise, it's a completely new connection trying to join mid-game
            await self.send_error(conn, "ILLEGAL_ACTION",
                                  "Not in LOBBY state.", pdu)
            return

        # 2. RUBRIC REQUIREMENT: Non-empty player_id
        if not player_id:
            await self.send_error(conn, "ILLEGAL_ACTION", 
                                  "player_id cannot be empty.", pdu)
            return

        # 3. RUBRIC REQUIREMENT: Deck size 1-50
        if not (1 <= len(deck_list) <= 50):
            await self.send_error(conn, "ILLEGAL_DECK", 
                                  "Deck must contain between 1 and 50 cards.", pdu)
            return

        # 4. RUBRIC REQUIREMENT: Check duplicate player_id (DUPLICATE_ID)
        for c in self.connections:
            if c is not conn and c.player_id == player_id:
                await self.send_error(conn, "DUPLICATE_ID",
                                      f"Player ID '{player_id}' already claimed.", pdu)
                return

        # 5. RUBRIC REQUIREMENT: Validate deck cards exist (ILLEGAL_DECK)
        ok, err_code, msg = validate_deck(player_id, deck_list, self.card_loader)
        if not ok:
            await self.send_error(conn, err_code or "ILLEGAL_DECK", msg, pdu)
            return

        # Accept — if player already submitted, replace deck, don't inflate.
        gs = self.gs
        if conn.player_id is None:
            conn.player_id = player_id
            gs.players_ready += 1
        # Store ONLY the deck list here.  The in-game zones (libraries,
        # hands, graveyards, battlefield) are populated by _run_setup:
        # _run_game_over clears them after the previous game, and READYs
        # may arrive while that reset is still pending — repopulating
        # them here would let the reset wipe the next game's decks.
        self._deck_lists[player_id] = list(deck_list)
        gs.waiting_for = [
            c.player_id for c in self.connections if c.player_id is None
        ]

        # Acknowledge.
        gsu = create_game_state_update(seq_num=0, state={
            "phase": "LOBBY",
            "players_ready": gs.players_ready,
            "waiting_for": gs.waiting_for,
        })
        await self.send_to(player_id, gsu)

    # ── MULLIGAN ─────────────────────────────────────────────────────────

    async def handle_mulligan_choice(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process MULLIGAN_CHOICE."""
        player_id = conn.player_id
        if not player_id:
            return

        # Only process MULLIGAN_CHOICE during MULLIGAN phase.
        if self.gs.phase != "MULLIGAN":
            await self.send_error(
                conn, "ILLEGAL_ACTION",
                "Not in MULLIGAN phase.", pdu,
            )
            return

        # The game may have ended (CONCEDE/disconnect) while this PDU was
        # in flight — _end_game clears the mulligan bookkeeping.  Reject
        # BEFORE touching the (possibly reset) game state: validate/process
        # on cleared mulligan_counts would raise, and re-seeding
        # _mulligan_expected_seq would STALE-reject the next game's first
        # MULLIGAN_CHOICE and hang the keep-wait forever.
        if self._game_over.is_set():
            return

        # Validate seq_num echoes the last GAME_STATE_UPDATE (RFC §5.4).
        expected = self._mulligan_expected_seq.get(player_id)
        if expected is not None:
            actual = pdu.get("seq_num", -1)
            if actual != expected:
                await self.send_error(
                    conn, "STALE_ACTION",
                    f"MULLIGAN_CHOICE seq_num mismatch. "
                    f"Expected {expected}, got {actual}.",
                    pdu,
                )
                return

        keep = pdu.get("keep", False)
        cards_to_bottom = pdu.get("cards_to_bottom", [])

        ok, code, msg = validate_mulligan(
            self.gs, player_id, keep, cards_to_bottom
        )
        if not ok:
            await self.send_error(conn, code or "ILLEGAL_ACTION", msg, pdu)
            return

        process_mulligan_choice(self.gs, player_id, keep, cards_to_bottom)

        vs = build_visible_state(self.gs, player_id)
        gsu = create_game_state_update(seq_num=0, state=vs)
        await self.send_to(player_id, gsu)

        # The game may have ended (CONCEDE/disconnect) DURING the
        # confirmation send — _end_game clears the mulligan bookkeeping
        # mid-flight.  Re-check between the await and the bookkeeping:
        # re-seeding _mulligan_expected_seq after the reset would
        # STALE-reject the next game's first MULLIGAN_CHOICE and hang the
        # keep-wait forever, and touching the reset _mulligan_kept dict
        # would KeyError-kill the read loop.
        if self._game_over.is_set():
            return
        # Record seq_num for MULLIGAN_CHOICE echo validation.
        self._mulligan_expected_seq[player_id] = conn.seq_num

        if keep:
            kept = self._mulligan_kept.get(player_id)
            if kept is not None and not kept.is_set():
                kept.set()

    # ── IN_GAME — priority-bearing actions ───────────────────────────────

    async def handle_priority_pass(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process PRIORITY_PASS — no-op; handled via priority-wait path."""
        pass

    async def handle_cast_spell(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process CAST_SPELL — no-op; handled via priority-wait path."""
        pass

    async def handle_activate_ability(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process ACTIVATE_ABILITY — validated and processed as a priority action."""
        # This action is handled via the priority-wait path (read_pdu callback)
        # in _process_action.  The stub handler here is for the dispatcher path
        # when the PDU arrives outside a priority window.
        pass

    async def handle_play_land(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        print(f"\n🗑️ GARBAGE CAN ATE IT: {pdu}")
        pass

    async def handle_declare_attackers(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_declare_blockers(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_assign_damage_order(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_discard(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process DISCARD — handled via CLEANUP priority-window in _on_phase."""
        pass

    async def handle_trigger_order_response(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_trigger_choice_response(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    # ── Any phase ────────────────────────────────────────────────────────

    async def handle_concede(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process CONCEDE — immediate GAME_OVER."""
        player_id = conn.player_id or pdu.get("player_id", "?")
        winner = self._opponent(player_id)
        if winner:
            await self._end_game(self.gs, "CONCEDE", winner, player_id)

    async def handle_ping(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process PING — respond with PONG."""
        seq = pdu.get("seq_num", 0)
        ts = pdu.get("timestamp", 0)
        pong = create_pong(seq_num=seq, timestamp=ts)
        await conn.send_pdu(pong)

    # ═══════════════════════════════════════════════════════════════════════════
    # Send / broadcast helpers
    # ═══════════════════════════════════════════════════════════════════════════

    async def send_to(self, player_id: str, pdu: dict[str, Any]) -> None:
        """Send a PDU to a specific player."""
        for conn in self.connections:
            if conn.player_id == player_id:
                await conn.send_pdu(pdu)
                return

    async def broadcast(self, pdu: dict[str, Any]) -> None:
        """Send a PDU to all connected players.

        Best-effort per connection: a socket that dies mid-send (RST
        during drain — not yet flagged ``_closed``) must not abort the
        broadcast, or the surviving players would never receive the PDU
        (e.g. GAME_OVER after a CONCEDE).  ``send_pdu`` already sets
        ``_closed`` on write failures.
        """
        for conn in self.connections:
            if conn.player_id and not getattr(conn, "_closed", False):
                try:
                    await conn.send_pdu(pdu)
                except (ConnectionError, OSError):
                    continue  # Best-effort — this socket is dead anyway.

    async def send_error(
        self,
        conn: ServerConnection,
        code: str,
        message: str,
        rejected_action: dict[str, Any],
    ) -> None:
        """Send an ERROR PDU to a specific connection.

        Per RFC §10.2.23 the ERROR's seq_num echoes the rejected action's
        seq_num when available (and does not consume a counter value, so
        the priority token stays valid for a retry — RFC §11.3).
        """
        pdu = create_error(
            seq_num=0, code=code, message=message,
            rejected_action=rejected_action,
        )
        seq = rejected_action.get("seq_num") if isinstance(rejected_action, dict) else None
        if seq is not None:
            await conn.send_pdu_explicit(pdu, seq)
        else:
            await conn.send_pdu(pdu)

    async def _end_game(
        self,
        gs: GameState,
        reason: str,
        winner_id: str,
        loser_id: str,
    ) -> None:
        """Broadcast GAME_OVER and signal the game loop to stop.

        The broadcast happens FIRST (while ``conn.player_id`` values are
        still intact — ``broadcast()`` skips connections without an id),
        then the ready-state is reset so PLAYER_READYs sent in response
        to GAME_OVER (which may arrive while the engine is still
        unwinding) are counted for the next game's lobby.

        ``_game_over`` is set synchronously at entry — BEFORE the first
        await — so the dedupe is atomic: concurrent callers (concede +
        watchdog + timeout) see it set and return, and a broadcast send
        failure can never leave the game unflagged (no delayed unwinding
        into a watchdog DISCONNECT re-broadcast, and no double GAME_OVER).
        """
        if self._game_over.is_set():
            return  # Already ending — dedupe concurrent game-over sources.

        # Claim the ending synchronously (atomic with respect to other
        # coroutines) before any await.
        self._game_over.set()

        pdu = create_game_over(
            seq_num=0,
            winner_id=winner_id,
            loser_id=loser_id,
            reason=reason,
        )
        try:
            await self.broadcast(pdu)
        finally:
            # Reset ready-state for the next game.  (Pure synchronous
            # operations — cannot mask the broadcast's exception.)
            gs.players_ready = 0
            gs.player_ids.clear()
            gs.waiting_for = []
            for conn in self.connections:
                conn.player_id = None
            self._deck_lists.clear()
            self._player_index.clear()
            self._mulligan_kept.clear()
            self._mulligan_expected_seq.clear()
            gs.mulligan_counts.clear()

    # ═══════════════════════════════════════════════════════════════════════════
    # Internal helpers
    # ═══════════════════════════════════════════════════════════════════════════

    def _connection_for(self, player_id: str) -> ServerConnection:
        """Return the ServerConnection for *player_id*."""
        for conn in self.connections:
            if conn.player_id == player_id:
                return conn
        return self.connections[0]

    def _opponent(self, player_id: str) -> str | None:
        """Return the opponent's player ID, or ``None``."""
        for pid in self.gs.player_ids:
            if pid != player_id:
                return pid
        return None

    def _check_game_over(self, gs: GameState) -> bool:
        """Check win/loss conditions.  Returns True if game is over."""
        for pid in gs.player_ids:
            if gs.life_totals.get(pid, 20) <= 0:
                winner = self._opponent(pid) or ""
                # Store the task: an un-stored ensure_future can be GC'd
                # mid-run, silently losing the game-over broadcast.
                self._pending_end_task = asyncio.ensure_future(
                    self._end_game(gs, "LIFE_ZERO", winner, pid)
                )
                self._pending_end_task.add_done_callback(
                    _retrieve_task_exception
                )
                return True
            # DECK_EMPTY: draw from empty library.
            if gs._draw_failed_for == pid:
                gs._draw_failed_for = None  # Clear after consuming.
                winner = self._opponent(pid) or ""
                self._pending_end_task = asyncio.ensure_future(
                    self._end_game(gs, "DECK_EMPTY", winner, pid)
                )
                self._pending_end_task.add_done_callback(
                    _retrieve_task_exception
                )
                return True
        return False
