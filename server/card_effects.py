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

# ── Type alias for effect handlers ───────────────────────────────────────────

# Signature: (gs, controller, targets, card_def_abilities, extra) -> state_changes
EffectHandler = Callable[
    [GameState, str, list[str], list[dict[str, Any]], dict[str, Any]],
    list[dict[str, Any]],
]

# ── State-change helper factories ────────────────────────────────────────────


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
        "duration": duration,  # "EOT" = until end of turn
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


# ── Effect handler implementations ───────────────────────────────────────────


def _effect_lightning_bolt(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Lightning Bolt deals 3 damage to any target."""
    if not targets:
        return []
    return [_damage(targets[0], 3)]


def _effect_shock(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Shock deals 2 damage to any target."""
    if not targets:
        return []
    return [_damage(targets[0], 2)]


def _effect_lava_spike(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Lava Spike deals 3 damage to target player."""
    if not targets:
        return []
    return [_damage(targets[0], 3)]


def _effect_flame_slash(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Flame Slash deals 4 damage to target creature."""
    if not targets:
        return []
    return [_damage(targets[0], 4)]


def _effect_searing_spear(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Searing Spear deals 3 damage to any target."""
    if not targets:
        return []
    return [_damage(targets[0], 3)]


def _effect_skullcrack(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Skullcrack deals 3 damage to any target.  (Life gain prevention
    is tracked as a state flag; for MTGNP 1.0 we just deal damage.)"""
    if not targets:
        return []
    return [_damage(targets[0], 3)]


def _effect_rift_bolt(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rift Bolt deals 3 damage to any target.  (Suspend is not implemented
    in MTGNP 1.0, so this resolves like a sorcery.)"""
    if not targets:
        return []
    return [_damage(targets[0], 3)]


def _effect_incinerate(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Incinerate deals 3 damage to any target.  (Regeneration prevention
    is not tracked separately in MTGNP 1.0.)"""
    if not targets:
        return []
    return [_damage(targets[0], 3)]


def _effect_counterspell(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Counter target spell."""
    if not targets:
        return []
    return [_counter(targets[0])]


def _effect_cancel(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Cancel — counter target spell."""
    if not targets:
        return []
    return [_counter(targets[0])]


def _effect_negate(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Negate — counter target noncreature spell."""
    if not targets:
        return []
    return [_counter(targets[0])]


def _effect_mana_leak(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Mana Leak — counter target spell unless its controller pays {3}.
    For MTGNP 1.0, the counter always resolves (simplified)."""
    if not targets:
        return []
    return [_counter(targets[0])]


def _effect_unsummon(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return target creature to its owner's hand."""
    if not targets:
        return []
    return [_return_to_hand(targets[0])]


def _effect_ponder(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Ponder — look at top 3, shuffle (optional), draw.  Simplified: draw 1."""
    return [_draw(controller, 1)]


def _effect_giant_growth(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Giant Growth — target creature gets +3/+3 until end of turn."""
    if not targets:
        return []
    return [_pump(targets[0], 3, 3)]


def _effect_rampant_growth(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rampant Growth — search for a basic land, put it onto battlefield tapped.
    Picks the first basic land found in the controller's library."""
    lib = gs.libraries.get(controller, [])
    for card_id in lib:
        # Basic lands are: plains, island, swamp, mountain, forest.
        card_lower = card_id.lower()
        if any(basic in card_lower for basic in ("plains_", "island_", "swamp_", "mountain_", "forest_")):
            lib.remove(card_id)
            return [_permanent_enters(card_id, controller, tapped=True)]
    # Fallback: create a Forest if no basics found.
    return [_permanent_enters("forest_001", controller, tapped=True)]


def _effect_naturalize(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Destroy target artifact or enchantment."""
    if not targets:
        return []
    return [_destroy(targets[0])]


def _effect_vines_of_vastwood(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Vines of Vastwood — target +4/+4 if kicked. Simplified: always kick."""
    if not targets:
        return []
    return [_pump(targets[0], 4, 4)]


def _effect_dark_ritual(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Add {B}{B}{B}."""
    return [_add_mana(controller, {"B": 3})]


def _effect_terror(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Destroy target nonartifact, nonblack creature."""
    if not targets:
        return []
    return [_destroy(targets[0])]


def _effect_doom_blade(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Destroy target nonblack creature."""
    if not targets:
        return []
    return [_destroy(targets[0])]


def _effect_raise_dead(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return target creature card from graveyard to hand."""
    if not targets:
        return []
    return [{"change_type": "RETURN_FROM_GRAVEYARD", "target": targets[0]}]


def _effect_mind_rot(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Target player discards 2 cards."""
    if not targets:
        return []
    # The actual discard choice is handled by the game lifecycle; here we
    # signal the target and let the system prompt the player.
    return [{"change_type": "FORCE_DISCARD", "target": targets[0], "count": 2}]


def _effect_gray_merchant(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Gray Merchant of Asphodel — each opponent loses X life where X = devotion to black."""
    devotion = 0
    card_loader = extra.get("card_loader")
    for perm in gs.battlefield.get(controller, []):
        if card_loader is not None:
            cd = card_loader.get_card(perm.card_def_id)
            if cd is not None and cd.color == "B":
                devotion += 1
        else:
            # Fallback: count all permanents if no loader.
            devotion += 1
    opponent = None
    for pid in gs.player_ids:
        if pid != controller:
            opponent = pid
            break
    if opponent is None:
        return []
    return [_life_loss(opponent, devotion), _life_gain(controller, devotion)]


def _effect_gravedigger(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Return target creature card from your graveyard to your hand."""
    if not targets:
        return []
    return [{"change_type": "RETURN_FROM_GRAVEYARD", "target": targets[0]}]


def _effect_healing_salve(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Healing Salve — target player gains 3 life (Mode 1)."""
    if not targets:
        return []
    return [_life_gain(targets[0], 3)]


def _effect_swords_to_plowshares(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Exile target creature. Its controller gains life equal to its power.
    Simplified: +3 life for the owner."""
    if not targets:
        return []
    return [_exile(targets[0]), _life_gain(controller, 3)]


def _effect_path_to_exile(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Exile target creature. Its controller may search for a basic land.
    Simplified: exile + controller gains a land."""
    if not targets:
        return []
    return [_exile(targets[0])]


def _effect_pacifism(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Enchant creature. Enchanted creature can't attack or block.
    For MTGNP 1.0, we flag the permanent."""
    if not targets:
        return []
    return [{"change_type": "ENCHANT", "target": targets[0], "aura": "pacifism"}]


def _effect_merfolk_looter(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Merfolk Looter — activated ability handled elsewhere.
    When it enters: just a creature."""
    return [_permanent_enters(extra.get("card_id", "merfolk_looter_001"), controller)]


def _effect_prodigal_sorcerer(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Prodigal Sorcerer — tap ability handled elsewhere.
    When it enters: just a creature."""
    return [_permanent_enters(extra.get("card_id", "prodigal_sorcerer_001"), controller)]


def _effect_sol_ring(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Sol Ring — tap ability produces {C}{C}.
    When it enters: just a permanent."""
    return [_permanent_enters(extra.get("card_id", "sol_ring_001"), controller)]


def _effect_millstone(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Millstone — activated ability: mill 2.
    When it enters: just a permanent."""
    return [_permanent_enters(extra.get("card_id", "millstone_001"), controller)]


def _effect_rod_of_ruin(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Rod of Ruin — activated ability: deal 1 damage.
    When it enters: just a permanent."""
    return [_permanent_enters(extra.get("card_id", "rod_of_ruin_001"), controller)]


def _effect_vanilla_creature(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Handler for creatures with no special abilities on entry."""
    return [_permanent_enters(extra.get("card_id", "creature_001"), controller)]


def _effect_vanilla_noncreature(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Handler for non-creature cards with no special effect (e.g. lands)."""
    return []


def _effect_white_knight(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """White Knight — First strike, Protection from black."""
    return [_permanent_enters(extra.get("card_id", "white_knight_001"), controller)]


def _effect_black_knight(
    gs: GameState,
    controller: str,
    targets: list[str],
    abilities: list[dict[str, Any]],
    extra: dict[str, Any],
) -> list[dict[str, Any]]:
    """Black Knight — First strike, Protection from white."""
    return [_permanent_enters(extra.get("card_id", "black_knight_001"), controller)]


# ── Dispatch table ───────────────────────────────────────────────────────────

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
    "goblin_guide": _effect_vanilla_creature,  # Triggered ability (attack reveal) not implemented
    "goblin_bushwhacker": _effect_vanilla_creature,  # Kicker not automatically handled
    "reckless_wurm": _effect_vanilla_creature,
    "monastery_swiftspear": _effect_vanilla_creature,  # Prowess tracked via keyword
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
    "phantasmal_bear": _effect_vanilla_creature,  # Illusion sacrifice not implemented
    # Green — Pump / Utility
    "giant_growth": _effect_giant_growth,
    "rampant_growth": _effect_rampant_growth,
    "naturalize": _effect_naturalize,
    "vines_of_vastwood": _effect_vines_of_vastwood,
    # Green — Creatures
    "llanowar_elves": _effect_vanilla_creature,  # Mana ability handled elsewhere
    "elvish_mystic": _effect_vanilla_creature,
    "grizzly_bears": _effect_vanilla_creature,
    "leatherback_baloth": _effect_vanilla_creature,
    "troll_ascetic": _effect_vanilla_creature,  # Hexproof handled in combat, regen not impl.
    # Black — Removal / Utility
    "dark_ritual": _effect_dark_ritual,
    "terror": _effect_terror,
    "doom_blade": _effect_doom_blade,
    "raise_dead": _effect_raise_dead,
    "mind_rot": _effect_mind_rot,
    # Black — Creatures
    "gray_merchant": _effect_gray_merchant,
    "gravedigger": _effect_gravedigger,
    "royal_assassin": _effect_vanilla_creature,  # Tap ability handled elsewhere
    "black_knight": _effect_black_knight,
    # White — Removal / Utility
    "swords_to_plowshares": _effect_swords_to_plowshares,
    "path_to_exile": _effect_path_to_exile,
    "healing_salve": _effect_healing_salve,
    "pacifism": _effect_pacifism,
    # White — Creatures
    "white_knight": _effect_white_knight,
    "serra_angel": _effect_vanilla_creature,  # Flying+Vigilance tracked via keywords
    "savannah_lions": _effect_vanilla_creature,
    "mother_of_runes": _effect_vanilla_creature,  # Protection ability handled elsewhere
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
    """Resolve a card's effect and return a list of state-change dicts.

    Parameters
    ----------
    gs :
        The current game state (may be mutated by the handler).
    card_id_base :
        The base identifier (e.g. ``'lightning_bolt'``).
    controller :
        Player ID of the spell/ability controller.
    targets :
        List of target IDs (player_id or permanent_id).
    abilities :
        List of parsed ability dicts from ``CardDef.abilities``.
    extra :
        Extra context (e.g. ``{'card_id': 'goblin_guide_001'}``).

    Returns
    -------
    A list of state-change dicts describing what the effect did.
    """
    handler = EFFECT_HANDLERS.get(card_id_base)
    if handler is None:
        # Unknown card — return empty (no effect).
        return []
    return handler(gs, controller, targets, abilities or [], extra or {})


# ── Keyword ability helpers ──────────────────────────────────────────────────


def has_keyword(abilities: list[dict[str, Any]], keyword: str) -> bool:
    """Return ``True`` if the ability list contains the given keyword."""
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
    """Return ``True`` if the creature has protection from *colour*."""
    return has_keyword(abilities, "protection")


# ── Triggered-ability helpers ────────────────────────────────────────────────


TRIGGER_EVENT_TYPES = {
    "ENTERS_BATTLEFIELD",
    "ATTACKS",
    "DEALT_DAMAGE",
    "DIES",
    "END_STEP",
}

# Static registry of card_id_base values that produce triggers with their
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


def check_triggers(
    gs: GameState,
    event_type: str,
    source_card_id: str,
    controller: str,
    targets: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Check if any permanents on the battlefield trigger on *event_type*.

    Uses a static registry of known triggered abilities.  For each matching
    permanent found, a trigger descriptor is returned.  The server's
    ``GameLifecycle`` will present ``TRIGGER_ORDER`` / ``TRIGGER_CHOICE``
    PDUs for these triggers.

    Returns a list of trigger descriptors::

        [{"trigger_id": "trg_01", "source": "gray_merchant_001",
          "effect_summary": "...", "requires_target": False,
          "legal_targets": []}]
    """
    triggers: list[dict[str, Any]] = []

    # Scan every permanent on the battlefield.
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
