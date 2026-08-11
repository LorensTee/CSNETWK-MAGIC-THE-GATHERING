from __future__ import annotations

from dataclasses import dataclass
from typing import Any


COLOUR_KEYS = ("W", "U", "B", "R", "G", "C")

GENERIC_KEY = "X"

@dataclass
class ManaPool:
    """floating mana available to player"""

    W: int = 0  # white
    U: int = 0  # blue
    B: int = 0  # black
    R: int = 0  # red
    G: int = 0  # green
    C: int = 0  # colorless

    @classmethod
    def empty(cls) -> ManaPool:
        """return zeroed mana pool"""
        return cls()

    def is_empty(self) -> bool:
        """return true if every colour is zero"""
        return all(getattr(self, k) == 0 for k in COLOUR_KEYS)

    def to_dict(self) -> dict[str, int]:
        """return dict of nonzero colours"""
        return {k: getattr(self, k) for k in COLOUR_KEYS if getattr(self, k) > 0}

    def __add__(self, other: ManaPool) -> ManaPool:
        """combine two mana pools"""
        return ManaPool(
            W=self.W + other.W,
            U=self.U + other.U,
            B=self.B + other.B,
            R=self.R + other.R,
            G=self.G + other.G,
            C=self.C + other.C,
        )

    def __sub__(self, other: ManaPool) -> ManaPool:
        """subtract other mana pool from this pool"""
        return ManaPool(
            W=self.W - other.W,
            U=self.U - other.U,
            B=self.B - other.B,
            R=self.R - other.R,
            G=self.G - other.G,
            C=self.C - other.C,
        )

@dataclass
class ManaCost:
    """mana cost of a spell or ability"""
    colours: dict[str, int]
    generic: int = 0


def parse_cost(cost_dict: dict[str, int]) -> ManaCost:
    """convert mana_cost dict ManaCost"""
    colours = {}
    generic = 0
    for key, val in cost_dict.items():
        if key == GENERIC_KEY:
            generic = val
        elif key in COLOUR_KEYS:
            colours[key] = val
    return ManaCost(colours=colours, generic=generic)

def can_pay(
    mana_payment: dict[str, int],
    mana_cost: dict[str, int],
    mana_pool: ManaPool,
) -> bool:
    """return true if the player can afford"""
    cost = parse_cost(mana_cost)
    pool_copy = {c: getattr(mana_pool, c) for c in COLOUR_KEYS}

    #if client sent X, they are asking the server to autoassign floating mana to cover the cost
    if "X" in mana_payment:
        # make sure they have the required specific colored pips
        for colour, needed in cost.colours.items():
            if pool_copy[colour] < needed:
                return False
            pool_copy[colour] -= needed
            
        # make sure the remaining floating mana can cover the cost
        leftover_floating = sum(pool_copy.values())
        return leftover_floating >= cost.generic

    # if explicit payments
    else:
        # check if pool actually has the mana
        for colour, amount in mana_payment.items():
            if colour not in pool_copy or pool_copy[colour] < amount:
                return False
                
        # if the payment cover the specific pips
        for colour, needed in cost.colours.items():
            if mana_payment.get(colour, 0) < needed:
                return False
                
        #is the total payment enough to cover the total cost
        total_paid = sum(mana_payment.values())
        total_needed = cost.generic + sum(cost.colours.values())
        return total_paid >= total_needed


def deduct_mana(payment: dict[str, int], pool: ManaPool) -> ManaPool:
    """deduct payment fr pool and return the new pool"""
    new_pool = ManaPool(
        W=pool.W, U=pool.U, B=pool.B,
        R=pool.R, G=pool.G, C=pool.C,
    )

    if "X" in payment:
        # Auto-deducts
        #pay the specific colors required
        for colour, amount in payment.items():
            if colour == "X" or amount <= 0:
                continue
            current = getattr(new_pool, colour, 0)
            if current < amount:
                raise ValueError(f"Not enough {colour} mana")
            setattr(new_pool, colour, current - amount)
            
        #drain remaining floating mana to cover X
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
        #standard deduction for explicit payments
        for colour, amount in payment.items():
            if amount <= 0: 
                continue
            current = getattr(new_pool, colour, 0)
            if current < amount:
                raise ValueError(f"Not enough {colour} mana")
            setattr(new_pool, colour, current - amount)
            
    return new_pool


def format_mana_pool(pool: ManaPool) -> str:
    """return string representation"""
    parts: list[str] = []
    for colour in COLOUR_KEYS:
        count = getattr(pool, colour, 0)
        parts.extend(f"{{{colour}}}" for _ in range(count))
    return "".join(parts) if parts else "{0}"


def empty_pool_dict() -> dict[str, int]:
    """return empty mana pool dict"""
    return {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
