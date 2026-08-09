"""
server/stack.py — Stack Manager (Module 02: Server Engine)

Maintains the LIFO (last-in, first-out) stack of spells and abilities.

When a player casts a spell or activates an ability, the ``StackManager``
creates a ``StackItem`` and pushes it onto the stack.  When both players
pass priority consecutively, the top item is resolved — the effect is
executed via ``card_effects.resolve_effect()`` and the resulting state
changes are returned to the caller for broadcasting as ``STACK_RESOLVE``.
"""

from __future__ import annotations

from typing import Any

from server.card_effects import resolve_effect
from server.game_state import GameState, StackItem

def check_state_based_actions(gs, card_loader) -> list[dict[str, Any]]:
    """Sweep the board for creatures with lethal damage and destroy them."""
    sba_changes = []
    
    try:
        for player_id, perms in gs.battlefield.items():
            dead_perms = []
            for perm in perms:
                if card_loader is None:
                    continue
                    
                # SAFELY get the ID from the Permanent object
                # (We check common names: instance_id, id, or card_id)
                perm_id = getattr(perm, "instance_id", None) or getattr(perm, "id", None) or getattr(perm, "card_id", None)
                
                # If we still can't find an ID, skip it
                if not perm_id:
                    continue
                    
                # To look up the card definition, we usually need the base name 
                # (e.g. "mountain" instead of "mountain_005")
                base_id = perm_id.rsplit("_", 1)[0] if "_" in perm_id else perm_id
                
                card_def = card_loader.get_card(base_id)
                
                # Safely check if it's a creature
                if card_def and hasattr(card_def, 'card_type') and "Creature" in card_def.card_type:
                    # Get damage from the Permanent object
                    damage = getattr(perm, "damage", 0)
                    toughness = getattr(card_def, 'toughness', 0)
                    
                    if damage >= toughness and toughness > 0:
                        dead_perms.append(perm)
                        
            # Move the dead creatures to the graveyard
            for dead_perm in dead_perms:
                # 1. Remove from battlefield
                gs.battlefield[player_id].remove(dead_perm)
                
                # 2. Get the unique instance ID for the network packet
                dead_id = getattr(dead_perm, "instance_id", None) or getattr(dead_perm, "id", None) or getattr(dead_perm, "card_id", None)
                
                # --- THE FIX: Strip the _001 suffix to get the raw card name! ---
                base_id = dead_id.rsplit("_", 1)[0] if "_" in dead_id else dead_id
                
                # 3. Append the INSTANCE id to the graveyard (matches the
                #    combat path and build_visible_state, which read
                #    gs.graveyards; spec §8.4 keeps the object's identity).
                gs.graveyards.setdefault(player_id, []).append(dead_id)
                
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
    """Manages the stack for a single game session.

    This class does NOT send PDUs itself — it returns data structures that
    the caller (``GameLifecycle``) uses to broadcast ``STACK_PUSH`` and
    ``STACK_RESOLVE``.
    """

    def __init__(self) -> None:
        # Maps stack_item_id → card_def (the CardDef from the card loader).
        # Used during resolution to find the effect handler.
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
        """Create a new ``StackItem`` and push it onto the stack.

        Parameters
        ----------
        gs :
            Game state (mutated in place — item appended to *gs.stack*).
        item_type :
            ``'SPELL'``, ``'ABILITY'``, or ``'TRIGGER_ABILITY'``.
        source :
            card_id of the card that produced this item.
        controller :
            Player ID of the controller.
        targets :
            List of target IDs (player_id or permanent_id).
        card_def :
            The ``CardDef`` for this item (used at resolution time).

        Returns
        -------
        The created ``StackItem`` (already appended to *gs.stack*).
        """
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
        """Pop the top item from the stack and resolve it.

        Parameters
        ----------
        gs :
            Game state (mutated in place — item popped).
        card_loader :
            Optional ``CardLoader`` reference passed through to effect
            handlers that need card-definition lookups (e.g. devotion).

        Returns
        -------
        ``(result, state_changes)`` where *result* is ``'RESOLVED'`` or
        ``'FIZZLE'`` and *state_changes* is a list of change dicts.

        A spell "fizzles" if ALL of its targets have become illegal since
        it was put on the stack.
        """
        if not gs.stack:
            return "FIZZLE", []

        item = gs.stack.pop()
        card_def = item.card_def or self._card_def_cache.get(item.stack_item_id)

        # Determine if the item fizzles
        fizzle = self._check_fizzle(gs, item)
        if fizzle:
            return "FIZZLE", []

        # Resolve the effect.
        extra: dict[str, Any] = {
            "stack_item_id": item.stack_item_id,
            "card_id": item.source,
        }
        if getattr(item, "source_permanent", ""):
            extra["source_permanent"] = item.source_permanent
        if card_loader is not None:
            extra["card_loader"] = card_loader

        if card_def is not None:
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

        # 1. Cast the spell and do the math
        state_changes = resolve_effect(
            gs,
            base_id,
            item.controller,
            item.targets,
            extra=extra,
        )

        # 2. RUN THE SWEEP! Check if anything died from the math
        sba_changes = check_state_based_actions(gs, card_loader)
        
        # 3. Combine the spell's changes with the death changes
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
        """Put a triggered ability (RFC §8.6.1) onto the stack.

        Parameters
        ----------
        effect_base_id :
            Pseudo base id resolving to the trigger's effect handler
            (e.g. ``'goblin_guide_trigger'``).
        source_permanent :
            Instance id of the permanent whose ability triggered (used by
            effects that need the source, e.g. prowess pumps).
        """
        gs.stack_counter += 1
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

    def is_empty(self, gs: GameState) -> bool:
        """Return ``True`` if the stack has no items."""
        return len(gs.stack) == 0

    def top(self, gs: GameState) -> StackItem | None:
        """Return the top item on the stack, or ``None`` if empty."""
        if not gs.stack:
            return None
        return gs.stack[-1]

    def _check_fizzle(self, gs: GameState, item: StackItem) -> bool:
        """Check if all of *item*'s targets have become illegal.

        A spell fizzles if all targets are no longer valid (e.g. a
        "destroy target creature" spell whose target has already left
        the battlefield).

        This is a simplified check — it only checks whether target
        permanents still exist on the battlefield if they are permanent
        IDs, and whether target players still exist.
        """
        if not item.targets:
            return False  # No targets required → never fizzles for targeting reasons.

        # Count how many targets are still valid.
        valid_targets = 0
        for target in item.targets:
            # Check if target is a player ID.
            if target in gs.life_totals:
                valid_targets += 1
                continue
            # Check if target is a permanent on any battlefield.
            for perms in gs.battlefield.values():
                if any(p.id == target for p in perms):
                    valid_targets += 1
                    break
            else:
                # Check if target is on the stack.
                if any(si.stack_item_id == target for si in gs.stack):
                    valid_targets += 1

        # Fizzle if no targets remain valid.
        return valid_targets == 0

    def clear_cache(self) -> None:
        """Reset the internal card-def cache (called on ``GAME_OVER``)."""
        self._card_def_cache.clear()
