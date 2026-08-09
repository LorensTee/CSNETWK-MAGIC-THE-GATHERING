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
        try:
            pdu = await asyncio.wait_for(future, timeout=timeout)
            
            pdu["_player_id"] = player_id 
            
            return pdu
        except asyncio.TimeoutError:
            raise
        finally:
            # Clean up the future reference on timeout or cancellation.
            if self._pending_pdu.get(player_id) is future:
                self._pending_pdu.pop(player_id, None)

    # ═══════════════════════════════════════════════════════════════════════════
    # Public entry point
    # ═══════════════════════════════════════════════════════════════════════════

    async def run(self) -> None:
        """Run the full game lifecycle (LOBBY → … → GAME_OVER → loop)."""
        while True:
            self._game_over.clear()
            gs = self.gs
            conns = self.connections
            if len(conns) < 2:
                return  # Not enough connections — should not happen.

            # Start read loops for both connections (sole PDU readers).
            async with asyncio.TaskGroup() as tg:
                for conn in conns:
                    tg.create_task(conn.read_loop())

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

    # ═══════════════════════════════════════════════════════════════════════════
    # Lifecycle state runners
    # ═══════════════════════════════════════════════════════════════════════════

    async def _run_lobby(
        self, gs: GameState, conns: list[ServerConnection]
    ) -> None:
        """LOBBY: wait for two PLAYER_READY PDUs."""
        gs.phase = "LOBBY"
        gs.player_ids.clear()
        gs.players_ready = 0
        gs.waiting_for = []

        for conn in conns:
            conn.player_id = None

        while gs.players_ready < 2:
            await asyncio.sleep(0.1)  # Yield — dispatcher updates state.

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
        for pid in gs.player_ids:
            deck = self._deck_lists.get(pid, [])
            ok, msg = self.card_loader.is_legal_deck(deck)
            if not ok:
                await self._end_game(gs, "ILLEGAL_DECK", pid,
                                     self._opponent(pid))
                return

        # Broadcast GAME_SETUP state (per program-states.md Step 4).
        for pid in gs.player_ids:
            gsu = create_game_state_update(seq_num=0, state={
                "phase": "GAME_SETUP",
                "players_ready": 2,
                "waiting_for": [],
            })
            await self.send_to(pid, gsu)

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
            await asyncio.gather(*kept_tasks)

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
        """GAME_OVER: reset game state for the next game."""
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
        gs.mana_pools = {}
        gs._draw_failed_for = None
        gs._cleanup_discard_for = None
        self._deck_lists.clear()
        self._mulligan_kept.clear()
        self._player_index.clear()
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
            both, action = await self.priority_mgr.run_priority_window(
                self._connection_for(ap_id),
                self._connection_for(nap_id),
                ap_id, nap_id,
                read_pdu=self.wait_for_pdu,
            )
            
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

        # ── Phases with priority ────────────────────────────────────────
        await self._run_priority_loop(gs, ap_id, nap_id)

        # ── CLEANUP discard handling ────────────────────────────────────
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
            # Broadcast updated state after discard.
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

        async def flip_to_nap():
            gs.priority_holder = nap_id
            await self._broadcast_game_state(gs)

        while not self._game_over.is_set():

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
            base_id = source_id.rsplit("_", 1)[0] if "_" in source_id else source_id
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
            pool = gs.mana_pools.setdefault(pid, ManaPool.empty())
            for color, amount in produces.items():
                current = getattr(pool, color, 0)
                setattr(pool, color, current + amount)

            print(f"💧 MANA ADDED! Pool is now: W:{pool.W} U:{pool.U} B:{pool.B} R:{pool.R} G:{pool.G} C:{pool.C}")
            
            # Broadcast the state update so the client sees the tapped land
            await self._broadcast_game_state(gs)

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

        # 1. RECONNECT BYPASS
        if self.gs.phase != "LOBBY":
            # If this socket already has an ID assigned by the Reconnect Watcher, 
            # they are just rejoining. Silently ignore this amnesia packet.
            if conn.player_id is not None:
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
        self._deck_lists[player_id] = list(deck_list)
        gs.libraries[player_id] = list(deck_list)
        gs.hands[player_id] = []
        gs.graveyards[player_id] = []
        gs.battlefield[player_id] = []
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
        # Record seq_num for MULLIGAN_CHOICE echo validation.
        self._mulligan_expected_seq[player_id] = conn.seq_num

        if keep:
            self._mulligan_kept[player_id].set()

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
        """Send a PDU to all connected players."""
        for conn in self.connections:
            if conn.player_id:
                await conn.send_pdu(pdu)

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
        """Broadcast GAME_OVER and signal the game loop to stop."""
        pdu = create_game_over(
            seq_num=0,
            winner_id=winner_id,
            loser_id=loser_id,
            reason=reason,
        )
        await self.broadcast(pdu)
        self._game_over.set()

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
                asyncio.ensure_future(
                    self._end_game(gs, "LIFE_ZERO", winner, pid)
                )
                return True
            # DECK_EMPTY: draw from empty library.
            if gs._draw_failed_for == pid:
                gs._draw_failed_for = None  # Clear after consuming.
                winner = self._opponent(pid) or ""
                asyncio.ensure_future(
                    self._end_game(gs, "DECK_EMPTY", winner, pid)
                )
                return True
        return False
