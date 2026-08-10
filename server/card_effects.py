"""
server/card_effects.py — Card Effect Resolution (Module 02: Server Engine)

Dispatches card effects when a spell or ability resolves on the stack.
Every card in the fixed set (58 unique types) has an entry in
``EFFECT_HANDLERS``, keyed by ``card_id_base``.

Simple vanilla creatures just produce a ``PERMANENT_ENTERS`` state change.
Cards with real effects (damage, counter, pump, mana, destroy) have dedicated
implementations per CONTRIBUTING.md Rule 11.
"""

from __future__ import annotations

from typing import Any, Callable

from server.game_state import GameState, Permanent
from server.mana import ManaPool

# Type alias for effect handlers 

# Signature: (gs, controller, targets, card_def_abilities, extra) -> state_changes
EffectHandler = Callable[
    [GameState, str, list[str], list[dict[str, Any]], dict[str, Any]],
    list[dict[str, Any]],
]

# State-change helper factories 


# damage
def _damage(target: str, amount: int) -> dict[str, Any]:
    return {"change_type": "DAMAGE", "target": target, "amount": amount}


# life gain
def _life_gain(target: str, amount: int) -> dict[str, Any]:
    return {"change_type": "LIFE_GAIN", "target": target, "amount": amount}


# life loss
def _life_loss(target: str, amount: int) -> dict[str, Any]:
    return {"change_type": "LIFE_LOSS", "target": target, "amount": amount}


# destroy
def _destroy(target: str) -> dict[str, Any]:
    return {"change_type": "DESTROY", "target": target}


# permanent enters
def _permanent_enters(card_id: str, controller: str, tapped: bool = False) -> dict[str, Any]:
    return {
        "change_type": "PERMANENT_ENTERS",
        "card_id": card_id,
        "controller": controller,
        "tapped": tapped,
    }


# counter
def _counter(spell_stack_id: str) -> dict[str, Any]:
    return {"change_type": "COUNTER", "target": spell_stack_id}


# pump
def _pump(target_id: str, power: int, toughness: int, duration: str = "EOT") -> dict[str, Any]:
    return {
        "change_type": "PUMP",
        "target": target_id,
        "power": power,
        "toughness": toughness,
        "duration": duration,  # "EOT" = until end of turn
    }


# return to hand
def _return_to_hand(target: str) -> dict[str, Any]:
    return {"change_type": "RETURN_TO_HAND", "target": target}


# exile
def _exile(target: str) -> dict[str, Any]:
    return {"change_type": "EXILE", "target": target}


# draw
def _draw(player: str, count: int = 1) -> dict[str, Any]:
    return {"change_type": "DRAW", "player": player, "count": count}


# discard card
def _discard_card(player: str, card_ids: list[str]) -> dict[str, Any]:
    return {"change_type": "DISCARD", "player": player, "card_ids": card_ids}


# add mana
def _add_mana(controller: str, mana: dict[str, int]) -> dict[str, Any]:
    return {"change_type": "ADD_MANA", "player": controller, "mana": mana}



# Effect Helper Functions 

# apply damage
def _apply_damage(gs: GameState, target: str, damage_amount: int) -> None:
    if target in gs.life_totals:
        gs.life_totals[target] -= damage_amount
    else:
        for perms in gs.battlefield.values():
            for perm in perms:
                if perm.id == target:
                    perm.damage = getattr(perm, "damage", 0) + damage_amount
                    return

# apply counter
def _apply_counter(gs: GameState, target_stack_id: str) -> None:
    for i, item in enumerate(gs.stack):
        if item.stack_item_id == target_stack_id:
            # Rip it off the stack! 
            # (If your engine tracks graveyards, you could also append item.source to gs.graveyard here)
            gs.stack.pop(i)
            break

# apply bounce
def _apply_bounce(gs: GameState, target_id: str) -> None:
    for player_id, perms in gs.battlefield.items():
        for i, perm in enumerate(perms):
            if perm.id == target_id:
                # rip it off the battlefield!
                popped_perm = perms.pop(i)
                
                # figure out the base card ID (e.g., stripping the unique "_004" suffix)
                # so the hand gets the raw card back.
                card_id = getattr(popped_perm, "card_id", target_id.rsplit("_", 1)[0])
                
                # apend it back to the players hand
                if player_id not in gs.hands:
                    gs.hands[player_id] = []
                gs.hands[player_id].append(card_id)
                return

# apply draw
def _apply_draw(gs: GameState, player_id: str, amount: int = 1) -> None:
    for _ in range(amount):
        # Make sure they actually have a deck left!
        if player_id in gs.libraries and gs.libraries[player_id]:
            # The library TOP is index 0 (GameState convention), matching
            # the draw-step path in turn_engine._handle_draw.
            drawn_card = gs.libraries[player_id].pop(0)
            
            if player_id not in gs.hands:
                gs.hands[player_id] = []
            gs.hands[player_id].append(drawn_card)

# apply pump
def _apply_pump(gs: GameState, target_id: str, power_bonus: int, toughness_bonus: int) -> None:
    for perms in gs.battlefield.values():
        for perm in perms:
            if perm.id == target_id:
                perm.temp_power = getattr(perm, "temp_power", 0) + power_bonus
                perm.temp_toughness = getattr(perm, "temp_toughness", 0) + toughness_bonus
                return

# apply destroy
def _apply_destroy(gs: GameState, target_id: str) -> None:
    for player_id, perms in gs.battlefield.items():
        for i, perm in enumerate(perms):
            if perm.id == target_id:
                popped = perms.pop(i)
                card_id = getattr(popped, "card_id", target_id.rsplit("_", 1)[0])
                
                if not hasattr(gs, "graveyards"):
                    gs.graveyards = {}
                if player_id not in gs.graveyards:
                    gs.graveyards[player_id] = []
                    
                gs.graveyards[player_id].append(card_id)
                return

# apply add mana
def _apply_add_mana(gs: GameState, player_id: str, mana_dict: dict[str, int]) -> None:
    pool = gs.mana_pools.setdefault(player_id, ManaPool.empty())
    for color, amount in mana_dict.items():
        current = getattr(pool, color, 0)
        setattr(pool, color, current + amount)

# apply raise dead
def _apply_raise_dead(gs: GameState, player_id: str, target_card_id: str) -> None:
    gy = gs.graveyards.get(player_id, [])
    if target_card_id in gy:
        gy.remove(target_card_id)
        if player_id not in gs.hands:
            gs.hands[player_id] = []
        gs.hands[player_id].append(target_card_id)

# apply life change
def _apply_life_change(gs: GameState, player_id: str, amount: int) -> None:
    if player_id in gs.life_totals:
        gs.life_totals[player_id] += amount

# find permanent anywhere
def _find_permanent_anywhere(gs: GameState, target_id: str) -> Permanent | None:
    for perms in gs.battlefield.values():
        for perm in perms:
            if perm.id == target_id:
                return perm
    return None


# apply exile
def _apply_exile(gs: GameState, target_id: str) -> None:
    for player_id, perms in gs.battlefield.items():
        for i, perm in enumerate(perms):
            if perm.id == target_id:
                popped = perms.pop(i)
                # Preserve the INSTANCE id (consistent with graveyards).
                card_id = popped.id
                
                if not hasattr(gs, "exile"):
                    gs.exile = {}
                if player_id not in gs.exile:
                    gs.exile[player_id] = []
                    
                gs.exile[player_id].append(card_id)
                return

# apply enchant
def _apply_enchant(gs: GameState, target_id: str, aura_name: str) -> None:
    for perms in gs.battlefield.values():
        for perm in perms:
            if perm.id == target_id:
                # Add an auras list to the permanent if it doesn't have one
                if not hasattr(perm, "auras"):
                    perm.auras = []
                perm.auras.append(aura_name)
                return

# apply spawn permanent
def _apply_spawn_permanent(gs, controller: str, card_id: str, card_loader: Any = None, enters_tapped: bool = False) -> None:
    power, toughness = 0, 0
    haste = False
    def_id = card_id
    
    if card_loader:
        base_id = card_id.rsplit("_", 1)[0] if "_" in card_id else card_id
        cd = card_loader.get_card(base_id)
        if cd:
            power = cd.power if getattr(cd, 'power', None) is not None else 0
            toughness = cd.toughness if getattr(cd, 'toughness', None) is not None else 0
            def_id = getattr(cd, 'card_id_base', base_id)
          
            # check if abilities list contains Haste
            if any(ab.get("name") == "haste" for ab in cd.abilities):
                haste = True

    # construct the Permanent object
    from server.game_state import Permanent  # Adjust if Permanent is in a different file!
    perm = Permanent(
        id=card_id,
        card_def_id=def_id,
        controller=controller,
        tapped=enters_tapped,
        power=power,
        toughness=toughness,
        summoning_sick=not haste,  # Haste skips summoning sickness!
        abilities=list(cd.abilities) if cd else [],
    )
    
    # actually put it on the board
    gs.battlefield.setdefault(controller, []).append(perm)
    

# Effect handler implementations 

# resolve goblin guide trigger
def _effect_goblin_guide_trigger(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    opponent = next((pid for pid in gs.player_ids if pid != controller), None)
    if opponent is None:
        return []
    lib = gs.libraries.get(opponent, [])
    if not lib:
        return [{"change_type": "REVEAL", "player": opponent, "card_id": None}]
    revealed = lib.pop(0)
    changes = [
        {"change_type": "REVEAL", "player": opponent, "card_id": revealed},
    ]
    loader = extra.get("card_loader")
    cd = loader.get_card(revealed) if loader is not None else None
    is_land = cd is not None and "land" in (cd.card_type or "").lower()
    if is_land:
        gs.hands.setdefault(opponent, []).append(revealed)
        changes.append(
            {"change_type": "LAND_TO_HAND", "player": opponent, "card_id": revealed}
        )
    else:
        gs.graveyards.setdefault(opponent, []).append(revealed)
        changes.append(
            {"change_type": "REVEALED_TO_GRAVEYARD", "player": opponent,
             "card_id": revealed}
        )
    return changes


# resolve monastery swiftspear trigger
def _effect_monastery_swiftspear_trigger(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    source = extra.get("source_permanent") or extra.get("card_id", "")
    _apply_pump(gs, source, 1, 1)
    return [{"change_type": "PUMP", "target": source, "power": 1,
             "toughness": 1}]


# resolve lightning bolt
def _effect_lightning_bolt(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 3)
    return [_damage(targets[0], 3)]


# resolve shock
def _effect_shock(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 2)
    return [_damage(targets[0], 2)]

# resolve lava spike
def _effect_lava_spike(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 3)
    return [_damage(targets[0], 3)]


# resolve flame slash
def _effect_flame_slash(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 4)
    return [_damage(targets[0], 4)]


# resolve searing spear
def _effect_searing_spear(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 3)
    return [_damage(targets[0], 3)]


# resolve skullcrack
def _effect_skullcrack(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 3)
    return [_damage(targets[0], 3)]


# resolve rift bolt
def _effect_rift_bolt(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 3)
    return [_damage(targets[0], 3)]


# resolve incinerate
def _effect_incinerate(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_damage(gs, targets[0], 3)
    return [_damage(targets[0], 3)]

# resolve counterspell
def _effect_counterspell(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_counter(gs, targets[0])
    return [_counter(targets[0])]


# resolve cancel
def _effect_cancel(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_counter(gs, targets[0])
    return [_counter(targets[0])]


# resolve negate
def _effect_negate(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_counter(gs, targets[0])
    return [_counter(targets[0])]


# resolve mana leak
def _effect_mana_leak(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_counter(gs, targets[0])
    return [_counter(targets[0])]


# resolve unsummon
def _effect_unsummon(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_bounce(gs, targets[0])
    return [_return_to_hand(targets[0])]


# resolve ponder
def _effect_ponder(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    _apply_draw(gs, controller, 1)
    return [_draw(controller, 1)]


# resolve giant growth
def _effect_giant_growth(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_pump(gs, targets[0], 3, 3)
    return [_pump(targets[0], 3, 3)]


# resolve rampant growth
def _effect_rampant_growth(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    lib = gs.libraries.get(controller, [])
    for card_id in lib:
        card_lower = card_id.lower()
        if any(basic in card_lower for basic in ("plains_", "island_", "swamp_", "mountain_", "forest_")):
            lib.remove(card_id)
            # pass enters_tapped=True to our new helpr
            _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"), enters_tapped=True)
            return [_permanent_enters(card_id, controller, tapped=True)]
            
    # fallback if no basic found
    fallback_id = "forest_001"
    _apply_spawn_permanent(gs, controller, fallback_id, extra.get("card_loader"), enters_tapped=True)
    return [_permanent_enters(fallback_id, controller, tapped=True)]


# resolve naturalize
def _effect_naturalize(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_destroy(gs, targets[0])
    return [_destroy(targets[0])]


# resolve vines of vastwood
def _effect_vines_of_vastwood(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_pump(gs, targets[0], 4, 4)
    return [_pump(targets[0], 4, 4)]


# resolve dark ritual
def _effect_dark_ritual(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    _apply_add_mana(gs, controller, {"B": 3})
    return [_add_mana(controller, {"B": 3})]


# resolve terror
def _effect_terror(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_destroy(gs, targets[0])
    return [_destroy(targets[0])]


# resolve doom blade
def _effect_doom_blade(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_destroy(gs, targets[0])
    return [_destroy(targets[0])]


# resolve raise dead
def _effect_raise_dead(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_raise_dead(gs, controller, targets[0])
    return [{"change_type": "RETURN_FROM_GRAVEYARD", "target": targets[0]}]


# resolve mind rot
def _effect_mind_rot(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    target = targets[0]
    count = extra.get("count", 2)
    hand = gs.hands.get(target, [])
    changes = []
    # discard count cards from the targets hand, last-drawn first
    discarded = hand[-count:] if count > 0 else []
    for cid in discarded:
        hand.remove(cid)
        gs.graveyards.setdefault(target, []).append(cid)
        changes.append({
            "change_type": "DISCARD", "player": target, "card_id": cid,
        })
    return changes


# resolve gray merchant
def _effect_gray_merchant(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    devotion = 0
    card_loader = extra.get("card_loader")
    for perm in gs.battlefield.get(controller, []):
        if card_loader is not None:
            cd = card_loader.get_card(getattr(perm, "card_def_id", "") or "")
            if cd is not None and cd.color == "B":
                devotion += 1
        else:
            devotion += 1

    # the creature enters the battlefield
    spawn_id = extra.get("card_id") or controller
    _apply_spawn_permanent(gs, controller, spawn_id, card_loader)

# resolve mind rot
    opponent = next((pid for pid in gs.player_ids if pid != controller), None)
    if opponent is None:
        return []

    _apply_life_change(gs, opponent, -devotion)
    _apply_life_change(gs, controller, devotion)
    return [_life_loss(opponent, devotion), _life_gain(controller, devotion)]

# resolve gravedigger
def _effect_gravedigger(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []

    # the creature enters the battlefield
    spawn_id = extra.get("card_id") or controller
    _apply_spawn_permanent(gs, controller, spawn_id, extra.get("card_loader"))

    _apply_raise_dead(gs, controller, targets[0])
    return [{"change_type": "RETURN_FROM_GRAVEYARD", "target": targets[0]}]

# resolve healing salve
def _effect_healing_salve(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_life_change(gs, targets[0], 3)
    return [_life_gain(targets[0], 3)]


# resolve swords to plowshares
def _effect_swords_to_plowshares(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    # resolve controller/power BEFORE the exile removes the permanent
    perm = _find_permanent_anywhere(gs, targets[0])
    gain_player = perm.controller if perm is not None else controller
    amount = perm.power if perm is not None and perm.power is not None else 0
    _apply_exile(gs, targets[0])
    _apply_life_change(gs, gain_player, amount)
    return [_exile(targets[0]), _life_gain(gain_player, amount)]

# resolve path to exile
def _effect_path_to_exile(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_exile(gs, targets[0])
    return [_exile(targets[0])]

# resolve pacifism
def _effect_pacifism(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_enchant(gs, targets[0], "pacifism")
    return [{"change_type": "ENCHANT", "target": targets[0], "aura": "pacifism"}]

# resolve merfolk looter
def _effect_merfolk_looter(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "black_knight_001")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    
    return [_permanent_enters(card_id, controller)]

# resolve prodigal sorcerer
def _effect_prodigal_sorcerer(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "prodigal_sorcerer_001")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    return [_permanent_enters(card_id, controller)]

# resolve sol ring
def _effect_sol_ring(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "sol_ring_001")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    return [_permanent_enters(card_id, controller)]

# resolve millstone
def _effect_millstone(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "millstone_001")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    return [_permanent_enters(card_id, controller)]

# resolve rod of ruin
def _effect_rod_of_ruin(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "rod_of_ruin_001")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    return [_permanent_enters(card_id, controller)]

# resolve vanilla creature
def _effect_vanilla_creature(
    gs,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    
    return [_permanent_enters(card_id, controller)]

# resolve vanilla noncreature
def _effect_vanilla_noncreature(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    
    return [_permanent_enters(card_id, controller)]

# resolve white knight
def _effect_white_knight(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "white_knight_001")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    
    return [_permanent_enters(card_id, controller)]

# resolve black knight
def _effect_black_knight(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    card_id = extra.get("card_id", "black_knight_001")
    _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"))
    return [_permanent_enters(card_id, controller)]


# Dispatch table 

# Maps every card_id_base to its effect handler.
# Cards not explicitly listed fall through to vanilla handlers based on type.

EFFECT_HANDLERS: dict[str, EffectHandler] = {
    # Red — Burn
    "lightning_bolt": _effect_lightning_bolt,
    "shock": _effect_shock,
    "lava_spike": _effect_lava_spike,
    "flame_slash": _effect_flame_slash,
    "searing_spear": _effect_searing_spear,
    "skullcrack": _effect_skullcrack,
    "rift_bolt": _effect_rift_bolt,
    "incinerate": _effect_incinerate,
    # Red — Creatures
    "goblin_guide": _effect_vanilla_creature,
    "goblin_guide_trigger": _effect_goblin_guide_trigger,
    "goblin_bushwhacker": _effect_vanilla_creature,  
    "reckless_wurm": _effect_vanilla_creature,
    "monastery_swiftspear": _effect_vanilla_creature,
    "monastery_swiftspear_trigger": _effect_monastery_swiftspear_trigger,
    "wall_of_stone": _effect_vanilla_creature,
    # Blue — Counters / Bounce
    "counterspell": _effect_counterspell,
    "cancel": _effect_cancel,
    "negate": _effect_negate,
    "mana_leak": _effect_mana_leak,
    "unsummon": _effect_unsummon,
    "ponder": _effect_ponder,
    # Blue — Creatures
    "merfolk_looter": _effect_merfolk_looter,
    "prodigal_sorcerer": _effect_prodigal_sorcerer,
    "air_elemental": _effect_vanilla_creature,
    "phantasmal_bear": _effect_vanilla_creature,
    # Green — Pump / Utility
    "giant_growth": _effect_giant_growth,
    "rampant_growth": _effect_rampant_growth,
    "naturalize": _effect_naturalize,
    "vines_of_vastwood": _effect_vines_of_vastwood,
    # Green — Creatures
    "llanowar_elves": _effect_vanilla_creature,
    "elvish_mystic": _effect_vanilla_creature,
    "grizzly_bears": _effect_vanilla_creature,
    "leatherback_baloth": _effect_vanilla_creature,
    "troll_ascetic": _effect_vanilla_creature,
    # Black — Removal / Utility
    "dark_ritual": _effect_dark_ritual,
    "terror": _effect_terror,
    "doom_blade": _effect_doom_blade,
    "raise_dead": _effect_raise_dead,
    "mind_rot": _effect_mind_rot,
    # Black — Creatures
    "gray_merchant": _effect_gray_merchant,
    "gravedigger": _effect_gravedigger,
    "royal_assassin": _effect_vanilla_creature,
    "black_knight": _effect_black_knight,
    # White — Removal / Utility
    "swords_to_plowshares": _effect_swords_to_plowshares,
    "path_to_exile": _effect_path_to_exile,
    "healing_salve": _effect_healing_salve,
    "pacifism": _effect_pacifism,
    # White — Creatures
    "white_knight": _effect_white_knight,
    "serra_angel": _effect_vanilla_creature,
    "savannah_lions": _effect_vanilla_creature,
    "mother_of_runes": _effect_vanilla_creature,
    # Colorless
    "sol_ring": _effect_sol_ring,
    "ornithopter": _effect_vanilla_creature,
    "millstone": _effect_millstone,
    "rod_of_ruin": _effect_rod_of_ruin,
}

# dispatch a card effect
def resolve_effect(
    gs: GameState,
    card_id_base: str,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    handler = EFFECT_HANDLERS.get(card_id_base)
    if handler is None:
        # unknown card return empty (no effect).
        return []
    return handler(gs, controller, targets, abilities or [], extra or {})


# Keyword ability helpers 

# check whether abilities include a keyword
def has_keyword(abilities: list[dict[str, Any]], keyword: str) -> bool:
    for ab in abilities:
        if ab.get("type") == "keyword" and ab.get("name") == keyword:
            return True
    return False

# check whether abilities include haste
def has_haste(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "haste")

# check whether abilities include flying
def has_flying(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "flying")

# check whether abilities include first strike
def has_first_strike(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "first_strike")

# check whether abilities include trample
def has_trample(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "trample")

# check whether abilities include defender
def has_defender(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "defender")

# check whether abilities include vigilance
def has_vigilance(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "vigilance")

# check whether abilities include hexproof
def has_hexproof(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "hexproof")

# check whether abilities include protection
def has_protection(abilities: list[dict[str, Any]], colour: str) -> bool:
    return has_keyword(abilities, "protection")


# Triggered-ability helpers 

TRIGGER_EVENT_TYPES = {
    "ENTERS_BATTLEFIELD",
    "ATTACKS",
    "DEALT_DAMAGE",
    "DIES",
    "END_STEP",
}
# dispatch a card effect

# registry of card_id_base values that produce triggers with their
# event type and a summary of what happens.
TRIGGER_REGISTRY: dict[str, list[dict[str, Any]]] = {
    "goblin_guide": [
        {
            "event": "ATTACKS",
            "summary": "Defending player reveals top card of library. "
                       "If it's a land, put it into their hand.",
            "requires_target": False,
        },
    ],
    "gray_merchant": [
        {
            "event": "ENTERS_BATTLEFIELD",
            "summary": "Each opponent loses life equal to your devotion to black. "
                       "You gain that much life.",
            "requires_target": False,
        },
    ],
    "gravedigger": [
        {
            "event": "ENTERS_BATTLEFIELD",
            "summary": "Return target creature card from your graveyard to your hand.",
            "requires_target": True,
        },
    ],
    "goblin_bushwhacker": [
        {
            "event": "ENTERS_BATTLEFIELD",
            "summary": "If kicked, creatures you control get +1/+0 and haste until EOT.",
            "requires_target": False,
        },
    ],
    "monastery_swiftspear": [
        {
            "event": "CAST_NONCREATURE_SPELL",
            "summary": "Prowess: +1/+1 until end of turn.",
            "requires_target": False,
        },
    ],
}

# collect trigger data for an event
def check_triggers(
    gs: GameState,
    event_type: str,
    source_card_id: str,
    controller: str,
    targets: list[str] | None = None,
) -> list[dict[str, Any]]:
    triggers: list[dict[str, Any]] = []

    # scan every permanent on the battlefield
    for pid, perms in gs.battlefield.items():
        for perm in perms:
            base_id = perm.card_def_id
            if base_id in TRIGGER_REGISTRY:
                for trigger_def in TRIGGER_REGISTRY[base_id]:
                    if trigger_def["event"] == event_type:
                        triggers.append({
                            "trigger_id": f"trg_{len(triggers) + 1:02d}",
                            "source": perm.id,
                            "effect_summary": trigger_def["summary"],
                            "requires_target": trigger_def["requires_target"],
                            "legal_targets": [],
                        })

    return triggers