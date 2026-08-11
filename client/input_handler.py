from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING, Any

from shared.pdus import (
    create_activate_ability,
    create_assign_damage_order,
    create_cast_spell,
    create_concede,
    create_declare_attackers,
    create_declare_blockers,
    create_discard,
    create_mulligan_choice,
    create_play_land,
    create_player_ready,
    create_priority_pass,
)

from server.card_loader import CardLoader

if TYPE_CHECKING:
    from client.client import GameClient


class InputHandler:
    """parse player input and creates PDU"""

    def __init__(self, client: GameClient) -> None:
        self.client = client
        self._pending_bottom: list[str] | None = None  # bottom when mulligan
        self.card_loader = CardLoader()
        self.card_loader.load()

    async def run(self) -> None:
        """main loop that reads lines infinitely"""
        while self.client.state != "DISCONNECTED":
            line = await self._read_line()
            if line is None:
                break
            line = line.strip()
            if not line:
                continue

            pdu = self._parse_and_execute(line)
            if pdu is not None:
                await self.client.outgoing_queue.put(pdu)

    async def _read_line(self) -> str | None:
        """read one line without blocking the event loop"""
        loop = asyncio.get_event_loop()
        try:
            return await loop.run_in_executor(None, sys.stdin.readline)
        except (EOFError, KeyboardInterrupt):
            return None

    COMMANDS: dict[str, str] = {
        "cast": "cast",
        "land": "land",
        "pass": "pass",
        "attack": "attack",
        "block": "block",
        "keep": "keep",
        "mulligan": "mulligan",
        "bottom": "bottom",
        "concede": "concede",
        "activate": "activate",
        "order": "order",
        "discard": "discard",
        "help": "help",
        "state": "state",
        "deck": "deck",
        "ready": "ready",
        "no attacks": "no_attacks",
        "no blocks": "no_blocks",
    }

    def _parse_and_execute(self, line: str) -> dict[str, Any] | None:
        """parse command line and return PDU dict"""
        parts = line.strip().split()
        if not parts:
            return None

        cmd = parts[0].lower()
        args = parts[1:]

        #for multiword cmds
        if cmd == "no":
            if args and args[0] == "attacks":
                return self._cmd_no_attacks()
            elif args and args[0] == "blocks":
                return self._cmd_no_blocks()
            else:
                print(f"Unknown command '{line}'")
                return None

        handler = {
            "cast": self._cmd_cast,
            "land": self._cmd_land,
            "pass": self._cmd_pass,
            "attack": self._cmd_attack,
            "block": self._cmd_block,
            "keep": self._cmd_keep,
            "mulligan": self._cmd_mulligan,
            "bottom": self._cmd_bottom,
            "concede": self._cmd_concede,
            "activate": self._cmd_activate,
            "order": self._cmd_order,
            "discard": self._cmd_discard,
            "help": self._cmd_help,
            "state": self._cmd_state,
            "deck": self._cmd_deck,
            "ready": self._cmd_ready,
        }.get(cmd)

        if handler is None:
            print(f"Unknown command '{cmd}'. Type 'help' for available commands.")
            return None

        return handler(args)

    def _get_seq_num(self) -> int:
        """return seq_num for priority PDUs"""
        return self.client._current_priority_seq or 0

    def _get_client_seq_num(self) -> int:
        """return the client's seq_num for PING or PLAYER_READY"""
        if self.client.connection:
            return self.client.connection.client_seq_num
        return 0

    def _cmd_cast(self, args: list[str]) -> dict[str, Any] | None:
        if not args:
            print("Usage: cast <N> [target]")
            return None
        try:
            idx = int(args[0]) - 1
        except ValueError:
            print("Invalid index. Use: cast <N> [target]")
            return None

        hand = self.client.visible_state.get("hand", [])
        if idx < 0 or idx >= len(hand):
            print(f"Invalid hand index {args[0]}. You have {len(hand)} cards.")
            return None

        card_id = hand[idx]
        targets: list[str] = []

        if len(args) > 1:
            target_str = args[1]
            
            # translate user typed numbers
            if target_str.isdigit():
                t_idx = int(target_str) - 1
                
                # pull battlefield from the state
                vs = self.client.visible_state
                my_id = vs.get("viewer_id")
                opp_id = vs.get("opponent_id")
                
                # gather perms in the same order printed
                all_perms = []
                for pid in [opp_id, my_id]:
                    if pid:
                        all_perms.extend(vs.get("battlefield", {}).get(pid, []))
                
                if 0 <= t_idx < len(all_perms):
                    target_perm = all_perms[t_idx]
                    real_id = target_perm.get("id", target_perm.get("permanent_id", target_perm.get("card_id")))
                    targets.append(real_id)
                else:
                    print(f"Invalid target index: {target_str}")
                    return None
            else:
                # if not num, then assume id
                targets.append(target_str)

        card_def = self.card_loader.get_card(card_id)
        if not card_def:
            print(f"Error: Could not find card definition for '{card_id}'")
            return None

        mana_payment: dict[str, int] = dict(card_def.mana_cost)

        return create_cast_spell(
            seq_num=self._get_seq_num(),
            card_id=card_id,
            targets=targets,
            mana_payment=mana_payment,
        )

    def _cmd_land(self, args: list[str]) -> dict[str, Any] | None:
        if not args:
            print("Usage: land <N>")
            return None
        try:
            idx = int(args[0]) - 1
        except ValueError:
            print("Invalid index. Use: land <N>")
            return None

        hand = self.client.visible_state.get("hand", [])
        if idx < 0 or idx >= len(hand):
            print(f"Invalid hand index {args[0]}.")
            return None

        return create_play_land(seq_num=self._get_seq_num(), card_id=hand[idx])

    def _cmd_pass(self, args: list[str]) -> dict[str, Any] | None:
        return create_priority_pass(seq_num=self._get_seq_num())

    def _cmd_attack(self, args: list[str]) -> dict[str, Any] | None:
        if len(args) < 2:
            print("Usage: attack <N> <target_player_id>")
            return None
        try:
            idx = int(args[0]) - 1
        except ValueError:
            print("Invalid index.")
            return None

        target = args[1]
        bf = self.client.visible_state.get("battlefield", {})
        pid = self.client.config.player_id
        perms = bf.get(pid, [])
        if idx < 0 or idx >= len(perms):
            print(f"Invalid creature index {args[0]}.")
            return None

        creature_id = perms[idx].get("id", "")
        return create_declare_attackers(
            seq_num=self._get_seq_num(),
            attackers=[{"creature_id": creature_id, "target": target}],
        )

    def _cmd_block(self, args: list[str]) -> dict[str, Any] | None:
        if len(args) < 2:
            print("Usage: block <N> <attacker_index>")
            return None
        try:
            idx = int(args[0]) - 1
            attacker_idx = int(args[1]) - 1
        except ValueError:
            print("Invalid index.")
            return None

        pid = self.client.config.player_id
        bf = self.client.visible_state.get("battlefield", {})
        perms = bf.get(pid, [])
        if idx < 0 or idx >= len(perms):
            print(f"Invalid blocker index {args[0]}.")
            return None

        # find opps attacker by idx
        opp = self._get_opponent()
        opp_perms = bf.get(opp, [])
        if attacker_idx < 0 or attacker_idx >= len(opp_perms):
            print(f"Invalid attacker index {args[1]}.")
            return None

        blocker_id = perms[idx].get("id", "")
        attacker_id = opp_perms[attacker_idx].get("id", "")
        return create_declare_blockers(
            seq_num=self._get_seq_num(),
            blockers=[{"creature_id": blocker_id, "blocking_id": attacker_id}],
        )

    def _cmd_keep(self, args: list[str]) -> dict[str, Any] | None:
        cards_to_bottom: list[str] = []
        if self._pending_bottom is not None:
            cards_to_bottom = self._pending_bottom
            self._pending_bottom = None
        return create_mulligan_choice(
            seq_num=self._get_seq_num(),
            keep=True,
            cards_to_bottom=cards_to_bottom,
        )

    def _cmd_mulligan(self, args: list[str]) -> dict[str, Any] | None:
        self._pending_bottom = None
        return create_mulligan_choice(
            seq_num=self._get_seq_num(),
            keep=False,
            cards_to_bottom=[],
        )

    def _cmd_bottom(self, args: list[str]) -> dict[str, Any] | None:
        if not args:
            print("Usage: bottom <N1> [N2 ...]")
            return None
        hand = self.client.visible_state.get("hand", [])
        indices: list[int] = []
        for a in args:
            try:
                idx = int(a) - 1
            except ValueError:
                continue
            if 0 <= idx < len(hand):
                indices.append(idx)
        self._pending_bottom = [hand[i] for i in indices]
        print(f"Will bottom {len(self._pending_bottom)} card(s) on keep.")
        return None

    def _cmd_concede(self, args: list[str]) -> dict[str, Any] | None:
        return create_concede(
            seq_num=self._get_seq_num(),
            player_id=self.client.config.player_id,
        )

    def _cmd_activate(self, args: list[str]) -> dict[str, Any] | None:
        if len(args) < 2:
            print("Usage: activate <N> <ability_index> [target]")
            return None
        try:
            idx = int(args[0]) - 1
            ability = int(args[1])
        except ValueError:
            print("Invalid index.")
            return None

        targets: list[str] = [args[2]] if len(args) > 2 else []
        pid = self.client.config.player_id
        bf = self.client.visible_state.get("battlefield", {})
        perms = bf.get(pid, [])
        if idx < 0 or idx >= len(perms):
            print(f"Invalid permanent index {args[0]}.")
            return None

        return create_activate_ability(
            seq_num=self._get_seq_num(),
            source_id=perms[idx].get("id", ""),
            ability_index=ability,
            targets=targets,
            cost_payment={"tap": True, "mana": {}},
        )

    def _cmd_order(self, args: list[str]) -> dict[str, Any] | None:
        if len(args) < 2:
            print("Usage: order <attacker_N> <blocker1> [blocker2 ...]")
            return None
        try:
            attacker_idx = int(args[0]) - 1
        except ValueError:
            print("Invalid index.")
            return None

        opp = self._get_opponent()
        bf = self.client.visible_state.get("battlefield", {})
        opp_perms = bf.get(opp, [])
        if attacker_idx < 0 or attacker_idx >= len(opp_perms):
            print(f"Invalid attacker index {args[0]}.")
            return None

        attacker_id = opp_perms[attacker_idx].get("id", "")
        blocker_order = []
        for a in args[1:]:
            try:
                bi = int(a) - 1
            except ValueError:
                continue
            pid = self.client.config.player_id
            perms = bf.get(pid, [])
            if 0 <= bi < len(perms):
                blocker_order.append(perms[bi].get("id", ""))

        return create_assign_damage_order(
            seq_num=self._get_seq_num(),
            attacker_id=attacker_id,
            blocker_order=blocker_order,
        )

    def _cmd_discard(self, args: list[str]) -> dict[str, Any] | None:
        if not args:
            print("Usage: discard <N1> [N2 ...]")
            return None
        hand = self.client.visible_state.get("hand", [])
        indices: list[int] = []
        for a in args:
            try:
                idx = int(a) - 1
            except ValueError:
                continue
            if 0 <= idx < len(hand):
                indices.append(idx)
        card_ids = [hand[i] for i in indices]
        return create_discard(seq_num=self._get_seq_num(), card_ids=card_ids)

    def _cmd_help(self, args: list[str]) -> None:
        """print help stuff for commands"""
        state = self.client.state
        phase = self.client.visible_state.get("phase", state)
        print(f"\n--- Available commands ({phase}) ---")
        print(self._phase_help_text(state, phase))
        print("")

    def _cmd_state(self, args: list[str]) -> None:
        """raw visible states"""
        import json
        print(json.dumps(self.client.visible_state, indent=2))

    def _cmd_deck(self, args: list[str]) -> None:
        """priint deck"""
        deck = self.client.deck_list
        if not deck:
            if args:
                # card id fr cmd
                self.client.deck_list = args
                deck = args
                print(f"Deck set to {len(deck)} cards.")
            else:
                print("No deck configured. Use: deck <card_id1> <card_id2> ...")
            return
        print(f"Current deck ({len(deck)} cards):")
        for c in deck:
            print(f"  {c}")

    def _cmd_ready(self, args: list[str]) -> dict[str, Any] | None:
        """Send PLAYER_READY"""
        deck = self.client.deck_list
        if not deck:
            print("No deck configured. Use 'deck <card1> <card2> ...' first.")
            return None
        if len(deck) > 50:
            print(f"Deck too large ({len(deck)} cards, max 50).")
            return None
        seq = self._get_client_seq_num() + 1
        if self.client.connection:
            self.client.connection.client_seq_num += 1
        return create_player_ready(
            seq_num=seq,
            player_id=self.client.config.player_id,
            deck_list=deck,
        )

    def _cmd_no_attacks(self) -> dict[str, Any] | None:
        """Declare no attackers"""
        return create_declare_attackers(
            seq_num=self._get_seq_num(),
            attackers=[],
        )

    def _cmd_no_blocks(self) -> dict[str, Any] | None:
        """Declare no blockers"""
        return create_declare_blockers(
            seq_num=self._get_seq_num(),
            blockers=[],
        )

    def _get_opponent(self) -> str:
        """return opp player id"""
        pid = self.client.config.player_id
        for p in self.client.visible_state.get("life_totals", {}):
            if p != pid:
                return p
        return "opponent"

    @staticmethod
    def _phase_help_text(state: str, phase: str) -> str:
        """return help text for the current phase"""
        if state == "LOBBY":
            return (
                "  deck <card_id1> <card_id2> ...  — set your deck list\n"
                "  ready                           — submit PLAYER_READY\n"
                "  help                            — show this help"
            )
        if state == "MULLIGAN":
            return (
                "  keep     — keep your opening hand (bottoms pending cards)\n"
                "  mulligan — take a mulligan (redraw 7)\n"
                "  bottom <N1> [N2 ...] — select cards to bottom before next keep"
            )
        return (
            "  pass               — pass priority\n"
            "  cast <N> [target]  — cast spell at hand index N\n"
            "  land <N>           — play land at hand index N\n"
            "  attack <N> <tgt>   — declare attack (N = creature, tgt = player)\n"
            "  block <N> <a_N>    — declare block (N = blocker, a_N = attacker)\n"
            "  concede            — concede the game\n"
            "  help               — show this help"
        )
