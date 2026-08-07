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
    pool_copy = {c: getattr(mana_pool, c) for c in COLOUR_KEYS}

    # If the client sent "X" in the payment, they are asking the server to 
    # auto-assign floating mana to cover the generic cost (Lazy Payment).
    if "X" in mana_payment:
        # 1. Verify they have the required specific colored pips
        for colour, needed in cost.colours.items():
            if pool_copy[colour] < needed:
                return False
            pool_copy[colour] -= needed
            
        # 2. Verify the remaining floating mana can cover the generic cost
        leftover_floating = sum(pool_copy.values())
        return leftover_floating >= cost.generic

    # For Explicit Payments (Client says exactly which mana they are spending, e.g. G:1, R:1)
    else:
        # 1. Security Check: Does the pool ACTUALLY have the mana they claim to pay?
        for colour, amount in mana_payment.items():
            if colour not in pool_copy or pool_copy[colour] < amount:
                return False
                
        # 2. Does the payment cover the specific pips?
        for colour, needed in cost.colours.items():
            if mana_payment.get(colour, 0) < needed:
                return False
                
        # 3. Is the total payment enough to cover the total cost?
        total_paid = sum(mana_payment.values())
        total_needed = cost.generic + sum(cost.colours.values())
        return total_paid >= total_needed


def deduct_mana(payment: dict[str, int], pool: ManaPool) -> ManaPool:
    """Deduct the *payment* from *pool* and return the new pool.

    Does NOT mutate the original pool.  Raises ``ValueError`` if the pool
    doesn't have enough mana.
    """
    new_pool = ManaPool(
        W=pool.W, U=pool.U, B=pool.B,
        R=pool.R, G=pool.G, C=pool.C,
    )

    if "X" in payment:
        # Auto-deduct for lazy clients
        # First, pay the specific colors required
        for colour, amount in payment.items():
            if colour == "X" or amount <= 0:
                continue
            current = getattr(new_pool, colour, 0)
            if current < amount:
                raise ValueError(f"Not enough {colour} mana")
            setattr(new_pool, colour, current - amount)
            
        # Second, drain remaining floating mana to cover X
        x_needed = payment["X"]
        for colour in COLOUR_KEYS:
            if x_needed <= 0:
                break
            current = getattr(new_pool, colour, 0)
            if current > 0:
                drain = min(current, x_needed)
                setattr(new_pool, colour, current - drain)
                x_needed -= drain
                
        if x_needed > 0:
            raise ValueError("Not enough total mana to cover generic cost")
            
    else:
        # Standard deduction for explicit payments
        for colour, amount in payment.items():
            if amount <= 0: 
                continue
            current = getattr(new_pool, colour, 0)
            if current < amount:
                raise ValueError(f"Not enough {colour} mana")
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
