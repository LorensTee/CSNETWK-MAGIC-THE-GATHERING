from __future__ import annotations

import random

from server.game_state import GameState


def shuffle_hand_into_library(gs: GameState, player_id: str) -> None:
    """move all cards from player's hand to their library and shuffle"""
    hand = gs.hands.get(player_id, [])
    lib = gs.libraries.get(player_id, [])

    #move all fr hand to lib
    lib.extend(hand)
    hand.clear()

    #shuffle
    random.shuffle(lib)


def draw_fresh_hand(gs: GameState, player_id: str, count: int = 7) -> None:
    """draw count cards from the top of player's library into their hand"""
    lib = gs.libraries.get(player_id, [])
    hand = gs.hands.get(player_id, [])

    for _ in range(min(count, len(lib))):
        card = lib.pop(0)
        hand.append(card)


def bottom_cards(gs: GameState, player_id: str, card_ids: list[str]) -> None:
    """place the specified card ids from hand to the bottom of the library"""
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
    """process 1 mulligan choice"""
    current_mulls = gs.mulligan_counts.get(player_id, 0)

    if not keep:
        # take mulligan
        shuffle_hand_into_library(gs, player_id)
        draw_fresh_hand(gs, player_id, 7)
        # inc mulligan count
        gs.mulligan_counts[player_id] = current_mulls + 1
    else:
        if len(cards_to_bottom) != current_mulls:
            raise ValueError(f"Player took {current_mulls} mulligans but tried to bottom {len(cards_to_bottom)} cards.")
            
        if cards_to_bottom:
            bottom_cards(gs, player_id, cards_to_bottom)
