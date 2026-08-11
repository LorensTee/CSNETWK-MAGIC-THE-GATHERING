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
from server.mana import deduct_mana, empty_pool_dict
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

class GameLifecycle:
    # The game lifecycle state machine.

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

        # dictionary mapping player id to async future object
        self._pending_pdu: dict[str, asyncio.Future[dict]] = {}

        # lock object for critical section thread safety
        self._lock = asyncio.Lock()

        # dictionary mapping player id to list index
        self._player_index: dict[str, int] = {}

        # event flag signalling game over state
        self._game_over = asyncio.Event()

        # dictionary storing deck list arrays by player id
        self._deck_lists: dict[str, list[str]] = {}

        # dictionary mapping player id to mulligan keep event
        self._mulligan_kept: dict[str, asyncio.Event] = {}

        # dictionary mapping player id to expected sequence integer
        self._mulligan_expected_seq: dict[str, int] = {}

    # async function that waits for player pdu packet
    async def wait_for_pdu(
        self, player_id: str, timeout: float
    ) -> dict[str, Any]:
        loop = asyncio.get_running_loop()

        # creates new future object on event loop
        future = loop.create_future()
        self._pending_pdu[player_id] = future
        try:
            pdu = await asyncio.wait_for(future, timeout=timeout)
            
            # sets player id property on returned dictionary
            pdu["_player_id"] = player_id 
            
            return pdu
        except asyncio.TimeoutError:
            raise
        finally:
            # cleans up pending future key from dictionary
            if self._pending_pdu.get(player_id) is future:
                self._pending_pdu.pop(player_id, None)

    # main method executing game loop state machine
    async def run(self) -> None:
        """Run the full game lifecycle (LOBBY → … → GAME_OVER → loop)."""
        while True:
            # resets game over event flag to false
            self._game_over.clear()
            gs = self.gs
            conns = self.connections

            # gets connections list length and checks minimum threshold
            if len(conns) < 2:
                return

            # creates background tasks for socket read loop
            async with asyncio.TaskGroup() as tg:
                for conn in conns:
                    tg.create_task(conn.read_loop())

                # runs lobby phase runner function
                await self._run_lobby(gs, conns)
                if self._game_over.is_set():
                    await self._run_game_over(gs, conns)
                    continue

                # runs setup phase runner function
                await self._run_setup(gs, conns)
                if self._game_over.is_set():
                    await self._run_game_over(gs, conns)
                    continue

                # runs mulligan phase runner function
                await self._run_mulligan(gs, conns)
                if self._game_over.is_set():
                    await self._run_game_over(gs, conns)
                    continue

                # executes main turn loop method
                await self._run_in_game(gs, conns)

                # executes game over cleanup function
                await self._run_game_over(gs, conns)

    # handles lobby phase waiting for players
    async def _run_lobby(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        # sets phase variable to lobby string
        gs.phase = "LOBBY"

        # resets player ids array and ready counter to zero
        gs.player_ids.clear()
        gs.players_ready = 0
        gs.waiting_for = []

        for conn in conns:
            conn.player_id = None

        # while loop waiting for ready count to equal two
        while gs.players_ready < 2:
            await asyncio.sleep(0.1)  # Yield — dispatcher updates state.

        # loops connections to assign player id and index
        for conn in conns:
            if conn.player_id:
                gs.player_ids.append(conn.player_id)
                self._player_index[conn.player_id] = len(gs.player_ids) - 1

    # handles game setup phase execution
    async def _run_setup(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        """GAME_SETUP: shuffle, draw 7, coin flip for first player."""
        gs.phase = "GAME_SETUP"

        # loops player ids to validate deck list legality
        for pid in gs.player_ids:
            deck = self._deck_lists.get(pid, [])
            ok, msg = self.card_loader.is_legal_deck(deck)

            # triggers game over if deck validation fails
            if not ok:
                await self._end_game(gs, "ILLEGAL_DECK", pid,
                                     self._opponent(pid))
                return

        # sends game setup state pdu to all players
        for pid in gs.player_ids:
            gsu = create_game_state_update(seq_num=0, state={
                "phase": "GAME_SETUP",
                "players_ready": 2,
                "waiting_for": [],
            })
            await self.send_to(pid, gsu)

        # sets initial health total values to integer twenty
        for pid in gs.player_ids:
            self._mulligan_kept[pid] = asyncio.Event()

        for pid in gs.player_ids:
            gs.life_totals[pid] = 20

        # shuffles deck array and pops seven cards to hand
        for pid in gs.player_ids:
            random.shuffle(gs.libraries[pid])
            gs.hands[pid] = []
            for _ in range(7):
                if gs.libraries[pid]:
                    gs.hands[pid].append(gs.libraries[pid].pop(0))

        # uses random choice to select starting player id
        first_player = random.choice(gs.player_ids)
        gs.active_player = first_player

        gs.phase = "MULLIGAN"
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

    # handles mulligan phase execution
    async def _run_mulligan(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:

        gs.phase = "MULLIGAN"
        
        # resets mulligan count dictionary to zero
        gs.mulligan_counts = {pid: 0 for pid in gs.player_ids}

        kept_tasks = [
            self._mulligan_kept[pid].wait()
            for pid in gs.player_ids
            if pid in self._mulligan_kept
        ]

        # waits for all player keep event coroutines
        if kept_tasks:
            await asyncio.gather(*kept_tasks)

    # main game loop running turns until game over
    async def _run_in_game(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:

        # while loop iterating until game over event is set
        while not self._game_over.is_set():

            # assigns active player and opponent player variables
            ap_id = gs.active_player or gs.player_ids[0]
            nap_id = self._opponent(ap_id) or gs.player_ids[1]

            ap_conn = conns[self._player_index[ap_id]]
            nap_conn = conns[self._player_index[nap_id]]

            # runs single turn step via turn engine
            await self.turn_engine.run_turn(gs, ap_id, nap_id)

            # toggles active player variable to opponent id
            if not self._game_over.is_set():
                gs.active_player = nap_id

    # cleans up state variables after game ends
    async def _run_game_over(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        # resets phase variable turn counter and list collections
        gs.phase = "LOBBY"
        gs.turn = 0
        gs.stack.clear()
        gs.hands.clear()
        gs.libraries.clear()
        gs.graveyards.clear()
        gs.battlefield.clear()
        gs.life_totals.clear()
        gs.player_ids.clear()
        gs.land_played_this_turn = False
        gs.mulligan_counts.clear()
        gs.waiting_for = []
        gs.players_ready = 0
        gs.stack_counter = 0
        gs.mana_pool = gs.mana_pool.empty()
        gs._draw_failed_for = None
        gs._cleanup_discard_for = None
        self._deck_lists.clear()
        self._mulligan_kept.clear()
        self._player_index.clear()
        self.stack_mgr.clear_cache()
        self.combat_mgr.reset()

    # callback method for phase transition execution
    async def _on_phase(
        self, gs: GameState, ap_id: str, nap_id: str, phase: str
    ) -> None:

        # updates phase string and priority holder variable
        gs.phase = phase

        if phase == "DECLARE_BLOCKERS":
            gs.priority_holder = nap_id
        else:
            gs.priority_holder = ap_id

        # broadcasts state update pdu to both players
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

        # opens priority window for declaring attackers
        if phase == "DECLARE_ATTACKERS":
            both, action = await self.priority_mgr.run_priority_window(
                self._connection_for(ap_id),
                self._connection_for(nap_id),
                ap_id, nap_id,
                read_pdu=self.wait_for_pdu,
            )
            
            # checks attacker array and updates combat manager
            if action and action.get("type") == "DECLARE_ATTACKERS":
                attackers = action.get("attackers", [])
                self.combat_mgr.set_attackers(gs, ap_id, attackers)
                
            await self._broadcast_game_state(gs) 
                
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        # opens priority window for declaring blockers
        if phase == "DECLARE_BLOCKERS":
            both, action = await self.priority_mgr.run_priority_window(
                self._connection_for(nap_id),
                self._connection_for(ap_id),
                nap_id, ap_id,
                read_pdu=self.wait_for_pdu,
            )
            
            if action and action.get("type") == "DECLARE_BLOCKERS":
                blockers = action.get("blockers", [])
                self.combat_mgr.set_blockers(gs, nap_id, blockers)
                
            await self._broadcast_game_state(gs)
                
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        # phase to assign damage order
        if phase == "ASSIGN_DAMAGE_ORDER":
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
                self.combat_mgr.set_damage_order(a_id, list(expected_blockers))
            return

        # calculates first strike damage and evaluates state based actions
        if phase == "FIRST_STRIKE_DAMAGE":
            result = self.combat_mgr.compute_first_strike_damage(gs)
            
            check_state_based_actions(gs, self.card_loader)
            
            await self._broadcast_combat_result(gs, result)
            
            await self._broadcast_game_state(gs)

            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        # calculates regular combat damage and updates board state
        if phase == "COMBAT_DAMAGE":
            result = self.combat_mgr.compute_combat_damage(gs)
            check_state_based_actions(gs, self.card_loader)

            await self._broadcast_combat_result(gs, result)
            await self._broadcast_game_state(gs)
            
            self.combat_mgr.reset()
            await self._run_priority_loop(gs, ap_id, nap_id)
            return

        await self._run_priority_loop(gs, ap_id, nap_id)

        # handles cleanup phase discard checking
        if phase == "CLEANUP" and gs._cleanup_discard_for is not None:
            pid = gs._cleanup_discard_for
            conn = self._connection_for(pid)
            try:
                response = await self.priority_mgr.grant_priority(
                    conn, pid, read_pdu=self.wait_for_pdu,
                )
            except (PriorityTimeout, ConnectionLost):
                await self._end_game(gs, "DISCONNECT",
                                     self._opponent(pid) or "", pid)
                return
            if response and response.get("type") == "DISCARD":
                card_ids = response.get("card_ids", [])
                ok, code, msg = validate_discard(gs, pid, card_ids)
                if ok:
                    hand = gs.hands.get(pid, [])
                    for cid in card_ids:
                        if cid in hand:
                            hand.remove(cid)
                            gs.graveyards.setdefault(pid, []).append(cid)
                    gs._cleanup_discard_for = None
                else:
                    await self.send_error(conn, code or "ILLEGAL_ACTION",
                                          msg, response)
            for p in gs.player_ids:
                vs = build_visible_state(gs, p)
                gsu = create_game_state_update(seq_num=0, state=vs)
                await self.send_to(p, gsu)

    # broadcast phase transition pdu helper
    async def _on_advance(
        self, gs: GameState, from_phase: str, to_phase: str
    ) -> None:
        ap_id = gs.active_player or (gs.player_ids[0] if gs.player_ids else "")
        pdu = create_phase_transition(
            seq_num=0,
            from_phase=from_phase,
            to_phase=to_phase,
            active_player=ap_id,
            turn=gs.turn,
        )
        await self.broadcast(pdu)

    # while loop running priority windows until phase advance
    async def _run_priority_loop(
        self, gs: GameState, ap_id: str, nap_id: str
    ) -> None:
        async def flip_to_nap():
            gs.priority_holder = nap_id
            await self._broadcast_game_state(gs)

        while not self._game_over.is_set():
            # checks state based actions like creature deaths
            sba_changes = check_state_based_actions(gs, self.card_loader)
            if sba_changes:
                await self._broadcast_game_state(gs)
                if self._check_game_over(gs):
                    return
                
            needs_broadcast = (gs.priority_holder != ap_id)
            gs.priority_holder = ap_id

            if needs_broadcast:
                await self._broadcast_game_state(gs)

            try:
                # runs priority window for active and non active players
                both_passed, action = await self.priority_mgr.run_priority_window(
                    self._connection_for(ap_id),
                    self._connection_for(nap_id),
                    ap_id, nap_id,
                    read_pdu=self.wait_for_pdu,
                    on_ap_pass_cb=flip_to_nap,
                )
            except PriorityTimeout as exc:
                winner = nap_id if exc.player_id == ap_id else ap_id
                await self._end_game(gs, "DISCONNECT", winner, exc.player_id)
                return
            except ConnectionLost as exc:
                winner = nap_id if exc.player_id == ap_id else ap_id
                await self._end_game(gs, "DISCONNECT", winner, exc.player_id)
                return

            # resolves top item on stack manager if players pass priority
            if both_passed:
                if self.stack_mgr.is_empty(gs):
                    if self._check_game_over(gs):
                        return
                    break
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
                    await self._broadcast_game_state(gs)
                    if self._check_game_over(gs):
                        return
            # processes action packet if player took action
            elif action is not None:
                await self._process_action(gs, ap_id, nap_id, action)
                if self._check_game_over(gs):
                    return

    # broadcasts the game state to players
    async def _broadcast_game_state(self, gs: GameState) -> None:
        for pid in gs.player_ids:
            vs = build_visible_state(gs, pid)
            pdu = create_game_state_update(seq_num=0, state=vs)
            await self.send_to(pid, pdu)

    # broadcasts the combat results to players
    async def _broadcast_combat_result(
        self, gs: GameState, result: dict[str, Any]
    ) -> None:
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
        await self._broadcast_game_state(gs)
        self._check_game_over(gs)

    # routes priority actions based on action type string
    async def _process_action(
        self, gs: GameState, ap_id: str, nap_id: str, action: dict[str, Any]
    ) -> None:
        atype = action.get("type", "")
        print(f"\nBRAIN RECEIVED IT: {action}")
        pid = action.get("_player_id", ap_id)

        # handles cast spell action execution
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
                # deducts mana payment from player mana pool object
                gs.mana_pool = deduct_mana(mana_payment, gs.mana_pool)
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
            # pushes spell item to stack manager and broadcasts
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

        # handles play land action execution
        elif atype == "PLAY_LAND":
            print("\nENTERED PLAY_LAND BLOCK")
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

                # removes land card id from hand array
                hand = gs.hands.get(pid, [])
                if card_id in hand:
                    hand.remove(card_id)
                
                # parses base card id by stripping instance suffix string
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

                # instantiates permanent object and appends to battlefield list
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
                
                print(f"\nSUCCESS: Added {card_id} to board!")
                await self._broadcast_game_state(gs)
                
            except Exception as e:
                import traceback
                print(f"\nFATAL ENGINE CRASH IN PLAY_LAND:")
                traceback.print_exc()

        # handles activate ability action execution
        elif atype == "ACTIVATE_ABILITY":
            source_id = action.get("source_id", "")
            ability_index = action.get("ability_index", 0)
            
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

            base_id = source_id.rsplit("_", 1)[0] if "_" in source_id else source_id
            cd = self.card_loader.get_card(base_id)
            
            if not cd or ability_index >= len(cd.abilities):
                await self.send_error(
                    self._connection_for(pid),
                    "ILLEGAL_ACTION", f"Invalid ability index {ability_index}.", action,
                )
                return
                
            ability = cd.abilities[ability_index]

            # sets tapped status true if ability requires tap
            if ability.get("requires_tap"):
                perm.tapped = True
            
            # updates mana pool attributes with produced color values
            produces = ability.get("produces", {})
            for color, amount in produces.items():
                current = getattr(gs.mana_pool, color, 0)
                setattr(gs.mana_pool, color, current + amount)
                
            print(f"MANA ADDED! Pool is now: W:{gs.mana_pool.W} U:{gs.mana_pool.U} B:{gs.mana_pool.B} R:{gs.mana_pool.R} G:{gs.mana_pool.G} C:{gs.mana_pool.C}")
            
            await self._broadcast_game_state(gs)

    # handles player ready pdu packet in lobby
    async def handle_player_ready(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process PLAYER_READY in LOBBY state."""
        player_id = pdu.get("player_id", "")
        deck_list = pdu.get("deck_list", [])

        # reconnect bypass
        if self.gs.phase != "LOBBY":
            if conn.player_id is not None:
                return
                
            await self.send_error(conn, "ILLEGAL_ACTION",
                                  "Not in LOBBY state.", pdu)
            return

        # checks that player id is non empty string
        if not player_id:
            await self.send_error(conn, "ILLEGAL_ACTION", 
                                  "player_id cannot be empty.", pdu)
            return

        # validates deck list array bounds
        if not (1 <= len(deck_list) <= 50):
            await self.send_error(conn, "ILLEGAL_DECK", 
                                  "Deck must contain between 1 and 50 cards.", pdu)
            return

        # checks for duplicate player id strings in connection list
        for c in self.connections:
            if c is not conn and c.player_id == player_id:
                await self.send_error(conn, "DUPLICATE_ID",
                                      f"Player ID '{player_id}' already claimed.", pdu)
                return

        # runs deck validation helper function
        ok, err_code, msg = validate_deck(player_id, deck_list, self.card_loader)
        if not ok:
            await self.send_error(conn, err_code or "ILLEGAL_DECK", msg, pdu)
            return

        gs = self.gs

        # assigns player id attribute and increments ready counter
        if conn.player_id is None:
            conn.player_id = player_id
            gs.players_ready += 1
        self._deck_lists[player_id] = list(deck_list)
        gs.libraries[player_id] = list(deck_list)
        gs.hands[player_id] = []
        gs.graveyards[player_id] = []
        gs.battlefield[player_id] = []
        gs.waiting_for = [
            c.player_id for c in self.connections if c.player_id is None
        ]

        gsu = create_game_state_update(seq_num=0, state={
            "phase": "LOBBY",
            "players_ready": gs.players_ready,
            "waiting_for": gs.waiting_for,
        })
        await self.send_to(player_id, gsu)

    # handles mulligan choice pdu packet
    async def handle_mulligan_choice(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        """Process MULLIGAN_CHOICE."""
        player_id = conn.player_id
        if not player_id:
            return

        # verifies phase variable equals mulligan string
        if self.gs.phase != "MULLIGAN":
            await self.send_error(
                conn, "ILLEGAL_ACTION",
                "Not in MULLIGAN phase.", pdu,
            )
            return

        # compares sequence number against expected value
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

        # runs mulligan validator module
        ok, code, msg = validate_mulligan(
            self.gs, player_id, keep, cards_to_bottom
        )
        if not ok:
            await self.send_error(conn, code or "ILLEGAL_ACTION", msg, pdu)
            return

        # executes mulligan choice processing logic
        process_mulligan_choice(self.gs, player_id, keep, cards_to_bottom)

        vs = build_visible_state(self.gs, player_id)
        gsu = create_game_state_update(seq_num=0, state=vs)
        await self.send_to(player_id, gsu)
        self._mulligan_expected_seq[player_id] = conn.seq_num

        # sets mulligan kept event flag if player chose keep
        if keep:
            self._mulligan_kept[player_id].set()

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
        print(f"\nGARBAGE CAN ATE IT: {pdu}")
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

    # handles concede packet and sets winner id
    async def handle_concede(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        player_id = conn.player_id or pdu.get("player_id", "?")
        winner = self._opponent(player_id)
        if winner:
            await self._end_game(self.gs, "CONCEDE", winner, player_id)

    # handles ping packet by returning pong pdu
    async def handle_ping(
        self, conn: ServerConnection, pdu: dict[str, Any]
    ) -> None:
        seq = pdu.get("seq_num", 0)
        ts = pdu.get("timestamp", 0)
        pong = create_pong(seq_num=seq, timestamp=ts)
        await conn.send_pdu(pong)

    # transmits pdu to matching player id connection
    async def send_to(self, player_id: str, pdu: dict[str, Any]) -> None:
        for conn in self.connections:
            if conn.player_id == player_id:
                await conn.send_pdu(pdu)
                return

    # broadcasts pdu packet to all connected clients
    async def broadcast(self, pdu: dict[str, Any]) -> None:
        for conn in self.connections:
            if conn.player_id:
                await conn.send_pdu(pdu)

    # constructs error pdu and transmits to client
    async def send_error(
        self,
        conn: ServerConnection,
        code: str,
        message: str,
        rejected_action: dict[str, Any],
    ) -> None:
        pdu = create_error(
            seq_num=0, code=code, message=message,
            rejected_action=rejected_action,
        )
        await conn.send_pdu(pdu)

    # broadcasts game over pdu and sets event flag
    async def _end_game(
        self,
        gs: GameState,
        reason: str,
        winner_id: str,
        loser_id: str,
    ) -> None:
        pdu = create_game_over(
            seq_num=0,
            winner_id=winner_id,
            loser_id=loser_id,
            reason=reason,
        )
        await self.broadcast(pdu)
        self._game_over.set()

    # returns connection object matching player id
    def _connection_for(self, player_id: str) -> ServerConnection:
        for conn in self.connections:
            if conn.player_id == player_id:
                return conn
        return self.connections[0]

    # loops player ids list to find opponent player id
    def _opponent(self, player_id: str) -> str | None:
        for pid in self.gs.player_ids:
            if pid != player_id:
                return pid
        return None

    # evaluates life totals and deck empty conditions for game loss
    def _check_game_over(self, gs: GameState) -> bool:
        for pid in gs.player_ids:
            if gs.life_totals.get(pid, 20) <= 0:
                winner = self._opponent(pid) or ""
                asyncio.ensure_future(
                    self._end_game(gs, "LIFE_ZERO", winner, pid)
                )
                return True
            if gs._draw_failed_for == pid:
                gs._draw_failed_for = None  # Clear after consuming.
                winner = self._opponent(pid) or ""
                asyncio.ensure_future(
                    self._end_game(gs, "DECK_EMPTY", winner, pid)
                )
                return True
        return False
