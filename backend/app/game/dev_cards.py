"""Development card deck composition and pure play-effect mechanics.

Orchestration (validation, phase/`pending` transitions, event emission)
for `PLAY_DEV_CARD` lives in `rules_engine`; this module only owns "what
cards are in the deck" and "what a card's effect does to hands/bank",
kept independent of any WS/event concerns so it's trivially testable.
"""

from __future__ import annotations

import random

from app.game.board import PlayerId
from app.game.players import DevCardType, PlayerState, ResourceHand, ResourceType
from app.game.state import Bank

#: Standard 25-card deck composition, used as-is for 2-4 players.
BASE_DECK_COMPOSITION: dict[DevCardType, int] = {
    DevCardType.KNIGHT: 14,
    DevCardType.VICTORY_POINT: 5,
    DevCardType.ROAD_BUILDING: 2,
    DevCardType.YEAR_OF_PLENTY: 2,
    DevCardType.MONOPOLY: 2,
}


def deck_composition(player_count: int) -> dict[DevCardType, int]:
    """Dev card counts for a game of `player_count` players.

    <=4 players: the exact standard 25-card deck (14 Knight, 5 Victory
    Point, 2 each of Road Building / Year of Plenty / Monopoly).

    5-8 players: there is no official dev-card count published for an
    extended 7-8p game (only the 5-6p expansion is official, and even
    that isn't in the frozen `GameSettings`/board contracts this module
    was built against), so this scales the base counts proportionally to
    `player_count / 4` (rounded to the nearest integer), and adds one
    extra Victory Point card per player beyond 4. This is a deliberate,
    documented house-rule approximation -- monotonically increasing with
    player count -- not a claim of official accuracy.
    """
    if player_count <= 4:
        return dict(BASE_DECK_COMPOSITION)

    scale = player_count / 4
    composition: dict[DevCardType, int] = {}
    for card, base in BASE_DECK_COMPOSITION.items():
        if card == DevCardType.VICTORY_POINT:
            composition[card] = base + (player_count - 4)
        else:
            composition[card] = int(base * scale + 0.5)
    return composition


def build_deck(player_count: int, rng: random.Random | None = None) -> list[DevCardType]:
    """A shuffled dev card pile for `player_count` players, ready to
    assign to `Bank.dev_card_pile` (convention: the *last* element is the
    top of the pile / next draw, per `Bank`'s docstring -- shuffling
    doesn't care about that, drawing via `list.pop()` does).
    """
    composition = deck_composition(player_count)
    deck: list[DevCardType] = []
    for card, count in composition.items():
        deck.extend([card] * count)
    (rng or random).shuffle(deck)
    return deck


#: Dev card purchase cost: 1 ore, 1 wool, 1 grain.
DEV_CARD_COST: dict[ResourceType, int] = {
    ResourceType.ORE: 1,
    ResourceType.WOOL: 1,
    ResourceType.GRAIN: 1,
}


def monopoly_transfer(
    players: dict[PlayerId, PlayerState],
    actor_id: PlayerId,
    resource: ResourceType,
) -> dict[PlayerId, int]:
    """MONOPOLY effect: take every unit of `resource` from every other
    player's hand and give them all to `actor_id`.

    Returns player_id -> amount taken, for logging/UI purposes.
    """
    actor = players[actor_id]
    taken: dict[PlayerId, int] = {}
    for player_id, player in players.items():
        if player_id == actor_id:
            continue
        amount = player.hand.get(resource, 0)
        if amount:
            player.hand[resource] = 0
            actor.hand[resource] = actor.hand.get(resource, 0) + amount
            taken[player_id] = amount
    return taken


def year_of_plenty_grant(bank: Bank, player: PlayerState, resources: list[ResourceType]) -> None:
    """YEAR_OF_PLENTY effect: grant the two (possibly repeated) requested
    resources from the bank to `player`.

    Caller (`rules_engine`) is responsible for having already validated
    the bank actually holds enough of each requested resource -- this
    function assumes that's true and just moves the cards.
    """
    for resource in resources:
        bank.resources[resource] -= 1
        player.hand[resource] = player.hand.get(resource, 0) + 1


def fold_bought_this_turn_into_hand(player: PlayerState) -> None:
    """End-of-turn bookkeeping: dev cards bought this turn become
    eligible to play from now on. Mirrors the contract documented on
    `PlayerState.dev_cards_bought_this_turn`.
    """
    for card, count in player.dev_cards_bought_this_turn.items():
        if count:
            player.dev_cards[card] = player.dev_cards.get(card, 0) + count
    player.dev_cards_bought_this_turn = {d: 0 for d in DevCardType}


def total_owned(player: PlayerState) -> int:
    """Total dev cards a player holds, including ones bought this turn
    but not yet playable -- used for `DEV_CARD_COUNT_CHANGED` events.
    """
    return sum(player.dev_cards.values()) + sum(player.dev_cards_bought_this_turn.values())


def hand_total(hand: ResourceHand) -> int:
    return sum(hand.values())
