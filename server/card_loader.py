"""
server/card_loader.py — Card Data Loading (Module 02: Server Engine)

Loads and validates the static card catalog from CSV files.  The server uses
this module to:

* Parse ``data/mtgnp_master_card_list.csv`` into ``CardDef`` objects.
* Build a set of all legal instance IDs from ``data/mtgnp_card_instances.csv``.
* Validate player deck lists against the legal card set.
"""

from __future__ import annotations

import csv
import os
from dataclasses import dataclass, field
from typing import Any


@dataclass
class CardDef:
    """Definition of a single unique card type.

    All 58 card types in the fixed set produce one ``CardDef`` each.
    """

    card_id_base: str
    """Base identifier without the copy suffix, e.g. ``'lightning_bolt'``."""

    name: str
    """Human-readable name, e.g. ``'Lightning Bolt'``."""

    card_type: str
    """Card type string: ``'Land'``, ``'Creature'``, ``'Instant'``,
    ``'Sorcery'``, ``'Enchantment'``, ``'Artifact'``, ``'Artifact Creature'``."""

    subtype: str
    """Subtype string or empty string."""

    color: str
    """Colour: ``'W'``, ``'U'``, ``'B'``, ``'R'``, ``'G'``, or ``'C'``
    (colourless)."""

    cmc: int
    """Converted mana cost."""

    mana_cost: dict[str, int]
    """Colour pip cost as ``{colour: pips}``, e.g. ``{'R': 1, 'X': 1}``.
    Generic mana is stored under key ``'X'``."""

    power: int | None
    """Power (non-``None`` only for creatures)."""

    toughness: int | None
    """Toughness (non-``None`` only for creatures)."""

    copies_in_set: int
    """How many copies of this card exist in the fixed set (usually 4 or 20)."""

    simplified_effect: str
    """Human-readable effect text from the CSV."""

    abilities: list[dict[str, Any]] = field(default_factory=list)
    """Parsed ability descriptors for game logic (populated during load)."""


class CardLoader:
    """Loads and validates the fixed MTGNP card catalog.

    Usage::

        loader = CardLoader()
        loader.load("data/mtgnp_master_card_list.csv", "data/mtgnp_card_instances.csv")
        ok, msg = loader.is_legal_deck(["lightning_bolt_001", "mountain_001", ...])
    """

    def __init__(self) -> None:
        # card_id_base → CardDef
        self.card_defs: dict[str, CardDef] = {}

        # Set of all valid instance IDs (e.g. "lightning_bolt_001")
        self.instance_ids: set[str] = set()

        # instance_id → card_id_base
        self.instance_to_base: dict[str, str] = {}

        self._loaded = False

    def load(
        self,
        master_path: str | None = None,
        instances_path: str | None = None,
    ) -> None:
        """Parse the two CSV files and build internal indexes.

        If *master_path* or *instances_path* is ``None``, defaults to
        ``data/mtgnp_master_card_list.csv`` and
        ``data/mtgnp_card_instances.csv`` relative to this file's location
        (or the current working directory).
        """
        base = os.path.dirname(os.path.abspath(__file__))
        data_dir = os.path.join(os.path.dirname(base), "data")

        master_path = master_path or os.path.join(data_dir, "mtgnp_master_card_list.csv")
        instances_path = instances_path or os.path.join(data_dir, "mtgnp_card_instances.csv")

        self._load_master(master_path)
        self._load_instances(instances_path)
        self._loaded = True

    def _load_master(self, path: str) -> None:
        """Parse the master card list CSV into *CardDef* objects."""
        with open(path, newline="", encoding="utf-8") as f:
            # Skip the title row (row 1).
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
        """Parse the card instances CSV to build the legal-ID set."""
        with open(path, newline="", encoding="utf-8") as f:
            # Skip the title row (row 1).
            next(f)
            reader = csv.DictReader(f)

            for row in reader:
                instance_id = row.get("card_id (protocol reference)", "").strip()
                if not instance_id:
                    continue
                self.instance_ids.add(instance_id)

                # Derive the base ID by stripping the _NNN suffix.
                # e.g. "lightning_bolt_001" → "lightning_bolt"
                base_id = instance_id.rsplit("_", 1)[0] if "_" in instance_id else instance_id
                # Handle double-underscore cases like mtgnp_master_card_list → ...
                # Actually, the pattern is always "<base>_###" — the base itself
                # uses underscores.  So "goblin_guide_001" → rsplit gives "goblin_guide".
                self.instance_to_base[instance_id] = base_id

    def is_legal_deck(self, deck_list: list[str]) -> tuple[bool, str]:
        """Validate a deck list against the legal card set.

        Returns
        -------
        ``(True, "")`` on success, or ``(False, "error description")`` on failure.
        """
        if not deck_list:
            return False, "Deck is empty."
        if len(deck_list) > 50:
            return False, f"Deck contains {len(deck_list)} cards; maximum is 50."

        for card_id in deck_list:
            if card_id not in self.instance_ids:
                return False, f"Card '{card_id}' is not in the legal card set."

        return True, ""

    def get_card(self, card_id: str) -> CardDef | None:
        """Look up a ``CardDef`` by instance ID or base ID.

        If *card_id* is an instance ID (e.g. ``'lightning_bolt_001'``), the
        base ID is extracted automatically.  Returns ``None`` if not found.
        """
        if card_id in self.card_defs:
            return self.card_defs[card_id]
        # Try as instance ID.
        base = self.instance_to_base.get(card_id)
        if base and base in self.card_defs:
            return self.card_defs[base]
        return None

    def instance_to_base_id(self, instance_id: str) -> str | None:
        """Return the base card ID for an instance ID, or ``None``."""
        return self.instance_to_base.get(instance_id)

    @staticmethod
    def _parse_abilities(csv_row: dict[str, str]) -> list[dict[str, Any]]:
        """Parse ability keywords from a CSV row.

        Currently parses keyword abilities from the Simplified Effect column.
        This is a stub that can be extended for full ability resolution.
        """
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

        # Tap abilities
        if "tap:" in effect:

            produces = {}
            # Count coloured/colourless mana symbols, e.g. "{c}{c}" → C:2.
            for symbol, color in (
                ("{r}", "R"), ("{g}", "G"), ("{u}", "U"),
                ("{w}", "W"), ("{b}", "B"), ("{c}", "C"),
            ):
                count = effect.count(symbol)
                if count:
                    produces[color] = count

            # Fallback for plain-text effects without mana symbols.
            if not produces:
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
