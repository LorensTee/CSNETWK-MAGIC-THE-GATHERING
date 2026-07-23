"""
server/mulligan.py — London Mulligan Logic (Module 02: Server Engine)

Implements the London Mulligan rule (RFC §6.4):

* A player who mulligans shuffles their hand into their library, draws a
  fresh 7-card hand, and increments their mulligan count.
* A player who keeps after N mulligans must bottom exactly N cards from
  their hand (they go to the bottom of the library in any order).
"""

from __future__ import annotations

import random

from server.game_state import GameState


def shuffle_hand_into_library(gs: GameState, player_id: str) -> None:
    """Move all cards from *player_id*'s hand into their library and shuffle.

    Parameters
    ----------
    gs :
        Game state (mutated in place).
    player_id :
        The player who is mulliganing.
    """
    hand = gs.hands.get(player_id, [])
    lib = gs.libraries.get(player_id, [])

    # Move all hand cards to library.
    lib.extend(hand)
    hand.clear()

    # Shuffle the library.
    random.shuffle(lib)


def draw_fresh_hand(gs: GameState, player_id: str, count: int = 7) -> None:
    """Draw *count* cards from the top of *player_id*'s library into their hand.

    Parameters
    ----------
    gs :
        Game state (mutated in place).
    player_id :
        The player drawing cards.
    count :
        Number of cards to draw (default 7).
    """
    lib = gs.libraries.get(player_id, [])
    hand = gs.hands.get(player_id, [])

    for _ in range(min(count, len(lib))):
        card = lib.pop(0)
        hand.append(card)


def bottom_cards(gs: GameState, player_id: str, card_ids: list[str]) -> None:
    """Place the specified *card_ids* from hand to the bottom of the library.

    Cards are placed in the order given (first card in *card_ids* goes to
    the bottom, second card goes on top of it, etc.).

    Parameters
    ----------
    gs :
        Game state (mutated in place).
    player_id :
        The player bottoming cards.
    card_ids :
        List of card instance IDs to bottom.
    """
    hand = gs.hands.get(player_id, [])
    lib = gs.libraries.get(player_id, [])

    for cid in card_ids:
        if cid in hand:
            hand.remove(cid)
            lib.append(cid)  # Bottom = append to end of library list.


def process_mulligan_choice(
    gs: GameState,
    player_id: str,
    keep: bool,
    cards_to_bottom: list[str],
) -> None:
    """Process a single mulligan decision.

    Parameters
    ----------
    gs :
        Game state (mutated in place).
    player_id :
        The player making the choice.
    keep :
        ``True`` if the player keeps their hand, ``False`` to mulligan.
    cards_to_bottom :
        Card IDs to bottom (must be empty if *keep* is False).
    """
    if not keep:
        # Take a mulligan.
        shuffle_hand_into_library(gs, player_id)
        draw_fresh_hand(gs, player_id, 7)
        # Increment mulligan count.
        gs.mulligan_counts[player_id] = gs.mulligan_counts.get(player_id, 0) + 1
    else:
        # Keep — bottom the required cards.
        if cards_to_bottom:
            bottom_cards(gs, player_id, cards_to_bottom)
