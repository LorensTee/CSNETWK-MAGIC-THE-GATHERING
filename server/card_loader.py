from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CardDef:
    """definition of a unique card type"""
    card_id_base: str
    name: str
    card_type: str
    subtype: str
    color: str
    cmc: int
    mana_cost: dict[str, int]
    power: int | None
    toughness: int | None
    copies_in_set: int
    simplified_effect: str
    abilities: list[dict[str, Any]] = field(default_factory=list)

class CardLoader:
    """loads and validates the fixed card catalog"""

    def __init__(self) -> None:
        self.card_defs: dict[str, CardDef] = {}
        self.instance_ids: set[str] = set()
        self.instance_to_base: dict[str, str] = {}
        self._loaded = False

    def load(
        self,
        master_path: str | None = None,
        instances_path: str | None = None,
    ) -> None:
        """parse the 2 CSV files and build internal indexes"""
        base = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(os.path.dirname(base), "data")

        master_path = master_path or os.path.join(data_dir, "mtgnp_master_card_list.csv")
        instances_path = instances_path or os.path.join(data_dir, "mtgnp_card_instances.csv")

        self._load_master(master_path)
        self._load_instances(instances_path)
        self._loaded = True

    def _load_master(self, path: str) -> None:
        """parse the master card list CSV"""
        with open(path, newline="", encoding="utf-8") as f:
            #skip  title row
            next(f)
            reader = csv.DictReader(f)

            for row in reader:
                card_id = row["Card ID Base"].strip()
                if not card_id:
                    continue

                mana_cost: dict[str, int] = {}
                for colour_key in ("W", "U", "B", "R", "G"):
                    val = row.get(colour_key, "0").strip()
                    if val and int(val) > 0:
                        mana_cost[colour_key] = int(val)
                generic_val = row.get("Generic", "0").strip()
                if generic_val and int(generic_val) > 0:
                    mana_cost["X"] = int(generic_val)

                power_str = row.get("Power", "").strip()
                toughness_str = row.get("Toughness", "").strip()
                power = int(power_str) if power_str and power_str != "-" else None
                toughness = int(toughness_str) if toughness_str and toughness_str != "-" else None

                copies = int(row.get("Copies in Set", "0").strip() or "0")

                card_def = CardDef(
                    card_id_base=card_id,
                    name=row.get("Card Name", "").strip(),
                    card_type=row.get("Card Type", "").strip(),
                    subtype=row.get("Subtype", "").strip(),
                    color=row.get("Color", "").strip(),
                    cmc=int(row.get("CMC", "0").strip() or "0"),
                    mana_cost=mana_cost,
                    power=power,
                    toughness=toughness,
                    copies_in_set=copies,
                    simplified_effect=row.get("Simplified Effect", "").strip(),
                    abilities=self._parse_abilities(row),
                )
                self.card_defs[card_id] = card_def

    def _load_instances(self, path: str) -> None:
        """parse the card instances CSV to build legal ID set"""
        with open(path, newline="", encoding="utf-8") as f:
            # skip title
            next(f)
            reader = csv.DictReader(f)

            for row in reader:
                instance_id = row.get("card_id (protocol reference)", "").strip()
                if not instance_id:
                    continue
                self.instance_ids.add(instance_id)

                base_id = instance_id.rsplit("_", 1)[0] if "_" in instance_id else instance_id
                self.instance_to_base[instance_id] = base_id

    def is_legal_deck(self, deck_list: list[str]) -> tuple[bool, str]:
        """validate deck list against legal card set"""
        if not deck_list:
            return False, "Deck is empty."
        if len(deck_list) > 50:
            return False, f"Deck contains {len(deck_list)} cards; maximum is 50."

        for card_id in deck_list:
            if card_id not in self.instance_ids:
                return False, f"Card '{card_id}' is not in the legal card set."

        return True, ""

    def get_card(self, card_id: str) -> CardDef | None:
        """Look up CardDef by instance or base ID"""
        if card_id in self.card_defs:
            return self.card_defs[card_id]
        # try as instance ID
        base = self.instance_to_base.get(card_id)
        if base and base in self.card_defs:
            return self.card_defs[base]
        return None

    def instance_to_base_id(self, instance_id: str) -> str | None:
        """return the base card ID for an instance ID or None"""
        return self.instance_to_base.get(instance_id)

    @staticmethod
    def _parse_abilities(csv_row: dict[str, str]) -> list[dict[str, Any]]:
        """parse ability keywords from a CSV row"""
        effect = csv_row.get("Simplified Effect", "").strip().lower()
        abilities: list[dict[str, Any]] = []

        if "haste" in effect:
            abilities.append({"type": "keyword", "name": "haste"})
        if "flying" in effect:
            abilities.append({"type": "keyword", "name": "flying"})
        if "first strike" in effect:
            abilities.append({"type": "keyword", "name": "first_strike"})
        if "trample" in effect:
            abilities.append({"type": "keyword", "name": "trample"})
        if "defender" in effect:
            abilities.append({"type": "keyword", "name": "defender"})
        if "vigilance" in effect:
            abilities.append({"type": "keyword", "name": "vigilance"})
        if "hexproof" in effect:
            abilities.append({"type": "keyword", "name": "hexproof"})
        if "protection from" in effect:
            abilities.append({"type": "keyword", "name": "protection"})

        #tap abilities
        if "tap:" in effect or "tap:" in effect:

            produces = {}
            name = csv_row.get("Card Name", "").strip().lower()

            if "add r" in effect or "add {r}" in effect or name == "mountain":
                produces["R"] = 1
            elif "add g" in effect or "add {g}" in effect or name == "forest":
                produces["G"] = 1
            elif "add u" in effect or "add {u}" in effect or name == "island":
                produces["U"] = 1
            elif "add w" in effect or "add {w}" in effect or name == "plains":
                produces["W"] = 1
            elif "add b" in effect or "add {b}" in effect or name == "swamp":
                produces["B"] = 1
            elif "add 1" in effect or "add {c}" in effect:
                produces["C"] = 1

            abilities.append({
                "type": "activated", 
                "name": "tap", 
                "requires_tap": True,
                "produces": produces
            })

        return abilities
