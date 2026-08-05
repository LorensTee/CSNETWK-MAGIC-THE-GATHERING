"""
client/renderer.py — Terminal Renderer (Module 03: Client App)

Draws the visible game state in a terminal-based UI using Unicode box-drawing
characters.  The renderer is deliberately simple: it formats what the server
sends and NEVER computes game outcomes.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from client.client import GameClient


class Renderer:
    """Renders the visible game state to the terminal.

    Parameters
    ----------
    client :
        The ``GameClient`` whose *visible_state* and *state* are drawn.
    """

    def __init__(self, client: GameClient) -> None:
        self.client = client
        self._width = 78

    # ═══════════════════════════════════════════════════════════════════════════
    # Public entry point
    # ═══════════════════════════════════════════════════════════════════════════

    def draw(self, state: str, visible_state: dict[str, Any]) -> None:
        """Clear the screen and redraw the full UI.

        Parameters
        ----------
        state :
            Client state string (``'LOBBY'``, ``'MULLIGAN'``, ``'IN_GAME'``,
            ``'GAME_OVER'``, …).
        visible_state :
            The ``state`` dict from the most recent ``GAME_STATE_UPDATE``.
        """
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

        # ── Header ──────────────────────────────────────────────────────
        header = (
            f" MTGNP 1.0 — You are: {player_id}  "
            f"Turn: {turn}  Phase: {phase} "
        )
        lines.extend(self._bordered_double(header))

        # ── Opponent board ──────────────────────────────────────────────
        opp_lines = self._build_opponent_board(visible_state, opponent, player_id)
        lines.extend(self._bordered_box(f" Opponent ({opponent}) ", opp_lines))

        # ── Stack ───────────────────────────────────────────────────────
        stack_lines = self._build_stack(visible_state)
        if stack_lines:
            lines.extend(self._bordered_box(" Stack ", stack_lines))

        # ── Your board ──────────────────────────────────────────────────
        own_lines = self._build_own_board(visible_state, player_id)
        lines.extend(self._bordered_box(f" Your Board ({player_id}) ", own_lines))

        # ── Your hand ───────────────────────────────────────────────────
        hand_lines = self._build_hand(visible_state, player_id)
        lines.extend(self._bordered_box(" Your Hand ", hand_lines))

        # ── Status line ─────────────────────────────────────────────────
        status = self._status_line(state, visible_state, player_id)
        lines.extend(self._bordered_double(status))

        # ── Prompt ──────────────────────────────────────────────────────
        prompt = self._phase_prompt(phase, state)
        lines.append(prompt)
        lines.append("> ")

        # Print everything.
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()

    # ═══════════════════════════════════════════════════════════════════════════
    # Board sections
    # ═══════════════════════════════════════════════════════════════════════════

    def _build_opponent_board(
        self,
        vs: dict[str, Any],
        opponent: str,
        player_id: str,
    ) -> list[str]:
        """Build the opponent-board section lines."""
        lines: list[str] = []

        life = vs.get("life_totals", {}).get(opponent, 20)
        lines.append(f"  Life: {self._life_bar(life)}")

        hc = vs.get("hand_counts", {})
        hand_count = hc.get(opponent, 0) if isinstance(hc, dict) else 0
        lib_count = vs.get("library_counts", {}).get(opponent, 0)
        lines.append(f"  Hand: {hand_count} cards  |  Library: {lib_count} cards")

        # Battlefield.
        bf = vs.get("battlefield", {})
        opp_perms = bf.get(opponent, [])
        if opp_perms:
            lines.append("  Battlefield:")
            for perm in opp_perms:
                lines.append(f"    {self._permanent_line(perm)}")
        else:
            lines.append("  Battlefield: (empty)")

        # Graveyard.
        gy = vs.get("graveyard", {})
        opp_gy = gy.get(opponent, [])
        if opp_gy:
            gy_str = ", ".join(self._short_name(c) for c in opp_gy[-5:])  # last 5
            lines.append(f"  Graveyard: [{gy_str}]")
        else:
            lines.append("  Graveyard: (empty)")

        return lines

    def _build_own_board(
        self,
        vs: dict[str, Any],
        player_id: str,
    ) -> list[str]:
        """Build the own-board section lines."""
        lines: list[str] = []

        life = vs.get("life_totals", {}).get(player_id, 20)
        lines.append(f"  Life: {self._life_bar(life)}")

        lib_count = vs.get("library_counts", {}).get(player_id, 0)
        lines.append(f"  Library: {lib_count} cards")

        # Battlefield.
        bf = vs.get("battlefield", {})
        own_perms = bf.get(player_id, [])
        if own_perms:
            lines.append("  Battlefield:")
            for i, perm in enumerate(own_perms, 1):
                lines.append(f"  [{i}] {self._permanent_line(perm)}")
        else:
            lines.append("  Battlefield: (empty)")

        # Graveyard.
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
        """Build the hand section lines (numbered cards)."""
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
        """Build the stack section lines."""
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

    # ═══════════════════════════════════════════════════════════════════════════
    # Status & prompt
    # ═══════════════════════════════════════════════════════════════════════════

    def _status_line(
        self,
        state: str,
        vs: dict[str, Any],
        player_id: str,
    ) -> str:
        """Build the status line (inside double-border box)."""
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
        """Return the phase-appropriate command hint."""
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
            "DRAW": "pass  |  cast <N> [target]  |  concede",
            "UPKEEP": "pass  |  cast <N> [target]  |  concede",
            "END_STEP": "pass  |  cast <N> [target]  |  concede",
            "END_OF_COMBAT": "pass  |  cast <N> [target]  |  concede",
            "BEGIN_COMBAT": "pass  |  cast <N> [target]  |  concede",
            "CLEANUP": "discard <N1> [N2...]  |  pass",
        }
        return f"Commands: {prompts.get(phase, 'pass  |  cast <N> [target]  |  concede  |  help')}"

    # ═══════════════════════════════════════════════════════════════════════════
    # Helper formatting
    # ═══════════════════════════════════════════════════════════════════════════

    @staticmethod
    def _life_bar(life: int) -> str:
        """Return a visual life bar, e.g. ``████████████████░░░░  16``."""
        life = max(0, min(life, 20))
        filled = life
        empty = 20 - filled
        bar = "█" * filled + "░" * empty
        return f"{bar}  {life}"

    @staticmethod
    def _permanent_line(perm: dict[str, Any]) -> str:
        """Format one permanent for display.

        Non-creatures: ``[T] name``  or  ``[ ] name``
        Creatures:     ``[T] name (P/T, N dmg) [abilities]``
        """
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

        # Abbrevs.
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
        """Shorten a card instance ID for display.

        ``'lightning_bolt_001'`` → ``'lightning_bolt'``
        ``'mountain_001'`` → ``'mountain'``
        """
        # If it ends with _NNN, strip the number.
        parts = card_id.rsplit("_", 1)
        if parts[-1].isdigit():
            return parts[0]
        return card_id

    @staticmethod
    def _ability_abbrevs(abilities: list) -> str:
        """Map keyword ability names to short display codes."""
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
        """Derive the opponent's player ID from visible state."""
        life = vs.get("life_totals", {})
        for pid in life:
            # Return the first player ID that isn't us (we'll know later).
            return pid
        return "opponent"

    # ═══════════════════════════════════════════════════════════════════════════
    # Box-drawing helpers
    # ═══════════════════════════════════════════════════════════════════════════

    @classmethod
    def _bordered_box(cls, title: str, lines: list[str]) -> list[str]:
        """Wrap *lines* in a single-line box ``┌─┐`` / ``│`` / ``└─┘``."""
        if not lines:
            lines = ["  (empty)"]
        width = max(len(l) for l in lines) if lines else 20
        width = max(width, len(title) + 4)
        result: list[str] = []
        # Top border with title.
        top = "┌─" + title + "─" + "─" * (width - len(title) - 2) + "┐"
        result.append(top)
        for line in lines:
            result.append(f"│ {line:<{width - 2}} │")
        result.append("└" + "─" * (width) + "┘")
        return result

    @classmethod
    def _bordered_double(cls, text: str) -> list[str]:
        """Wrap *text* in a double-line box ``╔═╗`` / ``║`` / ``╚═╝``."""
        width = max(len(text), 30)
        return [
            "╔" + "═" * width + "╗",
            "║" + f"{text:<{width}}" + "║",
            "╚" + "═" * width + "╝",
        ]

    @staticmethod
    def _clear_screen() -> None:
        """Clear the terminal using ANSI escape codes.

        Falls back to 50 blank lines if the output is not a TTY.
        """
        if sys.stdout.isatty():
            sys.stdout.write("\033[2J\033[H")
        else:
            sys.stdout.write("\n" * 50)
        sys.stdout.flush()

    def _draw_connecting(self) -> None:
        """Draw a placeholder while waiting for the first state update."""
        lines = self._bordered_double(" MTGNP 1.0 — Connecting to server... ")
        lines.append("")
        lines.append("  Waiting for game state...")
        sys.stdout.write("\n".join(lines))
        sys.stdout.flush()
