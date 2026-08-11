from __future__ import annotations

from typing import Any

from server.card_effects import resolve_effect
from server.game_state import GameState, StackItem

def check_state_based_actions(gs, card_loader) -> list[dict[str, Any]]:
    """Sweep the board for creatures with lethal damage and destroy"""
    sba_changes = []
    
    try:
        for player_id, perms in gs.battlefield.items():
            dead_perms = []
            for perm in perms:
                if card_loader is None:
                    continue
                    
                # get the ID from permanent
                perm_id = getattr(perm, "instance_id", None) or getattr(perm, "id", None) or getattr(perm, "card_id", None)
                
                if not perm_id:
                    continue
                    
                # look up the card definition
                base_id = perm_id.rsplit("_", 1)[0] if "_" in perm_id else perm_id
                
                card_def = card_loader.get_card(base_id)
                
                #check if it's a creature
                if card_def and hasattr(card_def, 'card_type') and "Creature" in card_def.card_type:
                    # get damage from permanent
                    damage = getattr(perm, "damage", 0)
                    toughness = getattr(card_def, 'toughness', 0)
                    
                    if damage >= toughness and toughness > 0:
                        dead_perms.append(perm)
                        
            #move the dead to the graveyard
            for dead_perm in dead_perms:
                # remove from battlefield
                gs.battlefield[player_id].remove(dead_perm)
                
                # get unique instance ID for the network packet
                dead_id = getattr(dead_perm, "instance_id", None) or getattr(dead_perm, "id", None) or getattr(dead_perm, "card_id", None)
                
                base_id = dead_id.rsplit("_", 1)[0] if "_" in dead_id else dead_id
                gs.graveyard[player_id].append(base_id)
                
                sba_changes.append({
                    "type": "ZONE_CHANGE",
                    "object_id": dead_id,
                    "from_zone": "battlefield",
                    "to_zone": "graveyard",
                    "controller": player_id
                })
                print(f"--- [SERVER] SBA: {dead_id} died and {base_id} was sent to graveyard!")
                
    except Exception as e:
        import traceback
        print("\n" + "!"*50)
        print("💥 CRASH IN STATE BASED ACTIONS 💥")
        traceback.print_exc()
        print("!"*50 + "\n")
        
    return sba_changes

class StackManager:
    """manages stack for a single game session"""

    def __init__(self) -> None:
        # used during resolution to find the effect handler
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
        """create a new stackitem and push it onto the stack"""
        gs.stack_counter += 1
        stack_item = StackItem(
            stack_item_id=f"stk_{gs.stack_counter:02d}",
            item_type=item_type,
            source=source,
            controller=controller,
            targets=targets,
            card_def=card_def,
        )
        gs.stack.append(stack_item)
        if card_def is not None:
            self._card_def_cache[stack_item.stack_item_id] = card_def
        return stack_item
    
    def resolve_top(
            self,
            gs,
            card_loader: Any = None,
        ) -> tuple[str, list[dict[str, Any]]]:
        """pop top item from the stack and resolve"""
        if not gs.stack:
            return "FIZZLE", []

        item = gs.stack.pop()
        card_def = item.card_def or self._card_def_cache.get(item.stack_item_id)

        fizzle = self._check_fizzle(gs, item)
        if fizzle:
            return "FIZZLE", []

        extra: dict[str, Any] = {
            "stack_item_id": item.stack_item_id,
            "card_id": item.source,
        }
        if card_loader is not None:
            extra["card_loader"] = card_loader

        if card_def is not None:
            base_id = getattr(card_def, "card_id_base", "")
            if not base_id:
                base_id = item.source.rsplit("_", 1)[0] if "_" in item.source else item.source
        else:
            base_id = item.source.rsplit("_", 1)[0] if "_" in item.source else item.source

        # cast the spell
        state_changes = resolve_effect(
            gs,
            base_id,
            item.controller,
            item.targets,
            extra=extra,
        )

        sba_changes = check_state_based_actions(gs, card_loader)
        
        # combine the spells changes with the death changes
        state_changes.extend(sba_changes)

        return "RESOLVED", state_changes

    def is_empty(self, gs: GameState) -> bool:
        """return true if the stack is empty"""
        return len(gs.stack) == 0

    def top(self, gs: GameState) -> StackItem | None:
        """return the top item on the stack or none if empty"""
        if not gs.stack:
            return None
        return gs.stack[-1]

    def _check_fizzle(self, gs: GameState, item: StackItem) -> bool:
        """check if all of item's targets have become illegal"""
        if not item.targets:
            return False

        # count how many targets are valid
        valid_targets = 0
        for target in item.targets:
            # if target is a player ID
            if target in gs.life_totals:
                valid_targets += 1
                continue
            #if target is a permanent on any battlefield
            for perms in gs.battlefield.values():
                if any(p.id == target for p in perms):
                    valid_targets += 1
                    break
            else:
                #if target is on stack
                if any(si.stack_item_id == target for si in gs.stack):
                    valid_targets += 1

        # fizzle if no targets valid
        return valid_targets == 0

    def clear_cache(self) -> None:
        """Reset the cache"""
        self._card_def_cache.clear()
