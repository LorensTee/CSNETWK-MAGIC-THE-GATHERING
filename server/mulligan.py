from __future__ import annotations

import random

from server.game_state import GameState


#move hand to library and shuffle
def shuffle_hand_into_library(gs: GameState, player_id: str) -> None:
    hand = gs.hands.get(player_id, [])
    lib = gs.libraries.get(player_id, [])

    lib.extend(hand)
    hand.clear()

    random.shuffle(lib)

#draw a new set of 7 cards for the player, replacing their hand
def draw_fresh_hand(gs: GameState, player_id: str, count: int = 7) -> None:
    lib = gs.libraries.get(player_id, [])
    hand = gs.hands.get(player_id, [])

    for _ in range(min(count, len(lib))):
        card = lib.pop(0)
        hand.append(card)

#move cards from hand to bottom of library
def bottom_cards(gs: GameState, player_id: str, card_ids: list[str]) -> None:
    hand = gs.hands.get(player_id, [])
    lib = gs.libraries.get(player_id, [])

    for cid in card_ids:
        if cid in hand:
            hand.remove(cid)
            lib.append(cid)


def process_mulligan_choice(
    gs: GameState,
    player_id: str,
    keep: bool,
    cards_to_bottom: list[str],
) -> None:
    current_mulls = gs.mulligan_counts.get(player_id, 0)

    if not keep:
        # do mulligan
        shuffle_hand_into_library(gs, player_id)
        draw_fresh_hand(gs, player_id, 7)
        #increment mulligan count
        gs.mulligan_counts[player_id] = current_mulls + 1
    else:
        if len(cards_to_bottom) != current_mulls:
            raise ValueError(f"Player took {current_mulls} mulligans but tried to bottom {len(cards_to_bottom)} cards.")
            
        if cards_to_bottom:
            bottom_cards(gs, player_id, cards_to_bottom)
