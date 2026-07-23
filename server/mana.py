"""
server/mana.py — Mana System (Module 02: Server Engine)

Represents mana pools and provides validation/deduction helpers for spell
and ability costs.  MTGNP 1.0 uses five colours (W, U, B, R, G) plus
colourless (C).  Generic mana in costs is represented by the key ``'X'``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


# ── Colour constants ────────────────────────────────────────────────────────

COLOUR_KEYS = ("W", "U", "B", "R", "G", "C")
"""All mana colour keys in canonical order."""

GENERIC_KEY = "X"
"""Key used for generic (any-colour) mana in costs."""


@dataclass
class ManaPool:
    """Floating mana available to a player.

    Attributes default to 0.
    """

    W: int = 0  # White
    U: int = 0  # Blue
    B: int = 0  # Black
    R: int = 0  # Red
    G: int = 0  # Green
    C: int = 0  # Colorless

    @classmethod
    def empty(cls) -> ManaPool:
        """Return a zeroed mana pool."""
        return cls()

    def is_empty(self) -> bool:
        """Return ``True`` if every colour is zero."""
        return all(getattr(self, k) == 0 for k in COLOUR_KEYS)

    def to_dict(self) -> dict[str, int]:
        """Return a JSON-safe dict of non-zero colours."""
        return {k: getattr(self, k) for k in COLOUR_KEYS if getattr(self, k) > 0}

    def __add__(self, other: ManaPool) -> ManaPool:
        """Combine two mana pools."""
        return ManaPool(
            W=self.W + other.W,
            U=self.U + other.U,
            B=self.B + other.B,
            R=self.R + other.R,
            G=self.G + other.G,
            C=self.C + other.C,
        )

    def __sub__(self, other: ManaPool) -> ManaPool:
        """Subtract *other* from this pool (may produce negatives)."""
        return ManaPool(
            W=self.W - other.W,
            U=self.U - other.U,
            B=self.B - other.B,
            R=self.R - other.R,
            G=self.G - other.G,
            C=self.C - other.C,
        )


# ── Cost representation ──────────────────────────────────────────────────────


@dataclass
class ManaCost:
    """The mana cost of a spell or ability.

    *colours* holds specific colour pips and *generic* holds the generic
    (any-colour) portion.
    """

    colours: dict[str, int]  # e.g. {"R": 1, "U": 2}
    generic: int = 0


def parse_cost(cost_dict: dict[str, int]) -> ManaCost:
    """Convert a ``mana_cost`` dict (from ``CardDef``) to a ``ManaCost``.

    Generic mana is stored under the key ``'X'``; colour pips use their
    single-letter keys.
    """
    colours = {}
    generic = 0
    for key, val in cost_dict.items():
        if key == GENERIC_KEY:
            generic = val
        elif key in COLOUR_KEYS:
            colours[key] = val
    return ManaCost(colours=colours, generic=generic)


# ── Validation & deduction ──────────────────────────────────────────────────


def can_pay(
    mana_payment: dict[str, int],
    mana_cost: dict[str, int],
    mana_pool: ManaPool,
) -> bool:
    """Return ``True`` if the player can afford the cost.

    *mana_payment* is what the player claims they're paying, and *mana_cost*
    is the CardDef's cost (used to verify the payment is sufficient).
    *mana_pool* is the pool of floating mana the player has available.

    The function checks:
    1. The payment covers every colour pip in the cost.
    2. The player has enough floating mana of each colour to cover the payment.
    3. Generic mana in the cost can come from any colour; we check total
       coloured + colourless mana after colour-specific pips are paid.

    Note: This is a simplified check.  A full implementation would treat
    colourless (C) vs. coloured differently, but MTGNP 1.0's card set is
    simple enough that this works.
    """
    cost = parse_cost(mana_cost)

    # 1. Check colour-specific pips.
    colour_total_needed = 0
    for colour, needed in cost.colours.items():
        if needed <= 0:
            continue
        offered = mana_payment.get(colour, 0)
        if offered < needed:
            return False
        pool_available = getattr(mana_pool, colour, 0)
        if pool_available < offered:
            return False
        colour_total_needed += needed

    # 2. Generic mana: can come from any colour or colourless.
    payment_total = sum(mana_payment.values())
    payment_colourless = mana_payment.get("C", 0)
    # Coloured payment after covering colour pips
    coloured_after_pips = sum(
        mana_payment.get(c, 0) - cost.colours.get(c, 0)
        for c in mana_payment
        if c in COLOUR_KEYS
    )
    # Generic can come from the leftover coloured mana or colourless mana
    available_for_generic = coloured_after_pips + payment_colourless
    if available_for_generic < cost.generic:
        return False

    return True


def deduct_mana(payment: dict[str, int], pool: ManaPool) -> ManaPool:
    """Deduct the *payment* from *pool* and return the new pool.

    Does NOT mutate the original pool.  Raises ``ValueError`` if the pool
    doesn't have enough mana.
    """
    new_pool = ManaPool(
        W=pool.W, U=pool.U, B=pool.B,
        R=pool.R, G=pool.G, C=pool.C,
    )
    for colour, amount in payment.items():
        if amount <= 0:
            continue
        current = getattr(new_pool, colour, 0)
        if current < amount:
            raise ValueError(
                f"Not enough {colour} mana: have {current}, need {amount}"
            )
        setattr(new_pool, colour, current - amount)
    return new_pool


def format_mana_pool(pool: ManaPool) -> str:
    """Return a compact string representation, e.g. ``'{R}{R}{B}'``."""
    parts: list[str] = []
    for colour in COLOUR_KEYS:
        count = getattr(pool, colour, 0)
        parts.extend(f"{{{colour}}}" for _ in range(count))
    return "".join(parts) if parts else "{0}"


def empty_pool_dict() -> dict[str, int]:
    """Return an empty mana-pool dict (all zeroes)."""
    return {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
