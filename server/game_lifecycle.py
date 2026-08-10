from __future__ import annotations

import asyncio
import random
import sys
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
    """
    Raised inside the engine when the game ends while awaiting a PDU.
    """


def _retrieve_task_exception(task: "asyncio.Task") -> None:
    if task.cancelled():
        return
    exc = task.exception()
    if exc is not None:
        print(f"[server] fire-and-forget task failed: {exc!r}",
              file=sys.stderr)


class GameLifecycle:
    def __init__(
        self,
        config: ServerConfig,
        card_loader: CardLoader,
        connections: list[ServerConnection],
    ) -> None:
        self.config = config
        self.card_loader = card_loader
        self.connections = connections
        self.gs = GameState()
        self.stack_mgr = StackManager()
        self.priority_mgr = PriorityManager(config)
        self.combat_mgr = CombatManager()
        self.turn_engine = TurnEngine(
            phase_handler=self._on_phase,
            advance_handler=self._on_advance,
        )

        # used to route PDUs during priority windows, keyed by player_id
		# one future per player at a time
        self._pending_pdu: dict[str, asyncio.Future[dict]] = {}

        # keep _end_game task spawned from _check_game_over
		# ensure it cannot be GC'd mid-run
        self._pending_end_task: asyncio.Task | None = None

        # async safety lock 
        self._lock = asyncio.Lock()

        # lookup map for player_id to player index, either 0 or 1
        self._player_index: dict[str, int] = {}

        # global state 
        self._game_over = asyncio.Event()

        # stored decks, keyed by player_id
        self._deck_lists: dict[str, list[str]] = {}

        # per-player asyncio.Event that fires when the player keeps their hand
        self._mulligan_kept: dict[str, asyncio.Event] = {}

        # sequence nums tracking for MULLIGAN_CHOICE echo validation
        self._mulligan_expected_seq: dict[str, int] = {}

    #wait for pdu from player
    async def wait_for_pdu(
        self, player_id: str, timeout: float
    ) -> dict[str, Any]:
		# make a future for the player's incoming move
        loop = asyncio.get_running_loop()
        future = loop.create_future()
        self._pending_pdu[player_id] = future

		# spawn background task just in case game_over
        game_over_task = asyncio.create_task(self._game_over.wait())
        try:

			# wait for three outcomes: player sends a packet, doesnt send a packet
			# or gameover from conceding, disconnecting, or lost
            done, _ = await asyncio.wait(
                {asyncio.ensure_future(future), game_over_task},
                return_when=asyncio.FIRST_COMPLETED,
                timeout=timeout,
            )

			# if future is fulfilled, attach sender as player_id
            if future in done:
                pdu = future.result()
                pdu["_player_id"] = player_id
                return pdu

            # if it's gameover, raise the interrupt
            if self._game_over.is_set():
                raise GameOverInterrupt()

			# well if not, player timed out
			# engine now decides what to do with that
            raise asyncio.TimeoutError()
        finally:
			# remove leaking bg task
            game_over_task.cancel()

			# remove pending pdu handler, so that other packets 
			# don't go here
            if self._pending_pdu.get(player_id) is future:
                self._pending_pdu.pop(player_id, None)

        # Public entry point
    
    #run the entire life cycle
    async def run(self) -> None:
		# get connections
		# if connections are less than two, bail
        conns = self.connections
        if len(conns) < 2:
            return

		# reset player ids so lobby can track READY handshakes
        for conn in conns:
            conn.player_id = None

        # keep network read loop continuously for the entire session
        read_tasks = [asyncio.create_task(conn.read_loop()) for conn in conns]
        try:
            while True:
				# start new game fresh
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

                # start core turn engine
                await self._run_in_game(gs, conns)

				# wait for game over
                await self._run_game_over(gs, conns)
        finally:
			# remove bg tasks
            for task in read_tasks:
                task.cancel()

        # Lifecycle state runners
    
    #wait for 2 players to enter
    async def _run_lobby(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        gs.phase = "LOBBY"

		# wait until 2 plays are ready.
        while gs.players_ready < 2 and not self._game_over.is_set():
            await asyncio.sleep(0.1)  # Yield - dispatcher updates state.

        # if game_over, break the while loop
        if self._game_over.is_set():
            return

        # assign player IDs in connection order.
        for conn in conns:
            if conn.player_id:
                gs.player_ids.append(conn.player_id)
                self._player_index[conn.player_id] = len(gs.player_ids) - 1

    # shuffle, draw 7, coinflip
    async def _run_setup(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
		# set phase to game_setup
        gs.phase = "GAME_SETUP"

		# set timeout in seconds, derived from prio mgr's miliseconds
        timeout_s = self.priority_mgr.config.time_limit_ms / 1000.0
        
		# validates both deck lists using stored lists.
        for pid in gs.player_ids:
            while True:
                deck = self._deck_lists.get(pid, [])
                ok, msg = self.card_loader.is_legal_deck(deck)
                if ok:
                    break
				# an invalid deck is answered with ERROR ILLEGAL_DECK
                # (GAME_OVER reasons are WIN/LOSS/CONCEDE/DISCONNECT only).
                await self.send_error(
                    self._connection_for(pid), "ILLEGAL_DECK", msg,
                    {"type": "PLAYER_READY"},
                )
                # wait for a corrected deck; a disconnect ends the game.
				# re-ready is handled in handle_player_ready
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

				# if player is ready
                if response and response.get("type") == "PLAYER_READY":
                    deck = response.get("deck_list", [])

					# second check
                    ok2, msg2 = self.card_loader.is_legal_deck(deck)
                    if ok2:
                        self._deck_lists[pid] = deck
                    else:
                        await self.send_error(
                            self._connection_for(pid), "ILLEGAL_DECK", msg2,
                            response,
                        )

        # broadcast GAME_SETUP state
		# server is setting up
        for pid in gs.player_ids:
            gsu = create_game_state_update(seq_num=0, state={
                "phase": "GAME_SETUP",
                "players_ready": 2,
                "waiting_for": [],
            })
            await self.send_to(pid, gsu)

        # copy cards from self._desk_lists to gs.libraries
		# if you modify or pop cards from libraries, it doesn't 
		# change the source material (_deck_lists)
        for pid in gs.player_ids:
            gs.libraries[pid] = list(self._deck_lists.get(pid, []))
            gs.hands[pid] = []
            gs.graveyards[pid] = []
            gs.battlefield[pid] = []

        # initialise mulligan events for this game session
        for pid in gs.player_ids:
            self._mulligan_kept[pid] = asyncio.Event()

        # life totals to 20
        for pid in gs.player_ids:
            gs.life_totals[pid] = 20

        # shuffle and draw 7 cards for each player
        for pid in gs.player_ids:
            random.shuffle(gs.libraries[pid])
            gs.hands[pid] = []
            for _ in range(7):
                if gs.libraries[pid]:
                    gs.hands[pid].append(gs.libraries[pid].pop(0))

        # set a coin flip for the first player
        first_player = random.choice(gs.player_ids)
        gs.active_player = first_player

        # setup complete. we transition to MULLIGAN phase.
        gs.phase = "MULLIGAN"
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)
            self._mulligan_expected_seq[pid] = self._connection_for(pid).seq_num

    #decide if mulligan or not
    async def _run_mulligan(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
		# set game phase to mulligan and set all player redraw counts to 0
        gs.phase = "MULLIGAN"
        gs.mulligan_counts = {pid: 0 for pid in gs.player_ids}

        # collect bg wait tasks that trigger when player decides to keep
        kept_tasks = [
            self._mulligan_kept[pid].wait()
            for pid in gs.player_ids
            if pid in self._mulligan_kept
        ]

        if kept_tasks:
			# start a background task watching for disconnects or forfeits
            keep_futures = {asyncio.ensure_future(t) for t in kept_tasks}
            game_over_task = asyncio.create_task(self._game_over.wait())

            try:
				# loop until every player keeps
				# wake up when any task completes.
                while keep_futures:
                    done, pending = await asyncio.wait(
                        keep_futures | {game_over_task},
                        return_when=asyncio.FIRST_COMPLETED,
                    )

					# if the game ends early, cancel remaining 
                    # keep checks and exit immediately.
                    if game_over_task in done:
                        for fut in keep_futures:
                            fut.cancel()  # discard the pending keep-waits.
                        return  # game over interrupted the mulligan.
                    keep_futures = pending - {game_over_task}
                return  # all players kept.
            finally:
				# cleanup
                game_over_task.cancel()

    #run turns until game over
    async def _run_in_game(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
		# keep playing turns in a loop until a game-over flag is set
        while not self._game_over.is_set():
			# identify who is active and who is opponent
            ap_id = gs.active_player or gs.player_ids[0]
            nap_id = self._opponent(ap_id) or gs.player_ids[1]

			# grab connection references for active and non-active players
            ap_conn = conns[self._player_index[ap_id]]
            nap_conn = conns[self._player_index[nap_id]]

            # run one turn via the turn engine
            await self.turn_engine.run_turn(gs, ap_id, nap_id)

            # swap active player
            if not self._game_over.is_set():
                gs.active_player = nap_id

    #reset for next game
    async def _run_game_over(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        
		# reset the phase back to LOBBY and reset the turn counter to zero
        gs.phase = "LOBBY"
        gs.turn = 0
        
		# clear all active match zones including hands, battlefield, deck, and stack
        gs.stack.clear()
        gs.hands.clear()
        gs.libraries.clear()
        gs.graveyards.clear()
        gs.battlefield.clear()
        
		# reset health, mulligans, land actions, and floating mana pools
        gs.life_totals.clear()
        gs.land_played_this_turn = False
        gs.mulligan_counts.clear()
        gs.stack_counter = 0
        gs.mana_pools = {}

		# clear match flags and wipe cached data in stack and combat managers.
        gs._draw_failed_for = None
        gs._cleanup_discard_for = None
        self.stack_mgr.clear_cache()
        self.combat_mgr.reset()

        # Turn engine callbacks
    
    async def _on_phase(
        self, gs: GameState, ap_id: str, nap_id: str, phase: str
    ) -> None:
        
		# set current game phase and give priority to non-active player if declaring blockers.
        gs.phase = phase
        if phase == "DECLARE_BLOCKERS":
            gs.priority_holder = nap_id
        else:
            gs.priority_holder = ap_id

        # broadcast a GAME_STATE_UPDATE first to both players
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

        # Combat sub-steps
        if phase == "DECLARE_ATTACKERS":
            # wait for ap to submit their attackers
            try:
                both, action = await self.priority_mgr.run_priority_window(
                    self._connection_for(ap_id),
                    self._connection_for(nap_id),
                    ap_id, nap_id,
                    read_pdu=self.wait_for_pdu,
                )
            except GameOverInterrupt:
                return
            
            # validate attackers after they are declared
            if action and action.get("type") == "DECLARE_ATTACKERS":
                attackers = action.get("attackers", [])
                self.combat_mgr.set_attackers(gs, ap_id, attackers)

                # every illegal declaration MUST be answered with
                # ERROR ILLEGAL_ACTION instead of a silent drop
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

                # attack triggers (Goblin Guide) go on the stack
                await self._maybe_push_attack_triggers(gs, ap_id, nap_id)
                
            await self._broadcast_game_state(gs) 

            if not self.combat_mgr.attackers:
                # if no attackers exist, skip to end of combat
                gs._skip_to_phase = "END_OF_COMBAT"
                return

            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        if phase == "DECLARE_BLOCKERS":
			# prompt non-active player to choose blockers
            try:
                both, action = await self.priority_mgr.run_priority_window(
                    self._connection_for(nap_id),
                    self._connection_for(ap_id),
                    nap_id, ap_id,
                    read_pdu=self.wait_for_pdu,
                )
            except GameOverInterrupt:
                return
            
			# save declared blockers and broadcast state to clients
            if action and action.get("type") == "DECLARE_BLOCKERS":
                blockers = action.get("blockers", [])
                self.combat_mgr.set_blockers(gs, nap_id, blockers)
                
            await self._broadcast_game_state(gs)

			# allow players to respond with instant speed spells after blocking  
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        if phase == "ASSIGN_DAMAGE_ORDER":
            # find all attacking creatures blocked by more than one defender
            multi_blocked = [
                a_id for a_id in self.combat_mgr.attackers
                if len([b for b, a in self.combat_mgr.blockers.items()
                       if a == a_id]) > 1
            ]

			# for each multi blocked attacker, ask active player to set damage order
            for a_id in multi_blocked:
                expected_blockers = {
                    b for b, a in self.combat_mgr.blockers.items()
                    if a == a_id
                }
                conn = self._connection_for(ap_id)

				# try getting dmg ordering response
				# if timeout/disconnect, forfeit player
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

				# if valid, apply damage order
                if response and response.get("type") == "ASSIGN_DAMAGE_ORDER":
                    r_aid = response.get("attacker_id", "")
                    order = response.get("blocker_order", [])
                    if r_aid == a_id and set(order) == expected_blockers:
                        self.combat_mgr.set_damage_order(a_id, order)
                        continue

                # auto-assign if player didn't provide valid order.
                self.combat_mgr.set_damage_order(a_id, list(expected_blockers))
            return


        if phase == "FIRST_STRIKE_DAMAGE":
            # optional. it only occurs if at least
            # one attacking or blocking creature has first/double strike.
            if not self.combat_mgr.has_first_strike_participants(gs):
                gs._skip_to_phase = "COMBAT_DAMAGE"
                return

			# compute first strike dmg, remove dead units, broadcast result
            result = self.combat_mgr.compute_first_strike_damage(gs)
            check_state_based_actions(gs, self.card_loader)
            await self._broadcast_combat_result(gs, result)
            await self._broadcast_game_state(gs)

            # give players priority window after first strike damage.
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        if phase == "COMBAT_DAMAGE":
            # 1. do the damage math
            result = self.combat_mgr.compute_combat_damage(gs)
            
            # 2. run the sweep (by moving dead creatures to graveyard)
            check_state_based_actions(gs, self.card_loader)
            
            # 3. broadcast the combat results (like damage numbers)
            await self._broadcast_combat_result(gs, result)
            
            # 4. broadcast the FULL updated game state to update the clients UI
            await self._broadcast_game_state(gs)
            
			# reset combat state tracker, give prio to players
            self.combat_mgr.reset()
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        # CLEANUP discard handling
        # No priority window at cleanup: the server sends GAME_STATE_UPDATE
        # and awaits DISCARD directly (see _run_cleanup_discard).
        if phase == "CLEANUP" and gs._cleanup_discard_for is not None:
            await self._run_cleanup_discard(gs, gs._cleanup_discard_for)
            return

        # Phases with priority 
        if phase == "CLEANUP":
            # no priority is given at cleanup Hand-size discard is handled above.
            return

        await self._run_priority_loop(gs, ap_id, nap_id)

    async def _run_cleanup_discard(self, gs: GameState, pid: str) -> None:
		# acquire connection info
		# calculate maximum wait time in seconds
        conn = self._connection_for(pid)
        timeout_s = self.priority_mgr.config.time_limit_ms / 1000.0

		# request discards until player hand size is 7 or less
        while len(gs.hands.get(pid, [])) > 7:

            # send the updated state first
			# let the player pick a card
            vs = build_visible_state(gs, pid)
            gsu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, gsu)

			# wait for the player to respond
			# if not, trigger game over
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

			# check if player sent discard action
			# reject if they send something else
            if not response or response.get("type") != "DISCARD":
                await self.send_error(
                    conn, "ILLEGAL_ACTION",
                    "Expected a DISCARD PDU during cleanup.", response or {},
                )
                continue

			# validate the selected cards
			# ask again if selection made is illegal
            card_ids = response.get("card_ids", [])
            ok, code, msg = validate_discard(gs, pid, card_ids)
            if not ok:
                await self.send_error(conn, code or "ILLEGAL_ACTION", msg, response)
                continue

			# move discarded cards from player's hand into graveyard
            hand = gs.hands.get(pid, [])
            for cid in card_ids:
                if cid in hand:
                    hand.remove(cid)
                    gs.graveyards.setdefault(pid, []).append(cid)

        gs._cleanup_discard_for = None

        # broadcast the final state to both players
        for p in gs.player_ids:
            vs = build_visible_state(gs, p)
            gsu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(p, gsu)

    # phase transition pdu broadcast
    async def _on_advance(
        self, gs: GameState, from_phase: str, to_phase: str
    ) -> None:

		# find active players and send phase change msg to everyone
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

        actor: str | None = None

		# keep handling player prioity actions until match ends
        while not self._game_over.is_set():

			# check rule state actions, update players and stop if someone lost
            sba_changes = check_state_based_actions(gs, self.card_loader)
            if sba_changes:
                await self._broadcast_game_state(gs)
                if self._check_game_over(gs):
                    return

            # a player who casts a spell / activates an ability
            # retains priority, the next window opens with THEM, not
            # automatically with the Active Player.
            first_id, second_id = ap_id, nap_id
            if actor is not None:
                first_id, second_id = actor, (nap_id if actor == ap_id else ap_id)

			# update ppl with priority, send state updates if its changed
            needs_broadcast = (gs.priority_holder != first_id)
            gs.priority_holder = first_id

            if needs_broadcast:
                await self._broadcast_game_state(gs)

			# helper function to switch priority to other player
            async def flip_to_second():
                gs.priority_holder = second_id
                await self._broadcast_game_state(gs)

			# prompt both player for actions, handle d/c or t/o
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

			# handle outcome when both players pass priority without acting
            if both_passed:
				# advance phase if stack is empty, stop when match over
                if self.stack_mgr.is_empty(gs):
                    if self._check_game_over(gs):
                        return
                    break  # advance to next phase
				# resolve the top spell or ability if stack is NOT empty
                else:
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
                    # broadcast updated game state after resolution
                    await self._broadcast_game_state(gs)
                    if self._check_game_over(gs):
                        return
			# process spell/ability/land action if player acted
            elif action is not None:
                await self._process_action(gs, ap_id, nap_id, action)
                if self._check_game_over(gs):
                    return

    # Broadcast helpers

    async def _broadcast_game_state(self, gs: GameState) -> None:
		# loop through every player build their private view and send the update
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

            # during mulligan sync sequence numbers so client choices are not rejected
            if gs.phase == "MULLIGAN" and not self._game_over.is_set():
                self._mulligan_expected_seq[pid] = \
                    self._connection_for(pid).seq_num

    async def _broadcast_combat_result(
        self, gs: GameState, result: dict[str, Any]
    ) -> None:
		# apply updated player life totals calculated from combat
        new_life = result.get("life_totals", {})
        gs.life_totals.update(new_life)

		# build and send the combat damage event summary to all players
        from shared.pdus import create_combat_damage_result
        pdu = create_combat_damage_result(
            seq_num=0,
            damage_events=result.get("damage_events", []),
            life_totals=gs.life_totals,
            creatures_died=result.get("creatures_died", []),
        )
        await self.broadcast(pdu)

		# send full updated state and check if someone lost the game
        await self._broadcast_game_state(gs)
        self._check_game_over(gs)

	# Action processing
    
    #process action during prio
    async def _process_action(
        self, gs: GameState, ap_id: str, nap_id: str, action: dict[str, Any]
    ) -> None:
		# identify action type and who sent it
        atype = action.get("type", "")
        print(f"\nBRAIN RECEIVED IT: {action}")
        pid = action.get("_player_id", ap_id)

		# handle casting a spell card from hand
        if atype == "CAST_SPELL":
            card_id = action.get("card_id", "")
            targets = action.get("targets", [])
            mana_payment = action.get("mana_payment", {})

			# verify if the spell cast is allowed by game rules
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

			# subtract the mana cost from the players mana pool
            try:
                pool = gs.mana_pools.setdefault(pid, ManaPool.empty())
                gs.mana_pools[pid] = deduct_mana(mana_payment, pool)
            except ValueError:
                await self.send_error(
                    self._connection_for(pid),
                    "INSUFFICIENT_MANA", "Failed to deduct mana.", action,
                )
                return

			# remove the cast card from the players hand
            hand = gs.hands.get(pid, [])
            if card_id in hand:
                hand.remove(card_id)

			# put the spell onto the game stack and let everyone know
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

            # prowess triggers (Monastery Swiftspear) fire when
            # a noncreature spell is cast.
            await self._maybe_push_cast_triggers(gs, pid, card_def)

		# handle playing a land card onto the board
        elif atype == "PLAY_LAND":
            print("\nENTERED PLAY_LAND BLOCK")
            try:
                card_id = action.get("card", action.get("card_id", ""))

                pid = action.get("player_id") or gs.priority_holder
                
				# confirm the land play is legal for this turn
                ok, code, msg = validate_play_land(gs, pid, card_id, self.card_loader)
                
                if not ok:
                    await self.send_error(
                        self._connection_for(pid),
                        code or "ILLEGAL_ACTION", msg, action,
                    )
                    return

                # remove played land from the hand
                hand = gs.hands.get(pid, [])
                if card_id in hand:
                    hand.remove(card_id)
                
               	# clean up instance suffix to fetch real card stats
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

				# create the new land card permanent for the battlefield
                perm = Permanent(
                    id=card_id,
                    card_def_id=def_id,
                    controller=pid,
                    tapped=False,
                    power=power or 0,
                    toughness=toughness or 0,
                    summoning_sick=False,
                )
				
                # place land on battlefield and mark that land was played this turn
                gs.battlefield.setdefault(pid, []).append(perm)
                gs.land_played_this_turn = True
                
                print(f"\nSUCCESS: Added {card_id} to board!")
                
                # broadcast updated board state to all players
                await self._broadcast_game_state(gs)
                
            except Exception as e:
                import traceback
                print(f"\nFATAL ENGINE CRASH IN PLAY_LAND:")
                traceback.print_exc()

		# handle using a land or creature ability
        elif atype == "ACTIVATE_ABILITY":
            source_id = action.get("source_id", "")
            ability_index = action.get("ability_index", 0)
            
            # locate the target card on the active battlefield
            perm = None
            for p in gs.battlefield.get(pid, []):
                if p.id == source_id:
                    perm = p
                    break
            
			# send error if card is not found on field
            if not perm:
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION", f"Permanent '{source_id}' not found.", action,
                )
                return

			# send error if card is already tapped
            if perm.tapped:
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION", f"'{source_id}' is already tapped.", action,
                )
                return

            # look up card data to find the activated ability details
            base_id = source_id
            if "_" in source_id:
                parts = source_id.rsplit("_", 1)
                if parts[1].isdigit():
                    base_id = parts[0]
            cd = self.card_loader.get_card(base_id)
            
			# verify that the ability choice index exists on the card
            if not cd or ability_index >= len(cd.abilities):
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION", f"Invalid ability index {ability_index}.", action,
                )
                return
                
            ability = cd.abilities[ability_index]
            
            # tap the card if required as cost
            if ability.get("requires_tap"):
                perm.tapped = True

            # generate mana into the players pool if this is a mana ability
            produces = ability.get("produces", {})
            if not produces:
                # undo tap and reject if non mana abilities are unsupported
                perm.tapped = False
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION",
                    f"Ability '{ability.get('name', '')}' on '{source_id}' is "
                    "not implemented in MTGNP 1.0.",
                    action,
                )
                return

			# add produced mana amounts to the player pool
            pool = gs.mana_pools.setdefault(pid, ManaPool.empty())
            for color, amount in produces.items():
                current = getattr(pool, color, 0)
                setattr(pool, color, current + amount)

            print(f"MANA ADDED! Pool is now: W:{pool.W} U:{pool.U} B:{pool.B} R:{pool.R} G:{pool.G} C:{pool.C}")
            
            # update all clients so they see tapped cards and new mana
            await self._broadcast_game_state(gs)

	# Triggered abilities
    
    async def _maybe_push_attack_triggers(
        self, gs: GameState, ap_id: str, nap_id: str
    ) -> None:
        from server.card_effects import check_triggers

		# check each attacking creature to see if it exists on the battlefield
        for cid in list(self.combat_mgr.attackers):
            perm = None
            for perms in gs.battlefield.values():
                for p in perms:
                    if p.id == cid:
                        perm = p
                        break
            if perm is None:
                continue
			# find attack triggers matching this specific attacking creature
            for trg in check_triggers(gs, "ATTACKS", cid, ap_id):
                if trg["source"] != cid:
                    continue  # only the attacking permanent triggers
				# push attack trigger to the stack and notify both players
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
        from server.card_effects import check_triggers

		# check card type and ignore if the spell played is a creature
        ctype = getattr(card_def, "card_type", "") if card_def else ""
        if "creature" in ctype.lower():
            return
		# trigger prowess abilities for all qualifying noncreature spell casts
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

	# PDU handler methods (called by dispatcher)
    # LOBBY 

    #process players in lobby
    async def handle_player_ready(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        player_id = pdu.get("player_id", "")
        deck_list = pdu.get("deck_list", [])

        # check if this packet is a rejoining connection during live gameplay
        if self.gs.phase != "LOBBY" and not self._game_over.is_set():
            # handle reconnection logic or player retrying deck submission
            if conn.player_id is not None:
                # check if player is submitting a fixed deck in setup phase
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
                
            # block new connections from joining mid game
            await self.send_error(conn, "ILLEGAL_ACTION",
                                  "Not in LOBBY state.", pdu)
            return

       	# check that player id is provided
        if not player_id:
            await self.send_error(conn, "ILLEGAL_ACTION", 
                                  "player_id cannot be empty.", pdu)
            return

        # check that deck size is within valid limits
        if not (1 <= len(deck_list) <= 50):
            await self.send_error(conn, "ILLEGAL_DECK", 
                                  "Deck must contain between 1 and 50 cards.", pdu)
            return

        # verify that the player id is not already in use
        for c in self.connections:
            if c is not conn and c.player_id == player_id:
                await self.send_error(conn, "DUPLICATE_ID",
                                      f"Player ID '{player_id}' already claimed.", pdu)
                return

        # check that all cards in the deck are valid
        ok, err_code, msg = validate_deck(player_id, deck_list, self.card_loader)
        if not ok:
            await self.send_error(conn, err_code or "ILLEGAL_DECK", msg, pdu)
            return

        # assign player id update lobby count and save the deck list
        gs = self.gs
        if conn.player_id is None:
            conn.player_id = player_id
            gs.players_ready += 1
        self._deck_lists[player_id] = list(deck_list)
        gs.waiting_for = [
            c.player_id for c in self.connections if c.player_id is None
        ]

        # send state update back to player to acknowledge ready state
        gsu = create_game_state_update(seq_num=0, state={
            "phase": "LOBBY",
            "players_ready": gs.players_ready,
            "waiting_for": gs.waiting_for,
        })
        await self.send_to(player_id, gsu)

    #  MULLIGAN 

    #process mulligan choice from player
    async def handle_mulligan_choice(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
		# get the player id attached to this connection
        player_id = conn.player_id
        if not player_id:
            return

        # check that the game is currently in the mulligan phase
        if self.gs.phase != "MULLIGAN":
            await self.send_error(
                conn, "ILLEGAL_ACTION",
                "Not in MULLIGAN phase.", pdu,
            )
            return

        # ignore incoming choice if the match has already ended
        if self._game_over.is_set():
            return

        # Validate seq_num echoes the last GAME_STATE_UPDATE
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

		# extract player keep decision and bottomed card choices
        keep = pdu.get("keep", False)
        cards_to_bottom = pdu.get("cards_to_bottom", [])

		# validate whether the mulligan decision follows game rules
        ok, code, msg = validate_mulligan(
            self.gs, player_id, keep, cards_to_bottom
        )
        if not ok:
            await self.send_error(conn, code or "ILLEGAL_ACTION", msg, pdu)
            return

		# apply the mulligan outcome to player hand and deck
        process_mulligan_choice(self.gs, player_id, keep, cards_to_bottom)

		# send updated private state back to the deciding player
        vs = build_visible_state(self.gs, player_id)
        gsu = create_game_state_update(seq_num=0, state=vs)
        await self.send_to(player_id, gsu)

        # ensure match did not terminate while sending state update
        if self._game_over.is_set():
            return
        
		# update sequence number tracking for next incoming message
        self._mulligan_expected_seq[player_id] = conn.seq_num

		# mark player choice complete if they chose to keep their hand
        if keep:
            kept = self._mulligan_kept.get(player_id)
            if kept is not None and not kept.is_set():
                kept.set()

    #  IN_GAME - priority-bearing actions 

    async def handle_priority_pass(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_cast_spell(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_activate_ability(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_play_land(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        # log land plays received outside of active priority windows
        print(f"[PLAY_LAND] {conn.player_id} tried to play "
              f"card={pdu.get('card_id')!r} outside a priority window")
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
        pass

    async def handle_trigger_order_response(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    async def handle_trigger_choice_response(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        pass

    #  Any phase 

    async def handle_concede(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:

		# extract player identifier from connection or message payload
        player_id = conn.player_id or pdu.get("player_id", "?")
        winner = self._opponent(player_id)
        if winner:
            await self._end_game(self.gs, "CONCEDE", winner, player_id)

    async def handle_ping(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:

		# extract message sequence number and timestamp to mirror back
        seq = pdu.get("seq_num", 0)
        ts = pdu.get("timestamp", 0)
        pong = create_pong(seq_num=seq, timestamp=ts)
        await conn.send_pdu(pong)

        # Send / broadcast helpers
    
    async def send_to(self, player_id: str, pdu: dict[str, Any]) -> None:

		# locate target player's active connection and deliver message
        for conn in self.connections:
            if conn.player_id == player_id:
                await conn.send_pdu(pdu)
                return

    async def broadcast(self, pdu: dict[str, Any]) -> None:
		# attempt best-effort message delivery across all active connections
        for conn in self.connections:
            if conn.player_id and not getattr(conn, "_closed", False):
                try:
                    await conn.send_pdu(pdu)
                except (ConnectionError, OSError):
                    continue

    async def send_error(
        self,
        conn: ServerConnection,
        code: str,
        message: str,
        rejected_action: dict[str, Any],
    ) -> None:
		# construct error payload detailing the rejected client action
        pdu = create_error(
            seq_num=0, code=code, message=message,
            rejected_action=rejected_action,
        )

		# echo rejected sequence number if present, otherwise send standard pdu
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

		# prevent duplicate game-ending execution across concurrent events
        if self._game_over.is_set():
            return

        # claim the ending before any await.
        self._game_over.set()

		# notify all connected players that the match has ended
        pdu = create_game_over(
            seq_num=0,
            winner_id=winner_id,
            loser_id=loser_id,
            reason=reason,
        )
        try:
            await self.broadcast(pdu)
        finally:
            # clear game state and session metadata for match cleanupni
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

        # Internal helpers
    
    def _connection_for(self, player_id: str) -> ServerConnection:
		
		# return connection for player_id
        for conn in self.connections:
            if conn.player_id == player_id:
                return conn
        return self.connections[0]

    def _opponent(self, player_id: str) -> str | None:

		# iterate through game state players to find the opposing player
        for pid in self.gs.player_ids:
            if pid != player_id:
                return pid
        return None

    def _check_game_over(self, gs: GameState) -> bool:

		# evaluate player state for life depletion or empty library draws
        for pid in gs.player_ids:
            if gs.life_totals.get(pid, 20) <= 0:
                winner = self._opponent(pid) or ""
                # schedule async game ending sequence for zero-life defeat
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
				# schedule async game ending sequence for empty deck draw
                self._pending_end_task = asyncio.ensure_future(
                    self._end_game(gs, "DECK_EMPTY", winner, pid)
                )
                self._pending_end_task.add_done_callback(
                    _retrieve_task_exception
                )
                return True
        return False
