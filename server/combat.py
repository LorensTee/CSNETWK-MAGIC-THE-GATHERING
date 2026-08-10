# combat system module
# handles mtgnp combat steps like attacking blocking and damage calculation

from __future__ import annotations

from typing import Any

from server.game_state import GameState, Permanent


class CombatManager:
    # combat manager class
	# keeps track of attackers blockers damage ordering and damage events

    def __init__(self) -> None:
        # creature_id → target player_id
        self.attackers: dict[str, str] = {}
        # blocker_id → attacker_id
        self.blockers: dict[str, str] = {}
        # attacker_id → ordered list of blocker_ids (damage assignment order)
        self.damage_order: dict[str, list[str]] = {}
        # Track creatures that attacked this combat (for untap after combat).
        self._attacking_creatures: set[str] = set()
        # Rejected attacker declarations: [{'creature_id', 'reason'}, ...].
        # Surfaced to the caller so it can answer with ERROR ILLEGAL_ACTION
        # (RFC §11) instead of silently dropping the declaration.
        self.rejected_attackers: list[dict[str, str]] = []

    def reset(self) -> None:
        # init method sets up empty dicts and sets for combat tracking
        self.attackers.clear()
        self.blockers.clear()
        self.damage_order.clear()
        self._attacking_creatures.clear()
        self.rejected_attackers.clear()

    def set_attackers(self, gs: GameState, player: str, attackers: list[dict[str, str]]) -> list[dict[str, Any]]:
        # record and validate declared attackers

		# clear existing attackers and rejections
        self.attackers.clear()
        self._attacking_creatures.clear()
        self.rejected_attackers.clear()

		# check each attacker entry
        changes: list[dict[str, Any]] = []
        for entry in attackers:
            cid = entry["creature_id"]
            target = entry["target"]

            perm = self._find_permanent(gs, player, cid)

			# check if on battlefield
            if not perm:
                self.rejected_attackers.append({
                    "creature_id": cid, "reason": "not_on_battlefield",
                })
                continue

			# check if tapped
            if perm.tapped:
                print(f"[Combat] Rejected {cid}: Already tapped!")
                self.rejected_attackers.append({
                    "creature_id": cid, "reason": "tapped",
                })
                continue

			# check summoning sickness
            if getattr(perm, "summoning_sick", False):
                print(f"[Combat] Rejected {cid}: Summoning sickness!")
                self.rejected_attackers.append({
                    "creature_id": cid, "reason": "summoning_sickness",
                })
                continue

			# check defender keyword
            if any(
                a.get("type") == "keyword" and a.get("name") == "defender"
                for a in getattr(perm, "abilities", [])
            ):
                print(f"[Combat] Rejected {cid}: Defender cannot attack!")
                self.rejected_attackers.append({
                    "creature_id": cid, "reason": "defender",
                })
                continue

			# check if actually a creature with power
            if not hasattr(perm, 'power') or perm.power is None:
                print(f"[Combat] Rejected {cid}: Not a creature!")
                self.rejected_attackers.append({
                    "creature_id": cid, "reason": "not_a_creature",
                })
                continue

            # safe to add attacker now
            self.attackers[cid] = target
            self._attacking_creatures.add(cid)

            # tap creature if it does not have vigilance
            has_vigilance = any(
                a.get("type") == "keyword" and a.get("name") == "vigilance"
                for a in getattr(perm, "abilities", [])
            )
            
            if not has_vigilance:
                perm.tapped = True
                changes.append({"change_type": "TAP", "target": cid})

        return changes

    def set_blockers(self, gs: GameState, player: str, blockers: list[dict[str, str]]) -> list[dict[str, Any]]:
        # record blockers
		# blockers do not tap
        self.blockers.clear()
        for entry in blockers:
            cid = entry["creature_id"]
            blocking = entry["blocking_id"]
            self.blockers[cid] = blocking
        return []

	# set damage order for multi blocked attackers
    def set_damage_order(self, attacker_id: str, blocker_order: list[str]) -> None:
        
        self.damage_order[attacker_id] = list(blocker_order)

	# check if any attacker or blocker has first strike or double strike
    def has_first_strike_participants(self, gs: GameState) -> bool:
        for cid in list(self.attackers) + list(self.blockers):
            perm = self._find_any_permanent(gs, cid)
            if perm is not None and self._deals_first_strike(perm):
                return True
        return False
		
	# compute first strike damage step
    def compute_first_strike_damage(self, gs: GameState) -> dict[str, Any]:
        
        return self._compute_damage(gs, first_strike_only=True)

	# set damage order for multi blocked attackers
    def compute_combat_damage(self, gs: GameState) -> dict[str, Any]:
        return self._compute_damage(gs, first_strike_only=False)

	# core damage logic for both first strike and normal damage
    def _compute_damage(self, gs: GameState, first_strike_only: bool) -> dict[str, Any]:
        damage_events: list[dict[str, Any]] = []
        creatures_died: list[str] = []

        # find defending player id
        ap_id = gs.active_player or ""
        def_id = ""
        for pid in gs.player_ids:
            if pid != ap_id:
                def_id = pid
                break

        # attackers deal damage
        for cid, target in self.attackers.items():
            perm = self._find_permanent(gs, ap_id, cid)
            if perm is None:
                continue

			# check first strike timing rules
            if first_strike_only:
                if not self._deals_first_strike(perm):
                    continue
			# check normal step timing rules
            else:
                if self._has_first_strike(perm) and not self._has_double_strike(perm):
                    continue

            power = perm.power

            blockers_for_this = [
                b_id for b_id, a_id in self.blockers.items() if a_id == cid
            ]

            if not blockers_for_this:
                # damage to player if unblocked
                damage_events.append({
                    "source": cid,
                    "target": target,
                    "amount": power,
                })
            else:
                # damage to blockers in order if blocked
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

                    # check if blocker dies
                    if blocker_perm.damage >= blocker_perm.toughness:
                        creatures_died.append(blocker_id)

		# blockers deal damage to attackers
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

                # check if attacker dies
                if a_perm.damage >= a_perm.toughness:
                    creatures_died.append(a_id)

        # update life totals
        new_life = dict(gs.life_totals)
        for event in damage_events:
            target = event["target"]
            # Damage to a player reduces their life.
            if target in gs.life_totals:
                new_life[target] -= event["amount"]
                
        gs.life_totals = new_life

        # move dead creatures to graveyard
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

    # helper to find permanent on player battlefield
    def _find_permanent(
        self, gs: GameState, player: str, permanent_id: str
    ) -> Permanent | None:
        for perm in gs.battlefield.get(player, []):
            if perm.id == permanent_id:
                return perm
        return None

	# helper to find permanent on any battlefield
    @staticmethod
    def _find_any_permanent(gs: GameState, permanent_id: str) -> Permanent | None:
        for perms in gs.battlefield.values():
            for perm in perms:
                if perm.id == permanent_id:
                    return perm
        return None

	# check if creature deals first strike or double strike damage
    @staticmethod
    def _deals_first_strike(perm: Permanent) -> bool:
        return (
            CombatManager._has_first_strike(perm)
            or CombatManager._has_double_strike(perm)
        )

	# check first strike keyword
    @staticmethod
    def _has_first_strike(perm: Permanent) -> bool:
        return any(
            a.get("type") == "keyword" and a.get("name") == "first_strike"
            for a in getattr(perm, "abilities", [])
        )

	# check double strike keyword
    @staticmethod
    def _has_double_strike(perm: Permanent) -> bool:
        return any(
            a.get("type") == "keyword" and a.get("name") == "double_strike"
            for a in getattr(perm, "abilities", [])
        )

	# check vigilance keyword
    @staticmethod
    def _has_vigilance(perm: Permanent) -> bool:
        return any(
            a.get("type") == "keyword" and a.get("name") == "vigilance"
            for a in getattr(perm, "abilities", [])
        )
