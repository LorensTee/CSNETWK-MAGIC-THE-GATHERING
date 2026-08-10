from __future__ import annotations

from dataclasses import dataclass
from typing import Any

COLOUR_KEYS = ("W", "U", "B", "R", "G", "C")

GENERIC_KEY = "X"


@dataclass
class ManaPool:
    W: int = 0  # white
    U: int = 0  # blue
    B: int = 0  # black
    R: int = 0  # red
    G: int = 0  # green
    C: int = 0  # colorless

    @classmethod
    def empty(cls) -> ManaPool:
        #return pool with all zeroes
        return cls()

    def is_empty(self) -> bool:
        #return true if all mana counts are zero
        return all(getattr(self, k) == 0 for k in COLOUR_KEYS)

    def to_dict(self) -> dict[str, int]:
        #return a dict of the mana pool with only non-zero values
        return {k: getattr(self, k) for k in COLOUR_KEYS if getattr(self, k) > 0}

    def __add__(self, other: ManaPool) -> ManaPool:
        #combine 2 mana pools
        return ManaPool(
            W=self.W + other.W,
            U=self.U + other.U,
            B=self.B + other.B,
            R=self.R + other.R,
            G=self.G + other.G,
            C=self.C + other.C,
        )

    def __sub__(self, other: ManaPool) -> ManaPool:
        #subtract a mana pool from another mana pool
        return ManaPool(
            W=self.W - other.W,
            U=self.U - other.U,
            B=self.B - other.B,
            R=self.R - other.R,
            G=self.G - other.G,
            C=self.C - other.C,
        )

@dataclass
class ManaCost: #mana cost of a card, with generic and colored mana
    colours: dict[str, int]
    generic: int = 0


def parse_cost(cost_dict: dict[str, int]) -> ManaCost:
    #convert a raw cost dict into a ManaCost
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
    cost = parse_cost(mana_cost)
    pool_copy = {c: getattr(mana_pool, c) for c in COLOUR_KEYS}

    if "X" in mana_payment:
        # check if the pool can cover the required coloured mana
        for colour, needed in cost.colours.items():
            if pool_copy[colour] < needed:
                return False
            pool_copy[colour] -= needed
            
        
        leftover_floating = sum(pool_copy.values())
        return leftover_floating >= cost.generic

    # explicit payments are a precise mana spend
    else:
        #make sure the pool actually contains the mana
        for colour, amount in mana_payment.items():
            if colour not in pool_copy or pool_copy[colour] < amount:
                return False
                
        # ensure the payment meets the coloured mana requirements
        for colour, needed in cost.colours.items():
            if mana_payment.get(colour, 0) < needed:
                return False
                
        # check whether the total payment covers coloured and generic costs
        total_paid = sum(mana_payment.values())
        total_needed = cost.generic + sum(cost.colours.values())
        return total_paid >= total_needed


def deduct_mana(payment: dict[str, int], pool: ManaPool) -> ManaPool:
    new_pool = ManaPool(
        W=pool.W, U=pool.U, B=pool.B,
        R=pool.R, G=pool.G, C=pool.C,
    )

    if "X" in payment:
        # drain the coloured mana first
        for colour, amount in payment.items():
            if colour == "X" or amount <= 0:
                continue
            current = getattr(new_pool, colour, 0)
            if current < amount:
                raise ValueError(f"Not enough {colour} mana")
            setattr(new_pool, colour, current - amount)
            
        # then use any remaining mana to cover the X
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
        # deduction for explicit mana payments
        for colour, amount in payment.items():
            if amount <= 0: 
                continue
            current = getattr(new_pool, colour, 0)
            if current < amount:
                raise ValueError(f"Not enough {colour} mana")
            setattr(new_pool, colour, current - amount)
            
    return new_pool


def format_mana_pool(pool: ManaPool) -> str:
    #Return a string representation
    parts: list[str] = []
    for colour in COLOUR_KEYS:
        count = getattr(pool, colour, 0)
        parts.extend(f"{{{colour}}}" for _ in range(count))
    return "".join(parts) if parts else "{0}"


def empty_pool_dict() -> dict[str, int]:
    # return an empty mana pool dict
    return {"W": 0, "U": 0, "B": 0, "R": 0, "G": 0, "C": 0}
