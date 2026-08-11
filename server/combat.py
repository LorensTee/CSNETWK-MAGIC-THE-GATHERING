from __future__ import annotations

from typing import Any

from server.game_state import GameState, Permanent


class CombatManager:
    """manages current combat phase's state"""
    def __init__(self) -> None:
        self.attackers: dict[str, str] = {}
        self.blockers: dict[str, str] = {}
        self.damage_order: dict[str, list[str]] = {}
        self._attacking_creatures: set[str] = set()

    def reset(self) -> None:
        """clear all combat state"""
        self.attackers.clear()
        self.blockers.clear()
        self.damage_order.clear()
        self._attacking_creatures.clear()

    def set_attackers(self, gs: GameState, player: str, attackers: list[dict[str, str]]) -> list[dict[str, Any]]:
        """record and validate declared attackers"""
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

            self.attackers[cid] = target
            self._attacking_creatures.add(cid)

            #check vigilance from abilities and tap if needed
            has_vigilance = any(
                a.get("type") == "keyword" and a.get("name") == "vigilance"
                for a in getattr(perm, "abilities", [])
            )
            
            if not has_vigilance:
                perm.tapped = True
                changes.append({"change_type": "TAP", "target": cid})

        return changes

    def set_blockers(self, gs: GameState, player: str, blockers: list[dict[str, str]]) -> list[dict[str, Any]]:
        """record declared blockers"""
        self.blockers.clear()
        for entry in blockers:
            cid = entry["creature_id"]
            blocking = entry["blocking_id"]
            self.blockers[cid] = blocking
        return []

    def set_damage_order(self, attacker_id: str, blocker_order: list[str]) -> None:
        """record damage order for multi blocked attacker"""
        self.damage_order[attacker_id] = list(blocker_order)

    def compute_first_strike_damage(self, gs: GameState) -> dict[str, Any]:
        """compute first strike damage step"""
        return self._compute_damage(gs, first_strike_only=True)

    def compute_combat_damage(self, gs: GameState) -> dict[str, Any]:
        """compute regular combat damage step"""
        return self._compute_damage(gs, first_strike_only=False)

    def _compute_damage(self, gs: GameState, first_strike_only: bool) -> dict[str, Any]:
        """damage computation"""
        damage_events: list[dict[str, Any]] = []
        creatures_died: list[str] = []

        #find defending player
        ap_id = gs.active_player or ""
        def_id = ""
        for pid in gs.player_ids:
            if pid != ap_id:
                def_id = pid
                break

        for cid, target in self.attackers.items():
            perm = self._find_permanent(gs, ap_id, cid)
            if perm is None:
                continue

            if first_strike_only != self._has_first_strike(perm):
                continue

            power = perm.power

            blockers_for_this = [
                b_id for b_id, a_id in self.blockers.items() if a_id == cid
            ]

            if not blockers_for_this:
                damage_events.append({
                    "source": cid,
                    "target": target,
                    "amount": power,
                })
            else:
                remaining = power
                ordered = self.damage_order.get(cid, blockers_for_this)
                for blocker_id in ordered:
                    if blocker_id not in blockers_for_this:
                        continue
                    blocker_perm = self._find_permanent(
                        gs, def_id, blocker_id
                    )
                    if blocker_perm is None:
                        continue 

                    lethal = blocker_perm.toughness - blocker_perm.damage
                    dealt = min(remaining, lethal)

                    damage_events.append({
                        "source": cid,
                        "target": blocker_id,
                        "amount": dealt,
                    })
                    blocker_perm.damage += dealt
                    remaining -= dealt

                    if blocker_perm.damage >= blocker_perm.toughness:
                        creatures_died.append(blocker_id)

                if remaining > 0 and self._has_trample(perm):
                    damage_events.append({
                        "source": cid,
                        "target": target,
                        "amount": remaining,
                    })

        #blocking creatures deal damage to their attackers
        for b_id, a_id in self.blockers.items():
            b_perm = self._find_permanent(gs, def_id, b_id)
            if b_perm is None:
                continue

            if first_strike_only != self._has_first_strike(b_perm):
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

                # check if attacker ded
                if a_perm.damage >= a_perm.toughness:
                    creatures_died.append(a_id)

        #Compute updated life totals
        new_life = dict(gs.life_totals)
        for event in damage_events:
            target = event["target"]
            # dmg to a player reduces life
            if target in gs.life_totals:
                new_life[target] -= event["amount"]
                
        gs.life_totals = new_life

        # if dead
        for died_id in creatures_died:
            for pid, perms in gs.battlefield.items():
                for i, perm in enumerate(perms):
                    if perm.id == died_id:
                        # move to graveyard
                        gs.graveyards.setdefault(pid, []).append(died_id)
                        perms.pop(i)
                        break

        return {
            "damage_events": damage_events,
            "life_totals": new_life,
            "creatures_died": creatures_died,
        }

    #internal helpers

    def _find_permanent(
        self, gs: GameState, player: str, permanent_id: str
    ) -> Permanent | None:
        """look up permanent on the battlefield by ID"""
        for perm in gs.battlefield.get(player, []):
            if perm.id == permanent_id:
                return perm
        return None

    @staticmethod
    def _has_first_strike(perm: Permanent) -> bool:
        """return true if the permanent has first strike"""
        return any(
            a.get("type") == "keyword" and a.get("name") == "first_strike"
            for a in getattr(perm, "abilities", [])
        )

    @staticmethod
    def _has_trample(perm: Permanent) -> bool:
        """return true if the permanent has trample"""
        return any(
            a.get("type") == "keyword" and a.get("name") == "trample"
            for a in getattr(perm, "abilities", [])
        )

    @staticmethod
    def _has_vigilance(perm: Permanent) -> bool:
        """return true if the permanent has vigilance"""
        return any(
            a.get("type") == "keyword" and a.get("name") == "vigilance"
            for a in getattr(perm, "abilities", [])
        )
