# stack manager module
# maintains lifo stack for spells and abilities
from __future__ import annotations

from typing import Any

from server.card_effects import resolve_effect
from server.game_state import GameState, StackItem

def check_state_based_actions(gs, card_loader) -> list[dict[str, Any]]:
    # sweep battlefield for dead creatures with lethal damage or zero toughness
    sba_changes = []

    # loop until state is stable
    try:
        # check battlefield for dead permanents
        for player_id, perms in gs.battlefield.items():
            dead_perms = []
            for perm in perms:
                # check valid card loader
                if card_loader is None:
                    continue
                
                # extract permanent instance id
                perm_id = getattr(perm, "instance_id", None) or getattr(perm, "id", None) or getattr(perm, "card_id", None)
                
                # check valid perm id
                if not perm_id:
                    continue
                    
                # derive base card id from instance id
                base_id = perm_id.rsplit("_", 1)[0] if "_" in perm_id else perm_id
                
                card_def = card_loader.get_card(base_id)
                
                # check creature type and calculate dynamic damage and toughness
                if card_def and hasattr(card_def, 'card_type') and "Creature" in card_def.card_type:
                    damage = getattr(perm, "damage", 0)
                    toughness = getattr(card_def, 'toughness', 0)

                    # mark dead if toughness zero or damage lethal
                    if damage >= toughness and toughness > 0:
                        dead_perms.append(perm)

            # process dead permanents            
            for dead_perm in dead_perms:
                # remove from battlefield
                gs.battlefield[player_id].remove(dead_perm)
                
                dead_id = getattr(dead_perm, "instance_id", None) or getattr(dead_perm, "id", None) or getattr(dead_perm, "card_id", None)
                base_id = dead_id.rsplit("_", 1)[0] if "_" in dead_id else dead_id
                
                # add to graveyard
                gs.graveyards.setdefault(player_id, []).append(dead_id)
                
                # append zone change packet
                sba_changes.append({
                    "type": "ZONE_CHANGE",
                    "object_id": dead_id,
                    "from_zone": "battlefield",
                    "to_zone": "graveyard",
                    "controller": player_id
                })
                print(f"--- [SERVER] SBA: {dead_id} died and {base_id} was sent to graveyard!")
    
    # handle sba execution errors        
    except Exception as e:
        import traceback
        print("\n" + "!"*50)
        print("💥 CRASH IN STATE BASED ACTIONS 💥")
        traceback.print_exc()
        print("!"*50 + "\n")
    
    # return recorded sba changes
    return sba_changes

class StackManager:
    # stack manager class
    # creates pushes and resolves items on stack

    def __init__(self) -> None:
        # init stack manager and card def cache
        self._card_def_cache: dict[str, Any] = {}

    def push(
        self,
        gs: GameState,
        item_type: str,
        source: str,
        controller: str,
        targets: list[str],
        card_def: Any = None,
    ) -> StackItem:
        # push spell or ability onto stack
        # incremnt stack counter
        gs.stack_counter += 1

        # construct stack item
        stack_item = StackItem(
            stack_item_id=f"stk_{gs.stack_counter:02d}",
            item_type=item_type,
            source=source,
            controller=controller,
            targets=targets,
            card_def=card_def,
        )

        # append to game state stack
        gs.stack.append(stack_item)
        if card_def is not None:
            self._card_def_cache[stack_item.stack_item_id] = card_def
        return stack_item

    # resolve top stack item
    def resolve_top(
            self,
            gs,
            card_loader: Any = None,
        ) -> tuple[str, list[dict[str, Any]]]:

        # return fizzle if stack empty
        if not gs.stack:
            return "FIZZLE", []

        # pop top item
        item = gs.stack.pop()
        card_def = item.card_def or self._card_def_cache.get(item.stack_item_id)

        # check if item fizzles from invalid targets
        fizzle = self._check_fizzle(gs, item)

        # return fizzle if targets invalid
        if fizzle:
            return "FIZZLE", []

        # assemble extra parameters for handler
        extra: dict[str, Any] = {
            "stack_item_id": item.stack_item_id,
            "card_id": item.source,
        }
        if getattr(item, "source_permanent", ""):
            extra["source_permanent"] = item.source_permanent
        if card_loader is not None:
            extra["card_loader"] = card_loader

        if card_def is not None:
            # derive base card id for resolution
            base_id = getattr(card_def, "card_id_base", "")
            if not base_id:
                base_id = item.source
                if "_" in base_id:
                    parts = base_id.rsplit("_", 1)
                    if parts[1].isdigit():
                        base_id = parts[0]
        else:
            base_id = item.source
            if "_" in base_id:
                parts = base_id.rsplit("_", 1)
                if parts[1].isdigit():
                    base_id = parts[0]

        # execute card effect handler
        state_changes = resolve_effect(
            gs,
            base_id,
            item.controller,
            item.targets,
            extra=extra,
        )

        # run state based actions sweep
        sba_changes = check_state_based_actions(gs, card_loader)
        
        # merge effect changes with sba changes
        state_changes.extend(sba_changes)

        return "RESOLVED", state_changes

    def push_trigger(
        self,
        gs: GameState,
        effect_base_id: str,
        controller: str,
        targets: list[str] | None = None,
        source_permanent: str = "",
    ) -> StackItem:
        # push trigger ability onto stack

        # increment stack counter
        gs.stack_counter += 1

        # construct trigger stack item
        item = StackItem(
            stack_item_id=f"stk_{gs.stack_counter:02d}",
            item_type="TRIGGER_ABILITY",
            source=effect_base_id,
            controller=controller,
            targets=targets or [],
            card_def=None,
        )
        if source_permanent:
            item.source_permanent = source_permanent
        gs.stack.append(item)
        return item

    # check if stack is empty
    def is_empty(self, gs: GameState) -> bool:
        return len(gs.stack) == 0

    # return top stack item without popping
    def top(self, gs: GameState) -> StackItem | None:
        if not gs.stack:
            return None
        return gs.stack[-1]

    def _check_fizzle(self, gs: GameState, item: StackItem) -> bool:
        # validate item targeting validity
        
        if not item.targets:
            return False
        
        valid_targets = 0
        for target in item.targets:
            # check target players
            if target in gs.life_totals:
                valid_targets += 1
                continue
            # check target permanents on battlefield
            for perms in gs.battlefield.values():
                if any(p.id == target for p in perms):
                    valid_targets += 1
                    break
            else:
                # check target stack items
                if any(si.stack_item_id == target for si in gs.stack):
                    valid_targets += 1

        # return true if no valid targets remain
        return valid_targets == 0

    # clear card def cache on game over
    def clear_cache(self) -> None:
        self._card_def_cache.clear()
