from __future__ import annotations

from typing import Any, Callable

from server.game_state import GameState, Permanent

EffectHandler = Callable[
    [GameState, str, list[str], list[dict[str, Any]], dict[str, Any]],
    list[dict[str, Any]],
]


def _damage(target: str, amount: int) -> dict[str, Any]:
    return {"change_type": "DAMAGE", "target": target, "amount": amount}


def _life_gain(target: str, amount: int) -> dict[str, Any]:
    return {"change_type": "LIFE_GAIN", "target": target, "amount": amount}


def _life_loss(target: str, amount: int) -> dict[str, Any]:
    return {"change_type": "LIFE_LOSS", "target": target, "amount": amount}


def _destroy(target: str) -> dict[str, Any]:
    return {"change_type": "DESTROY", "target": target}


def _permanent_enters(card_id: str, controller: str, tapped: bool = False) -> dict[str, Any]:
    return {
        "change_type": "PERMANENT_ENTERS",
        "card_id": card_id,
        "controller": controller,
        "tapped": tapped,
    }


def _counter(spell_stack_id: str) -> dict[str, Any]:
    return {"change_type": "COUNTER", "target": spell_stack_id}


def _pump(target_id: str, power: int, toughness: int, duration: str = "EOT") -> dict[str, Any]:
    return {
        "change_type": "PUMP",
        "target": target_id,
        "power": power,
        "toughness": toughness,
        "duration": duration,  # until end of turn
    }


def _return_to_hand(target: str) -> dict[str, Any]:
    return {"change_type": "RETURN_TO_HAND", "target": target}


def _exile(target: str) -> dict[str, Any]:
    return {"change_type": "EXILE", "target": target}


def _draw(player: str, count: int = 1) -> dict[str, Any]:
    return {"change_type": "DRAW", "player": player, "count": count}


def _discard_card(player: str, card_ids: list[str]) -> dict[str, Any]:
    return {"change_type": "DISCARD", "player": player, "card_ids": card_ids}


def _add_mana(controller: str, mana: dict[str, int]) -> dict[str, Any]:
    return {"change_type": "ADD_MANA", "player": controller, "mana": mana}



#Effect Helpers

def _apply_damage(gs: GameState, target: str, damage_amount: int) -> None:
    """physically mutates GameState for damage effects"""
    if target in gs.life_totals:
        gs.life_totals[target] -= damage_amount
    else:
        for perms in gs.battlefield.values():
            for perm in perms:
                if perm.id == target:
                    perm.damage = getattr(perm, "damage", 0) + damage_amount
                    return

def _apply_counter(gs: GameState, target_stack_id: str) -> None:
    """physically removes a countered spell from stack"""
    for i, item in enumerate(gs.stack):
        if item.stack_item_id == target_stack_id:
            gs.stack.pop(i)
            break

def _apply_bounce(gs: GameState, target_id: str) -> None:
    """physically moves a permanent from the battlefield to its owner's hand"""
    for player_id, perms in gs.battlefield.items():
        for i, perm in enumerate(perms):
            if perm.id == target_id:
                popped_perm = perms.pop(i)
                card_id = getattr(popped_perm, "card_id", target_id.rsplit("_", 1)[0])

                if player_id not in gs.hands:
                    gs.hands[player_id] = []
                gs.hands[player_id].append(card_id)
                return

def _apply_draw(gs: GameState, player_id: str, amount: int = 1) -> None:
    """physically draw cards from the library to the hand"""
    for _ in range(amount):
        if player_id in gs.libraries and gs.libraries[player_id]:
            drawn_card = gs.libraries[player_id].pop() 
            
            if player_id not in gs.hands:
                gs.hands[player_id] = []
            gs.hands[player_id].append(drawn_card)

def _apply_pump(gs: GameState, target_id: str, power_bonus: int, toughness_bonus: int) -> None:
    """add temporary stats to creature"""
    for perms in gs.battlefield.values():
        for perm in perms:
            if perm.id == target_id:
                perm.temp_power = getattr(perm, "temp_power", 0) + power_bonus
                perm.temp_toughness = getattr(perm, "temp_toughness", 0) + toughness_bonus
                return

def _apply_destroy(gs: GameState, target_id: str) -> None:
    """move perm from the battle to the grave"""
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

def _apply_add_mana(gs: GameState, player_id: str, mana_dict: dict[str, int]) -> None:
    """add floating mana to player's pool"""
    pool = gs.mana_pools.get(player_id)
    if pool:
        for color, amount in mana_dict.items():
            current = getattr(pool, color, 0)
            setattr(pool, color, current + amount)

def _apply_raise_dead(gs: GameState, player_id: str, target_card_id: str) -> None:
    """move card from graveyard to the hand"""
    gy = gs.graveyards.get(player_id, [])
    if target_card_id in gy:
        gy.remove(target_card_id)
        if player_id not in gs.hands:
            gs.hands[player_id] = []
        gs.hands[player_id].append(target_card_id)

def _apply_life_change(gs: GameState, player_id: str, amount: int) -> None:
    """modifies players life"""
    if player_id in gs.life_totals:
        gs.life_totals[player_id] += amount

def _apply_exile(gs: GameState, target_id: str) -> None:
    """move a permanent from battlefield to exile"""
    for player_id, perms in gs.battlefield.items():
        for i, perm in enumerate(perms):
            if perm.id == target_id:
                popped = perms.pop(i)
                card_id = getattr(popped, "card_id", target_id.rsplit("_", 1)[0])
                
                if not hasattr(gs, "exile"):
                    gs.exile = {}
                if player_id not in gs.exile:
                    gs.exile[player_id] = []
                    
                gs.exile[player_id].append(card_id)
                return

def _apply_enchant(gs: GameState, target_id: str, aura_name: str) -> None:
    """attach aura flag to a perm"""
    for perms in gs.battlefield.values():
        for perm in perms:
            if perm.id == target_id:
                if not hasattr(perm, "auras"):
                    perm.auras = []
                perm.auras.append(aura_name)
                return

def _apply_spawn_permanent(gs, controller: str, card_id: str, card_loader: Any = None, enters_tapped: bool = False) -> None:
    """construct and place perm onto battlefield"""
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
            
            if any(ab.get("name") == "haste" for ab in cd.abilities):
                haste = True

    # Construct perm
    from server.game_state import Permanent
    perm = Permanent(
        id=card_id,
        card_def_id=def_id,
        controller=controller,
        tapped=enters_tapped,
        power=power,
        toughness=toughness,
        summoning_sick=not haste,
    )
    
    # put on board
    gs.battlefield.setdefault(controller, []).append(perm)
    

# effect handler implementations


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


def _effect_ponder(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    _apply_draw(gs, controller, 1)
    return [_draw(controller, 1)]


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
            _apply_spawn_permanent(gs, controller, card_id, extra.get("card_loader"), enters_tapped=True)
            return [_permanent_enters(card_id, controller, tapped=True)]
            
    fallback_id = "forest_001"
    _apply_spawn_permanent(gs, controller, fallback_id, extra.get("card_loader"), enters_tapped=True)
    return [_permanent_enters(fallback_id, controller, tapped=True)]


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


def _effect_dark_ritual(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    _apply_add_mana(gs, controller, {"B": 3})
    return [_add_mana(controller, {"B": 3})]


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


def _effect_mind_rot(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    return [{"change_type": "FORCE_DISCARD", "target": targets[0], "count": 2}]


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
            cd = card_loader.get_card(getattr(perm, "card_def_id", perm.card_id))
            if cd is not None and cd.color == "B":
                devotion += 1
        else:
            devotion += 1
            
    opponent = next((pid for pid in gs.player_ids if pid != controller), None)
    if opponent is None:
        return []
        
    _apply_life_change(gs, opponent, -devotion)
    _apply_life_change(gs, controller, devotion)
    return [_life_loss(opponent, devotion), _life_gain(controller, devotion)]


def _effect_gravedigger(
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


def _effect_swords_to_plowshares(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    if not targets:
        return []
    _apply_exile(gs, targets[0])
    _apply_life_change(gs, controller, 3) 
    return [_exile(targets[0]), _life_gain(controller, 3)]


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
    "goblin_bushwhacker": _effect_vanilla_creature,
    "reckless_wurm": _effect_vanilla_creature,
    "monastery_swiftspear": _effect_vanilla_creature,
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


def resolve_effect(
    gs: GameState,
    card_id_base: str,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]] | None = None,
    extra: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """resolve card's effect and return a list of state change dict"""
    handler = EFFECT_HANDLERS.get(card_id_base)
    if handler is None:
        return []
    return handler(gs, controller, targets, abilities or [], extra or {})


# keyword ability helpers


def has_keyword(abilities: list[dict[str, Any]], keyword: str) -> bool:
    """return true if the ability list contains the given keyword"""
    for ab in abilities:
        if ab.get("type") == "keyword" and ab.get("name") == keyword:
            return True
    return False


def has_haste(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "haste")


def has_flying(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "flying")


def has_first_strike(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "first_strike")


def has_trample(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "trample")


def has_defender(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "defender")


def has_vigilance(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "vigilance")


def has_hexproof(abilities: list[dict[str, Any]]) -> bool:
    return has_keyword(abilities, "hexproof")


def has_protection(abilities: list[dict[str, Any]], colour: str) -> bool:
    return has_keyword(abilities, "protection")


# Triggered ability helpers
TRIGGER_EVENT_TYPES = {
    "ENTERS_BATTLEFIELD",
    "ATTACKS",
    "DEALT_DAMAGE",
    "DIES",
    "END_STEP",
}

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


def check_triggers(
    gs: GameState,
    event_type: str,
    source_card_id: str,
    controller: str,
    targets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """check if any perms on the battle trigger"""
    triggers: list[dict[str, Any]] = []

    # scan every perm on battlefield
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
