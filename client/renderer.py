from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client.client import GameClient


class Renderer:
    """renders visible game state to the terminal"""
    def __init__(self, client: GameClient) -> None:
        self.client = client
        self._width = 78

    def draw(self, state: str, visible_state: dict[str, Any]) -> None:
        """clear screen and redraw"""
        self._clear_screen()

        if not visible_state:
            self._draw_connecting()
            return

        player_id = self.client.config.player_id or "?"
        opponent = visible_state.get("opponent_id") 
        if not opponent:
            opponent = "?"
            
        phase = visible_state.get("phase", "?")
        turn = visible_state.get("turn", 0)
        phase = visible_state.get("phase", "?")
        turn = visible_state.get("turn", 0)

        lines: list[str] = []

        # header
        header = (
            f" MTGNP 1.0 — You are: {player_id}  "
            f"Turn: {turn}  Phase: {phase} "
        )
        lines.extend(self._bordered_double(header))

        # opponents board
        opp_lines = self._build_opponent_board(visible_state, opponent, player_id)
        lines.extend(self._bordered_box(f" Opponent ({opponent}) ", opp_lines))

        # stack laman
        stack_lines = self._build_stack(visible_state)
        if stack_lines:
            lines.extend(self._bordered_box(" Stack ", stack_lines))

        # players board
        own_lines = self._build_own_board(visible_state, player_id)
        lines.extend(self._bordered_box(f" Your Board ({player_id}) ", own_lines))

        # cards on hand
        hand_lines = self._build_hand(visible_state, player_id)
        lines.extend(self._bordered_box(" Your Hand ", hand_lines))

        # stats
        status = self._status_line(state, visible_state, player_id)
        lines.extend(self._bordered_double(status))

        # prompt
        prompt = self._phase_prompt(phase, state)
        lines.append(prompt)
        lines.append("> ")

        # print all
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()

    def _build_opponent_board(
        self,
        vs: dict[str, Any],
        opponent: str,
        player_id: str,
    ) -> list[str]:
        """build the opponents board lines"""
        lines: list[str] = []

        life = vs.get("life_totals", {}).get(opponent, 20)
        lines.append(f"  Life: {self._life_bar(life)}")

        hc = vs.get("hand_counts", {})
        hand_count = hc.get(opponent, 0) if isinstance(hc, dict) else 0
        lib_count = vs.get("library_counts", {}).get(opponent, 0)
        lines.append(f"  Hand: {hand_count} cards  |  Library: {lib_count} cards")

        # battlefield
        bf = vs.get("battlefield", {})
        opp_perms = bf.get(opponent, [])
        if opp_perms:
            lines.append("  Battlefield:")
            for perm in opp_perms:
                lines.append(f"    {self._permanent_line(perm)}")
        else:
            lines.append("  Battlefield: (empty)")

        # graveyard
        gy = vs.get("graveyard", {})
        opp_gy = gy.get(opponent, [])
        if opp_gy:
            gy_str = ", ".join(self._short_name(c) for c in opp_gy[-5:])  #last 5
            lines.append(f"  Graveyard: [{gy_str}]")
        else:
            lines.append("  Graveyard: (empty)")

        return lines

    def _build_own_board(
        self,
        vs: dict[str, Any],
        player_id: str,
    ) -> list[str]:
        """Build the players board lines"""
        lines: list[str] = []

        life = vs.get("life_totals", {}).get(player_id, 20)
        lines.append(f"  Life: {self._life_bar(life)}")

        lib_count = vs.get("library_counts", {}).get(player_id, 0)
        lines.append(f"  Library: {lib_count} cards")

        # battlefield
        bf = vs.get("battlefield", {})
        own_perms = bf.get(player_id, [])
        if own_perms:
            lines.append("  Battlefield:")
            for i, perm in enumerate(own_perms, 1):
                lines.append(f"  [{i}] {self._permanent_line(perm)}")
        else:
            lines.append("  Battlefield: (empty)")

        # graveyard
        gy = vs.get("graveyard", {})
        own_gy = gy.get(player_id, [])
        if own_gy:
            gy_str = ", ".join(self._short_name(c) for c in own_gy[-5:])
            lines.append(f"  Graveyard: [{gy_str}]")
        else:
            lines.append("  Graveyard: (empty)")

        return lines

    def _build_hand(
        self,
        vs: dict[str, Any],
        player_id: str,
    ) -> list[str]:
        """Build hand lines"""
        lines: list[str] = []
        hand = vs.get("hand", [])
        if hand:
            for i, card_id in enumerate(hand, 1):
                lines.append(f"  [{i:2d}] {self._short_name(card_id)}")
        else:
            lines.append("  (empty)")
        return lines

    def _build_stack(
        self,
        vs: dict[str, Any],
    ) -> list[str]:
        """Build stack lines"""
        stack = vs.get("stack", [])
        if not stack:
            return []
        lines: list[str] = []
        for i, si in enumerate(stack):
            prefix = "  "
            suffix = ""
            if i == len(stack) - 1:
                suffix = "  ← TOP"
            source = self._short_name(si.get("source", "?"))
            ctrl = si.get("controller", "?")
            tgt = si.get("targets", [])
            tgt_str = ", ".join(self._short_name(t) for t in tgt) if tgt else "no targets"
            lines.append(f"{prefix}{si.get('stack_item_id','?')} {source} ({ctrl}) → {tgt_str}{suffix}")
        return lines

    def _status_line(
        self,
        state: str,
        vs: dict[str, Any],
        player_id: str,
    ) -> str:
        """Build the stats lines"""
        if state in ("LOBBY", "GAME_SETUP"):
            return f" Status: {state}  —  {vs.get('waiting_for', 'waiting for players')} "
        if state == "MULLIGAN":
            return " Status: Mulligan — Keep or mulligan your opening hand? "
        if state == "GAME_OVER":
            return (
                f" Game Over!  Winner: {vs.get('winner_id','?')}  "
                f"Loser: {vs.get('loser_id','?')}  Reason: {vs.get('reason','?')} "
            )
        # IN_GAME
        ph = vs.get("priority_holder")
        if ph == player_id:
            return " [YOU HAVE PRIORITY]  (pass / cast / land / concede / help) "
        elif ph:
            return f" [WAITING — {ph} has priority] "
        else:
            return " [WAITING — no priority window] "

    def _phase_prompt(self, phase: str, state: str) -> str:
        """return the phase cmd hint"""
        if state == "LOBBY":
            return "Commands: deck <card_id> ...  |  ready  |  help"
        if state == "MULLIGAN":
            return "Commands: keep  |  mulligan  |  help"
        if state == "GAME_OVER":
            return "Game over! Send 'ready' to start a new game."

        prompts = {
            "PRECOMBAT_MAIN": "cast <N> [target]  |  land <N>  |  activate <N> <a> [t]  |  pass  |  concede",
            "POSTCOMBAT_MAIN": "cast <N> [target]  |  land <N>  |  activate <N> <a> [t]  |  pass  |  concede",
            "DECLARE_ATTACKERS": "attack <N> <target>  |  no attacks  |  pass",
            "DECLARE_BLOCKERS": "block <N> <attacker_N>  |  no blocks  |  pass",
            "ASSIGN_DAMAGE_ORDER": "order <attacker_N> <blocker1> [blocker2...]",
            "DRAW": "pass  |  activate <N> <a> [t]  |  concede",
            "UPKEEP": "pass  |  activate <N> <a> [t]  |  concede",
            "END_STEP": "pass  |  activate <N> <a> [t]  |  concede",
            "END_OF_COMBAT": "pass  |  activate <N> <a> [t]  |  concede",
            "BEGIN_COMBAT": "pass  |  activate <N> <a> [t]  |  concede",
            "CLEANUP": "discard <N1> [N2...]  |  pass",
        }
        return f"Commands: {prompts.get(phase, 'pass  |  cast <N> [target]  |  concede  |  help')}"

    @staticmethod
    def _life_bar(life: int) -> str:
        """return a visual life bar"""
        life = max(0, min(life, 20))
        filled = life
        empty = 20 - filled
        bar = "█" * filled + "░" * empty
        return f"{bar}  {life}"

    @staticmethod
    def _permanent_line(perm: dict[str, Any]) -> str:
        """format 1 perm for display"""
        tap = "[T]" if perm.get("tapped") else "[ ]"
        name = perm.get("id", "?")

        is_creature = (
            "power" in perm or "toughness" in perm
        ) and perm.get("toughness", 0) > 0

        if not is_creature:
            return f"{tap} {name}"

        power = perm.get("power", 0)
        toughness = perm.get("toughness", 0)
        damage = perm.get("damage", 0)

        stats = f"({power}/{toughness}"
        if damage > 0:
            stats += f", {damage} dmg"
        stats += ")"

        abbr = perm.get("abilities", [])
        if abbr:
            abbrev_str = self._ability_abbrevs(abbr)
        else:
            abbrev_str = ""

        ss = " SS" if perm.get("summoning_sick") else ""
        suffix = f" {abbrev_str}{ss}" if abbrev_str or ss else ""
        return f"{tap} {name} {stats}{suffix}"

    @staticmethod
    def _short_name(card_id: str) -> str:
        """Shorten a card ID for display"""
        # removes the numbers at the end
        parts = card_id.rsplit("_", 1)
        if parts[-1].isdigit():
            return parts[0]
        return card_id

    @staticmethod
    def _ability_abbrevs(abilities: list) -> str:
        """maps to short display codes"""
        KEYWORD_MAP = {
            "haste": "H",
            "flying": "F",
            "first_strike": "FS",
            "trample": "T",
            "defender": "D",
            "vigilance": "V",
            "hexproof": "X",
            "protection": "P",
        }
        codes: list[str] = []
        for ab in abilities:
            if isinstance(ab, dict):
                name = ab.get("name", "")
                code = KEYWORD_MAP.get(name)
                if code:
                    codes.append(code)
            elif isinstance(ab, str):
                code = KEYWORD_MAP.get(ab)
                if code:
                    codes.append(code)
        return "[" + " ".join(codes) + "]" if codes else ""

    @staticmethod
    def _get_opponent(vs: dict[str, Any]) -> str:
        """derive opponent's player ID"""
        life = vs.get("life_totals", {})
        for pid in life:
            return pid
        return "opponent"

    @classmethod
    def _bordered_box(cls, title: str, lines: list[str]) -> list[str]:
        """lines for single line boxes"""
        if not lines:
            lines = ["  (empty)"]
        width = max(len(l) for l in lines) if lines else 20
        width = max(width, len(title) + 4)
        result: list[str] = []
        # top border w/ title
        top = "┌─" + title + "─" + "─" * (width - len(title) - 2) + "┐"
        result.append(top)
        for line in lines:
            result.append(f"│ {line:<{width - 2}} │")
        result.append("└" + "─" * (width) + "┘")
        return result

    @classmethod
    def _bordered_double(cls, text: str) -> list[str]:
        """double line box"""
        width = max(len(text), 30)
        return [
            "╔" + "═" * width + "╗",
            "║" + f"{text:<{width}}" + "║",
            "╚" + "═" * width + "╝",
        ]

    @staticmethod
    def _clear_screen() -> None:
        """clears the terminal"""
        if sys.stdout.isatty():
            sys.stdout.write("\033[2J\033[H")
        else:
            sys.stdout.write("\n" * 50)
        sys.stdout.flush()

    def _draw_connecting(self) -> None:
        """draw placeholder while waiting for update"""
        lines = self._bordered_double(" MTGNP 1.0 — Connecting to server... ")
        lines.append("")
        lines.append("  Waiting for game state...")
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
