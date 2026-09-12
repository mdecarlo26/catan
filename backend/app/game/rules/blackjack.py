"""Blackjack-on-7 house rule: standard-deck mechanics + payout math.

Follows the `nuke_mode.py` / `robber_strategies.py` pure-function
convention exactly: no event emission, no `scoring` calls, validation
helpers (none needed here -- `rules_engine._validate_blackjack_*` owns
that) raise plain `ValueError` where relevant. `rules_engine` owns all
orchestration (dispatch, phase/`GameState.blackjack_round` transitions,
event construction, and calling `scoring.recompute_*` afterward).

Duplicated cost tables
-----------------------
`SETTLEMENT_COST` / `ROAD_COST` / `CITY_COST` below are exact duplicates
of the same-named tables in `rules_engine.py`. They are **not** imported
from there: `rules_engine` imports this module at startup (to dispatch
`BLACKJACK_*` actions), so importing back from `rules_engine` here would
be circular. These are frozen, standard-Catan costs that cannot drift
silently -- `test_blackjack.py`'s payout tests assert exact resource
deltas against both copies' values, so a change to one without the other
fails loudly in CI, not silently in production.
"""

from __future__ import annotations

import random
from typing import Literal

from app.game.board import BuildingType, PlayerId
from app.game.players import ResourceType
from app.game.state import (
    BlackjackParticipant,
    BlackjackStake,
    Card,
    CardRank,
    CardSuit,
    GameState,
)

SETTLEMENT_COST: dict[ResourceType, int] = {
    ResourceType.LUMBER: 1,
    ResourceType.BRICK: 1,
    ResourceType.WOOL: 1,
    ResourceType.GRAIN: 1,
}
ROAD_COST: dict[ResourceType, int] = {
    ResourceType.LUMBER: 1,
    ResourceType.BRICK: 1,
}
CITY_COST: dict[ResourceType, int] = {
    ResourceType.ORE: 3,
    ResourceType.GRAIN: 2,
}

_STRUCTURE_COST: dict[str, dict[ResourceType, int]] = {
    "settlement": SETTLEMENT_COST,
    "city": CITY_COST,
    "road": ROAD_COST,
}

#: Blackjack target / bust threshold. Standard rules.
BLACKJACK_TARGET = 21
#: Dealer auto-play rule per the plan: hit while <= 16, stand on >= 17.
#: No soft-17 distinction -- kept simple, exactly as specified.
DEALER_STAND_THRESHOLD = 17


# ---------------------------------------------------------------------
# Deck / dealing
# ---------------------------------------------------------------------


def build_shuffled_deck(rng: random.Random | None = None) -> list[Card]:
    """A freshly shuffled standard 52-card deck. Convention (matching
    `Bank.dev_card_pile`): the *last* element is the next draw, so
    dealing is always `deck.pop()` -- shuffle order doesn't care.
    """
    deck = [Card(rank=rank, suit=suit) for suit in CardSuit for rank in CardRank]
    (rng or random).shuffle(deck)
    return deck


def draw_card(deck: list[Card]) -> Card:
    """Draw (and remove) the top card. Caller guarantees `deck` is
    non-empty -- a single 52-card deck can never be exhausted by one
    blackjack round (at most 8 players * ~a handful of cards each), so
    no reshuffle-on-empty handling is implemented.
    """
    return deck.pop()


# ---------------------------------------------------------------------
# Hand value
# ---------------------------------------------------------------------


def hand_value(cards: list[Card]) -> int:
    """Best total <= 21 if achievable, else the minimal total (every ace
    already counted as 1). Standard soft-ace handling: count every ace as
    11 first, then downgrade aces to 1 one at a time while the total
    exceeds 21.
    """
    total = 0
    aces = 0
    for card in cards:
        if card.rank == CardRank.ACE:
            total += 11
            aces += 1
        elif card.rank in (CardRank.JACK, CardRank.QUEEN, CardRank.KING):
            total += 10
        else:
            total += int(card.rank.value)
    while total > BLACKJACK_TARGET and aces > 0:
        total -= 10
        aces -= 1
    return total


def is_bust(cards: list[Card]) -> bool:
    return hand_value(cards) > BLACKJACK_TARGET


def dealer_should_hit(cards: list[Card]) -> bool:
    return hand_value(cards) <= 16


def play_dealer_hand(deck: list[Card], hand: list[Card]) -> list[Card]:
    """Mutates and returns `hand` in place, drawing from `deck` per the
    fixed dealer rule (hit <= 16, stand >= 17). No double-down/split/
    soft-17 nuance -- exactly the plan's simplified rule.
    """
    while dealer_should_hit(hand):
        hand.append(draw_card(deck))
    return hand


# ---------------------------------------------------------------------
# Bet resolution
# ---------------------------------------------------------------------


def resolve_outcome(
    bettor_total: int, bettor_busted: bool, dealer_total: int, dealer_busted: bool
) -> Literal["win", "loss", "push"]:
    """Standard comparison: a bust is an automatic loss regardless of the
    dealer's hand; a dealer bust wins for every still-alive bettor;
    otherwise higher total wins, equal totals push. No bonus payout for
    a natural (two-card) 21 -- it's compared like any other total, per
    this implementation's documented "keep it simple" call (the plan
    specifies hit/stand only, no double-down/split, and says nothing
    about a natural-blackjack bonus multiplier).
    """
    if bettor_busted:
        return "loss"
    if dealer_busted:
        return "win"
    if bettor_total > dealer_total:
        return "win"
    if bettor_total < dealer_total:
        return "loss"
    return "push"


def resolve_stake_payout(
    state: GameState,
    dealer_id: PlayerId,
    bettor_id: PlayerId,
    stake: BlackjackStake,
    result: Literal["win", "loss", "push"],
) -> None:
    """Mutate `state` per the plan's exact win/loss/push payout rules.

    The stake itself is never moved/escrowed at bet-placement time (see
    `BlackjackStake`'s docstring) -- only checked for ownership/
    affordability then. This function is the ONE place any of it
    actually changes hands, and only for `"win"`/`"loss"` (a `"push"` is
    a pure no-op: the stake was never touched, so there's nothing to
    return).

    - Resource stake, loss: the exact staked cards move from the
      bettor's hand straight to the dealer's.
    - Resource stake, win: the bank hands the bettor an equal number of
      matching cards on top of what they already have (their stake was
      never at risk on a win).
    - Structure stake, loss: the piece is removed from the board and
      returned to the *owner's own* available-to-build supply (never
      physically transferred to the dealer -- relocating a road/
      settlement onto another player's territory isn't meaningful). As
      the documented house-edge equivalent, the dealer is separately
      compensated from the **bank** at that structure's normal build
      cost, so the dealer's incentive stays "only ever gains" for both
      stake kinds. This is a documented interpretation the plan
      delegates to implementation, not something the user specified to
      this level of detail.
    - Structure stake, win: the piece is untouched (it was never
      actually at risk except on a loss) and the bank grants the bettor
      one extra unit of that piece type in their available-to-build
      supply -- a piece-slot credit redeemable later through the normal
      BUILD_* flow (still at normal resource cost), not a free instant
      placement.

    Caller (`rules_engine`) is responsible for `scoring.recompute_*`
    afterward if a road/settlement/city changed hands or was removed --
    this module doesn't call into `scoring`, matching `nuke_mode.py`'s
    convention.
    """
    if result == "push":
        return

    dealer = state.players[dealer_id]
    bettor = state.players[bettor_id]

    if stake.kind == "resources":
        resources = stake.resources or {}
        if result == "loss":
            for resource, amount in resources.items():
                if amount <= 0:
                    continue
                bettor.hand[resource] = bettor.hand.get(resource, 0) - amount
                dealer.hand[resource] = dealer.hand.get(resource, 0) + amount
        else:  # win
            for resource, amount in resources.items():
                if amount <= 0:
                    continue
                state.bank.resources[resource] = state.bank.resources.get(resource, 0) - amount
                bettor.hand[resource] = bettor.hand.get(resource, 0) + amount
        return

    # Structure stake ("settlement" / "city" / "road").
    if result == "loss":
        board = state.board
        if stake.kind == "road":
            assert stake.edge_id is not None
            board.roads.pop(stake.edge_id, None)
            bettor.roads_remaining += 1
        else:
            assert stake.vertex_id is not None
            board.buildings.pop(stake.vertex_id, None)
            if stake.kind == "city":
                bettor.cities_remaining += 1
            else:
                bettor.settlements_remaining += 1
        # Bank-funded dealer compensation, per this function's docstring.
        # Deliberately not gated on the bank actually holding enough of
        # each resource (a rare, optional house rule; mirrors this
        # codebase's existing looseness elsewhere, e.g. `dev_cards
        # .year_of_plenty_grant`, about the bank's supply going negative
        # in an edge case rather than needing a dedicated shortage rule).
        for resource, amount in _STRUCTURE_COST[stake.kind].items():
            state.bank.resources[resource] = state.bank.resources.get(resource, 0) - amount
            dealer.hand[resource] = dealer.hand.get(resource, 0) + amount
    else:  # win: piece untouched, bank grants one extra piece-slot credit.
        if stake.kind == "road":
            bettor.roads_remaining += 1
        elif stake.kind == "city":
            bettor.cities_remaining += 1
        else:
            bettor.settlements_remaining += 1


def stake_kind_for_bet(state: GameState, vertex_id, edge_id) -> str:
    """Resolve a `BLACKJACK_PLACE_BET` payload's `vertex_id`/`edge_id`
    into the concrete `BlackjackStake.kind` string ("settlement" / "city"
    / "road"). Caller has already validated ownership; this only reads
    `Board.buildings` to distinguish settlement vs. city.
    """
    if edge_id is not None:
        return "road"
    assert vertex_id is not None
    building = state.board.buildings[vertex_id]
    return "city" if building.building_type == BuildingType.CITY else "settlement"


def new_participant(stake: BlackjackStake) -> BlackjackParticipant:
    return BlackjackParticipant(stake=stake)
