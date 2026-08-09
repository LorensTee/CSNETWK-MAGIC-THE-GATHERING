"""
server/combat.py — Combat System (Module 02: Server Engine)

Implements the full MTGNP combat sequence (RFC §8):

1. Declare Attackers  — AP chooses which creatures attack and whom they attack.
2. Declare Blockers   — NAP chooses which creatures block which attackers.
3. Assign Damage Order — AP orders blockers for multi-blocked attackers.
4. First Strike Damage — Only creatures with first strike deal damage.
5. Combat Damage      — All remaining creatures deal damage simultaneously.

This module returns data structures — the caller (``GameLifecycle``) broadcasts
``COMBAT_DAMAGE_RESULT`` and ``PHASE_TRANSITION`` PDUs.
"""

from __future__ import annotations

from typing import Any

from server.game_state import GameState, Permanent


class CombatManager:
    """Manages the current combat phase's state.

    Tracks attacking creatures, blocking assignments, and damage order.
    All damage computation is done here; the caller just broadcasts results.
    """

    def __init__(self) -> None:
        # creature_id → target player_id
        self.attackers: dict[str, str] = {}
        # blocker_id → attacker_id
        self.blockers: dict[str, str] = {}
        # attacker_id → ordered list of blocker_ids (damage assignment order)
        self.damage_order: dict[str, list[str]] = {}
        # Track creatures that attacked this combat (for untap after combat).
        self._attacking_creatures: set[str] = set()

    def reset(self) -> None:
        """Clear all combat state (called after combat phase resolves)."""
        self.attackers.clear()
        self.blockers.clear()
        self.damage_order.clear()
        self._attacking_creatures.clear()

    def set_attackers(self, gs: GameState, player: str, attackers: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Record and validate declared attackers.

        Parameters
        ----------
        gs :
            Game state (mutated: attackers tapped unless they have vigilance).
        player :
            Attacking player ID (AP).
        attackers :
            List of ``{'creature_id': ..., 'target': ...}`` dicts.

        Returns
        -------
        State-change dicts describing the tap events.
        """
        self.attackers.clear()
        self._attacking_creatures.clear()

        changes: list[dict[str, Any]] = []
        for entry in attackers:
            cid = entry["creature_id"]
            target = entry["target"]

            perm = self._find_permanent(gs, player, cid)

            if not perm:
                continue

            if perm.tapped:
                print(f"[Combat] Rejected {cid}: Already tapped!")
                continue

            if getattr(perm, "summoning_sick", False):
                print(f"[Combat] Rejected {cid}: Summoning sickness!")
                continue

            if not hasattr(perm, 'power') or perm.power is None:
                print(f"[Combat] Rejected {cid}: Not a creature!")
                continue

            # ✅ SAFE TO ADD NOW!
            self.attackers[cid] = target
            self._attacking_creatures.add(cid)

            # Check vigilance via abilities and tap if needed.
            has_vigilance = any(
                a.get("type") == "keyword" and a.get("name") == "vigilance"
                for a in getattr(perm, "abilities", [])
            )
            
            if not has_vigilance:
                perm.tapped = True
                changes.append({"change_type": "TAP", "target": cid})

        return changes

    def set_blockers(self, gs: GameState, player: str, blockers: list[dict[str, str]]) -> list[dict[str, Any]]:
        """Record declared blockers.

        Blockers do NOT tap (unlike attackers).
        """
        self.blockers.clear()
        for entry in blockers:
            cid = entry["creature_id"]
            blocking = entry["blocking_id"]
            self.blockers[cid] = blocking
        return []

    def set_damage_order(self, attacker_id: str, blocker_order: list[str]) -> None:
        """Record the damage order for a multi-blocked attacker.

        The attacker deals damage to blockers in *blocker_order*; damage
        must be lethal to each before proceeding to the next.
        """
        self.damage_order[attacker_id] = list(blocker_order)

    def has_first_strike_participants(self, gs: GameState) -> bool:
        """Return ``True`` if any attacking or blocking creature has first
        strike or double strike (RFC §9.6: the First Strike Damage Step is
        optional and only runs when such a creature is present).
        """
        for cid in list(self.attackers) + list(self.blockers):
            perm = self._find_any_permanent(gs, cid)
            if perm is not None and self._deals_first_strike(perm):
                return True
        return False

    def compute_first_strike_damage(self, gs: GameState) -> dict[str, Any]:
        """Compute first strike damage step.

        Only creatures with first strike deal damage in this step.
        Non-first-strike creatures wait for the regular combat damage step.

        Returns
        -------
        A dict with keys *damage_events*, *life_totals*, *creatures_died*.
        """
        return self._compute_damage(gs, first_strike_only=True)

    def compute_combat_damage(self, gs: GameState) -> dict[str, Any]:
        """Compute regular (non-first-strike) combat damage step.

        All surviving creatures (without first strike, or creatures with
        double strike) deal damage simultaneously.

        Returns
        -------
        A dict with keys *damage_events*, *life_totals*, *creatures_died*.
        """
        return self._compute_damage(gs, first_strike_only=False)

    def _compute_damage(self, gs: GameState, first_strike_only: bool) -> dict[str, Any]:
        """Core damage computation logic.

        * If *first_strike_only* is ``True``, only first-strike creatures deal
          and receive damage.
        * If *first_strike_only* is ``False``, only non-first-strike creatures
          deal and receive damage.
        """
        damage_events: list[dict[str, Any]] = []
        creatures_died: list[str] = []

        # Find the defending player (non-AP).
        ap_id = gs.active_player or ""
        def_id = ""
        for pid in gs.player_ids:
            if pid != ap_id:
                def_id = pid
                break

        # ── Attacking creatures deal damage ──────────────────────────────
        for cid, target in self.attackers.items():
            perm = self._find_permanent(gs, ap_id, cid)
            if perm is None:
                continue  # Attacker died in first strike step.

            if first_strike_only:
                # FS step: first strike AND double strike creatures deal damage.
                if not self._deals_first_strike(perm):
                    continue
            else:
                # Normal step: pure first-strike creatures already dealt
                # damage; double strike deals damage in BOTH steps (§9.7).
                if self._has_first_strike(perm) and not self._has_double_strike(perm):
                    continue

            power = perm.power

            # Is this attacker blocked?
            blockers_for_this = [
                b_id for b_id, a_id in self.blockers.items() if a_id == cid
            ]

            if not blockers_for_this:
                # Unblocked → damage to defending player.
                damage_events.append({
                    "source": cid,
                    "target": target,
                    "amount": power,
                })
                # Apply trample to player if unblocked and has trample (redundant).
            else:
                # Blocked → damage to blockers in order (with trample overflow).
                remaining = power
                # Get damage order if multi-block, otherwise single blocker.
                ordered = self.damage_order.get(cid, blockers_for_this)
                for blocker_id in ordered:
                    if blocker_id not in blockers_for_this:
                        continue
                    blocker_perm = self._find_permanent(
                        gs, def_id, blocker_id
                    )
                    if blocker_perm is None:
                        continue  # Blocker already died this step.

                    lethal = blocker_perm.toughness - blocker_perm.damage
                    dealt = min(remaining, lethal)

                    damage_events.append({
                        "source": cid,
                        "target": blocker_id,
                        "amount": dealt,
                    })
                    blocker_perm.damage += dealt
                    remaining -= dealt

                    # Check if blocker dies.
                    if blocker_perm.damage >= blocker_perm.toughness:
                        creatures_died.append(blocker_id)

                # NOTE: MTGNP 1.0 does NOT implement trample (RFC §9.7).
                # A blocked attacker never deals damage to the defending
                # player; leftover damage is simply not assigned.

        # ── Blocking creatures deal damage to their attackers ────────────
        for b_id, a_id in self.blockers.items():
            b_perm = self._find_permanent(gs, def_id, b_id)
            if b_perm is None:
                continue

            if first_strike_only:
                if not self._deals_first_strike(b_perm):
                    continue
            else:
                if self._has_first_strike(b_perm) and not self._has_double_strike(b_perm):
                    continue

            a_perm = self._find_permanent(gs, ap_id, a_id)
            if a_perm is None:
                continue

            b_power = b_perm.power
            if b_power > 0:
                damage_events.append({
                    "source": b_id,
                    "target": a_id,
                    "amount": b_power,
                })
                a_perm.damage += b_power

                # Check if attacker dies.
                if a_perm.damage >= a_perm.toughness:
                    creatures_died.append(a_id)

        # ── Compute updated life totals ──────────────────────────────────
        new_life = dict(gs.life_totals)
        for event in damage_events:
            target = event["target"]
            # Damage to a player reduces their life.
            if target in gs.life_totals:
                new_life[target] -= event["amount"]
                
        gs.life_totals = new_life

        # ── Apply deaths ─────────────────────────────────────────────────
        for died_id in creatures_died:
            for pid, perms in gs.battlefield.items():
                for i, perm in enumerate(perms):
                    if perm.id == died_id:
                        # Move to graveyard.
                        gs.graveyards.setdefault(pid, []).append(died_id)
                        perms.pop(i)
                        break

        return {
            "damage_events": damage_events,
            "life_totals": new_life,
            "creatures_died": creatures_died,
        }

    # ── Internal helpers ─────────────────────────────────────────────────

    def _find_permanent(
        self, gs: GameState, player: str, permanent_id: str
    ) -> Permanent | None:
        """Look up a permanent on the battlefield by ID."""
        for perm in gs.battlefield.get(player, []):
            if perm.id == permanent_id:
                return perm
        return None

    @staticmethod
    def _find_any_permanent(gs: GameState, permanent_id: str) -> Permanent | None:
        """Look up a permanent on any player's battlefield by ID."""
        for perms in gs.battlefield.values():
            for perm in perms:
                if perm.id == permanent_id:
                    return perm
        return None

    @staticmethod
    def _deals_first_strike(perm: Permanent) -> bool:
        """Return ``True`` if the permanent deals damage in the first strike
        step (has first strike OR double strike).
        """
        return (
            CombatManager._has_first_strike(perm)
            or CombatManager._has_double_strike(perm)
        )

    @staticmethod
    def _has_first_strike(perm: Permanent) -> bool:
        """Return ``True`` if the permanent has first strike."""
        return any(
            a.get("type") == "keyword" and a.get("name") == "first_strike"
            for a in getattr(perm, "abilities", [])
        )

    @staticmethod
    def _has_double_strike(perm: Permanent) -> bool:
        """Return ``True`` if the permanent has double strike."""
        return any(
            a.get("type") == "keyword" and a.get("name") == "double_strike"
            for a in getattr(perm, "abilities", [])
        )

    @staticmethod
    def _has_vigilance(perm: Permanent) -> bool:
        """Return ``True`` if the permanent has vigilance."""
        return any(
            a.get("type") == "keyword" and a.get("name") == "vigilance"
            for a in getattr(perm, "abilities", [])
        )
