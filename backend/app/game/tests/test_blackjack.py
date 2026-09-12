"""Tests for `settings.blackjack_mode` (Blackjack-on-7), per
`app.game.rules.blackjack`'s pure mechanics and `app.game.rules_engine`'s
orchestration (`Phase.BLACKJACK_ROUND`, `GameState.blackjack_round`).

Covers: hand-value calculation (including soft aces), the dealer's fixed
auto-play threshold, `resolve_outcome`'s win/loss/push comparison for
every combination, `resolve_stake_payout`'s exact resource/piece-count
deltas for both stake kinds, the bettor queue walking in seat order, the
round being skipped entirely when nobody bets, `blackjack_mode` being
fully inert when off, and its inapplicability both in rush mode and for
a Knight-triggered robber move (only a *dice-roll* 7 ever offers it).

`app.game.board`'s real geometry functions are available (Wave 1 has
landed -- see `test_rush_mode.py`'s note), but blackjack's own
board-touching logic (`Board.buildings`/`Board.roads` lookups for a
structure stake) never calls the adjacency/geometry helpers directly, so
these tests use plain synthetic vertex/edge ids rather than a real
board -- mirroring `test_special_build_phase.py`'s minimal
geometry-free `_dummy_board()` approach.
"""

from __future__ import annotations

import pytest

from app.game import rules_engine
from app.game.actions import (
    BlackjackDeclineAction,
    BlackjackHitAction,
    BlackjackPlaceBetAction,
    BlackjackPlaceBetPayload,
    BlackjackStandAction,
    MoveRobberAction,
    MoveRobberPayload,
    StealResourceAction,
    StealResourcePayload,
)
from app.game.board import (
    Board,
    BuildingType,
    EdgeId,
    HexCoord,
    HexTile,
    Terrain,
    VertexBuilding,
    VertexId,
)
from app.game.players import PlayerState, ResourceType
from app.game.rules import blackjack
from app.game.rules_engine import RuleViolation
from app.game.settings_schema import GameSettings
from app.game.state import (
    AwaitingRobberPlacement,
    AwaitingSteal,
    Bank,
    BlackjackParticipant,
    BlackjackRoundState,
    BlackjackStake,
    Card,
    CardRank,
    CardSuit,
    GameState,
    Phase,
)

# ---------------------------------------------------------------------
# Fixture-building helpers (self-contained -- no conftest.py, matching
# this package's existing convention)
# ---------------------------------------------------------------------


#: A second hex, distinct from robber_hex's default (0, 0) -- MOVE_ROBBER
#: requires moving to a different hex, so every test that drives a real
#: MOVE_ROBBER action through rules_engine targets this one.
OTHER_HEX = HexCoord(9, 9)


def _dummy_board(**extra) -> Board:
    hex_coord = HexCoord(0, 0)
    kwargs = dict(
        hexes={
            hex_coord: HexTile(coord=hex_coord, terrain=Terrain.DESERT, number_token=None),
            OTHER_HEX: HexTile(coord=OTHER_HEX, terrain=Terrain.DESERT, number_token=None),
        },
        ports=[],
        buildings={},
        roads={},
        robber_hex=hex_coord,
    )
    kwargs.update(extra)
    return Board(**kwargs)


def _make_state(num_players: int, board: Board | None = None, **settings_kwargs) -> GameState:
    player_ids = [f"p{i}" for i in range(num_players)]
    players = {
        pid: PlayerState(player_id=pid, nickname=pid, seat=i) for i, pid in enumerate(player_ids)
    }
    settings_kwargs.setdefault("player_count", num_players)
    settings_kwargs.setdefault("discard_limit", 50)
    return GameState(
        room_code="TEST",
        settings=GameSettings(**settings_kwargs),
        phase=Phase.MAIN,
        turn_order=player_ids,
        current_player_index=0,
        board=board or _dummy_board(),
        bank=Bank(resources={r: 19 for r in ResourceType}, dev_card_pile=[]),
        players=players,
    )


def _card(rank: CardRank) -> Card:
    return Card(rank=rank, suit=CardSuit.SPADES)


# ---------------------------------------------------------------------
# Hand value / dealer auto-play (pure `rules.blackjack` functions)
# ---------------------------------------------------------------------


def test_hand_value_hard_totals():
    assert blackjack.hand_value([_card(CardRank.TEN), _card(CardRank.SEVEN)]) == 17
    assert blackjack.hand_value([_card(CardRank.KING), _card(CardRank.QUEEN)]) == 20


def test_hand_value_soft_ace_counts_as_eleven_when_safe():
    # A + 6 = soft 17 (ace as 11).
    assert blackjack.hand_value([_card(CardRank.ACE), _card(CardRank.SIX)]) == 17


def test_hand_value_ace_downgrades_to_avoid_bust():
    # A + 6 + K: 11+6+10 = 27 busts, so the ace downgrades to 1 -> 17.
    hand = [_card(CardRank.ACE), _card(CardRank.SIX), _card(CardRank.KING)]
    assert blackjack.hand_value(hand) == 17
    assert not blackjack.is_bust(hand)


def test_hand_value_multiple_aces():
    # A + A = 11+11=22 -> downgrade one ace -> 12 (soft 12).
    assert blackjack.hand_value([_card(CardRank.ACE), _card(CardRank.ACE)]) == 12


def test_hand_value_true_bust():
    hand = [_card(CardRank.KING), _card(CardRank.QUEEN), _card(CardRank.TWO)]
    assert blackjack.hand_value(hand) == 22
    assert blackjack.is_bust(hand)


def test_dealer_auto_play_threshold():
    assert blackjack.dealer_should_hit([_card(CardRank.TEN), _card(CardRank.SIX)])  # 16
    assert not blackjack.dealer_should_hit([_card(CardRank.TEN), _card(CardRank.SEVEN)])  # 17


def test_dealer_auto_play_draws_until_stand_or_bust():
    deck = [_card(CardRank.THREE)]  # drawn last (popped first from the end)
    hand = [_card(CardRank.TEN), _card(CardRank.FIVE)]  # 15 -> must hit
    blackjack.play_dealer_hand(deck, hand)
    assert blackjack.hand_value(hand) == 18
    assert deck == []


# ---------------------------------------------------------------------
# Outcome comparison
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "bettor_total,bettor_busted,dealer_total,dealer_busted,expected",
    [
        (25, True, 17, False, "loss"),  # bettor bust is an automatic loss...
        (25, True, 30, True, "loss"),  # ...even if the dealer also busted.
        (18, False, 22, True, "win"),  # dealer bust wins for a surviving bettor.
        (19, False, 17, False, "win"),
        (16, False, 19, False, "loss"),
        (18, False, 18, False, "push"),
    ],
)
def test_resolve_outcome(bettor_total, bettor_busted, dealer_total, dealer_busted, expected):
    assert (
        blackjack.resolve_outcome(bettor_total, bettor_busted, dealer_total, dealer_busted)
        == expected
    )


# ---------------------------------------------------------------------
# Payout math (`resolve_stake_payout`) -- every combination
# ---------------------------------------------------------------------


def _payout_state() -> GameState:
    return _make_state(2, blackjack_mode=True)


def test_resource_stake_loss_transfers_to_dealer():
    state = _payout_state()
    dealer, bettor = state.players["p0"], state.players["p1"]
    bettor.hand[ResourceType.LUMBER] = 3
    stake = BlackjackStake(kind="resources", resources={ResourceType.LUMBER: 2})

    blackjack.resolve_stake_payout(state, "p0", "p1", stake, "loss")

    assert bettor.hand[ResourceType.LUMBER] == 1
    assert dealer.hand[ResourceType.LUMBER] == 2


def test_resource_stake_win_paid_by_bank_not_dealer():
    state = _payout_state()
    dealer, bettor = state.players["p0"], state.players["p1"]
    bettor.hand[ResourceType.LUMBER] = 3
    bank_before = state.bank.resources[ResourceType.LUMBER]
    stake = BlackjackStake(kind="resources", resources={ResourceType.LUMBER: 2})

    blackjack.resolve_stake_payout(state, "p0", "p1", stake, "win")

    # Bettor keeps the original 3 plus the bank's matching 2 = 5.
    assert bettor.hand[ResourceType.LUMBER] == 5
    assert dealer.hand[ResourceType.LUMBER] == 0
    assert state.bank.resources[ResourceType.LUMBER] == bank_before - 2


def test_resource_stake_push_is_untouched():
    state = _payout_state()
    dealer, bettor = state.players["p0"], state.players["p1"]
    bettor.hand[ResourceType.LUMBER] = 3
    stake = BlackjackStake(kind="resources", resources={ResourceType.LUMBER: 2})

    blackjack.resolve_stake_payout(state, "p0", "p1", stake, "push")

    assert bettor.hand[ResourceType.LUMBER] == 3
    assert dealer.hand[ResourceType.LUMBER] == 0


def test_structure_stake_loss_returns_piece_to_owner_and_pays_dealer_from_bank():
    vertex_id: VertexId = (HexCoord(0, 0),)
    board = _dummy_board(buildings={vertex_id: VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)})
    state = _make_state(2, board=board, blackjack_mode=True)
    dealer, bettor = state.players["p0"], state.players["p1"]
    settlements_before = bettor.settlements_remaining
    bank_grain_before = state.bank.resources[ResourceType.GRAIN]
    stake = BlackjackStake(kind="settlement", vertex_id=vertex_id)

    blackjack.resolve_stake_payout(state, "p0", "p1", stake, "loss")

    # Piece removed from the board, returned to the OWNER's own supply --
    # never physically transferred to the dealer.
    assert vertex_id not in state.board.buildings
    assert bettor.settlements_remaining == settlements_before + 1
    # Dealer compensated from the bank at the settlement's normal build
    # cost (1 lumber/brick/wool/grain), not from the victim.
    for resource, amount in blackjack.SETTLEMENT_COST.items():
        assert dealer.hand[resource] == amount
    assert state.bank.resources[ResourceType.GRAIN] == bank_grain_before - blackjack.SETTLEMENT_COST[ResourceType.GRAIN]


def test_structure_stake_win_grants_extra_piece_slot_without_touching_the_board():
    vertex_id: VertexId = (HexCoord(0, 0),)
    board = _dummy_board(buildings={vertex_id: VertexBuilding(player_id="p1", building_type=BuildingType.CITY)})
    state = _make_state(2, board=board, blackjack_mode=True)
    dealer, bettor = state.players["p0"], state.players["p1"]
    cities_before = bettor.cities_remaining
    stake = BlackjackStake(kind="city", vertex_id=vertex_id)

    blackjack.resolve_stake_payout(state, "p0", "p1", stake, "win")

    # Piece was never at risk on a win -- board untouched.
    assert state.board.buildings[vertex_id].player_id == "p1"
    # Bank grants one extra piece-slot credit (not an instant free build).
    assert bettor.cities_remaining == cities_before + 1
    assert dealer.hand[ResourceType.ORE] == 0


def test_structure_stake_push_is_a_pure_no_op():
    edge_id: EdgeId = (HexCoord(0, 0), HexCoord(1, 0))
    board = _dummy_board(roads={edge_id: "p1"})
    state = _make_state(2, board=board, blackjack_mode=True)
    dealer, bettor = state.players["p0"], state.players["p1"]
    roads_before = bettor.roads_remaining
    stake = BlackjackStake(kind="road", edge_id=edge_id)

    blackjack.resolve_stake_payout(state, "p0", "p1", stake, "push")

    assert state.board.roads[edge_id] == "p1"
    assert bettor.roads_remaining == roads_before
    assert dealer.hand == {r: 0 for r in ResourceType}


def test_road_stake_loss_returns_road_and_pays_dealer():
    edge_id: EdgeId = (HexCoord(0, 0), HexCoord(1, 0))
    board = _dummy_board(roads={edge_id: "p1"})
    state = _make_state(2, board=board, blackjack_mode=True)
    dealer, bettor = state.players["p0"], state.players["p1"]
    roads_before = bettor.roads_remaining
    stake = BlackjackStake(kind="road", edge_id=edge_id)

    blackjack.resolve_stake_payout(state, "p0", "p1", stake, "loss")

    assert edge_id not in state.board.roads
    assert bettor.roads_remaining == roads_before + 1
    for resource, amount in blackjack.ROAD_COST.items():
        assert dealer.hand[resource] == amount


# ---------------------------------------------------------------------
# Round entry gating: settings.blackjack_mode / rush_mode / reason
# ---------------------------------------------------------------------


def test_blackjack_mode_off_is_fully_inert():
    """With blackjack_mode off, a dice-roll 7's robber move (0 steal
    candidates) resolves straight to Phase.MAIN as before -- no round is
    ever created.
    """
    state = _make_state(2, blackjack_mode=False)
    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingRobberPlacement(actor="p0", reason="dice_roll")

    rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=OTHER_HEX))
    )

    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None


def test_blackjack_inapplicable_in_rush_mode_even_when_both_settings_true():
    state = _make_state(2, blackjack_mode=True, rush_mode=True)
    state.phase = Phase.MAIN
    state.rush_pending_robber = AwaitingRobberPlacement(actor="p0", reason="dice_roll")

    rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=OTHER_HEX))
    )

    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None
    assert state.rush_pending_robber is None


def test_blackjack_never_offered_for_knight_triggered_robber_move():
    state = _make_state(3, blackjack_mode=True)
    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingRobberPlacement(actor="p0", reason="knight_card")

    rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=OTHER_HEX))
    )

    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None


def test_blackjack_round_starts_after_multi_candidate_steal_resolves():
    """The steal-resolution path (`_apply_steal_resource`) is the other
    call site that must offer blackjack -- exercised directly here via a
    manually-seeded `AwaitingSteal(reason="dice_roll")`.
    """
    state = _make_state(3, blackjack_mode=True)
    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingSteal(actor="p0", candidate_targets=["p1"], reason="dice_roll")

    rules_engine.validate_and_apply(
        state, "p0", StealResourceAction(payload=StealResourcePayload(target_player_id="p1"))
    )

    assert state.phase == Phase.BLACKJACK_ROUND
    assert state.blackjack_round is not None
    assert state.blackjack_round.dealer_id == "p0"
    assert set(state.blackjack_round.responses_pending) == {"p1", "p2"}


def test_blackjack_round_entry_skips_disconnected_players():
    state = _make_state(3, blackjack_mode=True)
    state.players["p2"].is_connected = False
    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingRobberPlacement(actor="p0", reason="dice_roll")

    rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=OTHER_HEX))
    )

    assert state.phase == Phase.BLACKJACK_ROUND
    assert state.blackjack_round.responses_pending == ["p1"]


def test_blackjack_round_no_op_when_nobody_else_connected():
    state = _make_state(2, blackjack_mode=True)
    state.players["p1"].is_connected = False
    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingRobberPlacement(actor="p0", reason="dice_roll")

    rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=OTHER_HEX))
    )

    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None


# ---------------------------------------------------------------------
# Betting collection: decline-only skips the round entirely
# ---------------------------------------------------------------------


def test_round_skipped_when_everyone_declines():
    state = _make_state(3, blackjack_mode=True)
    state.phase = Phase.BLACKJACK_ROUND
    state.blackjack_round = BlackjackRoundState(dealer_id="p0", responses_pending=["p1", "p2"])

    rules_engine.validate_and_apply(state, "p1", BlackjackDeclineAction())
    assert state.phase == Phase.BLACKJACK_ROUND  # still waiting on p2

    rules_engine.validate_and_apply(state, "p2", BlackjackDeclineAction())

    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None


def test_decline_rejected_from_ineligible_player():
    state = _make_state(3, blackjack_mode=True)
    state.phase = Phase.BLACKJACK_ROUND
    state.blackjack_round = BlackjackRoundState(dealer_id="p0", responses_pending=["p1"])

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p2", BlackjackDeclineAction())
    assert exc.value.code == "not_eligible"


def test_place_bet_rejects_more_than_one_stake_kind():
    state = _make_state(2, blackjack_mode=True)
    state.phase = Phase.BLACKJACK_ROUND
    state.blackjack_round = BlackjackRoundState(dealer_id="p0", responses_pending=["p1"])
    state.players["p1"].hand[ResourceType.LUMBER] = 1

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(
            state,
            "p1",
            BlackjackPlaceBetAction(
                payload=BlackjackPlaceBetPayload(
                    resources={ResourceType.LUMBER: 1}, edge_id=(HexCoord(0, 0), HexCoord(1, 0))
                )
            ),
        )
    assert exc.value.code == "invalid_bet"


def test_place_bet_rejects_unowned_structure():
    state = _make_state(2, blackjack_mode=True)
    state.phase = Phase.BLACKJACK_ROUND
    state.blackjack_round = BlackjackRoundState(dealer_id="p0", responses_pending=["p1"])

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(
            state,
            "p1",
            BlackjackPlaceBetAction(payload=BlackjackPlaceBetPayload(vertex_id=(HexCoord(0, 0),))),
        )
    assert exc.value.code == "invalid_target"


# ---------------------------------------------------------------------
# Full round: bettor queue order + resource/structure win/loss/push
# ---------------------------------------------------------------------


def test_full_round_queue_order_and_all_payout_outcomes(monkeypatch):
    vertex_id: VertexId = (HexCoord(5, 0),)
    edge_id: EdgeId = (HexCoord(0, 0), HexCoord(1, 0))
    board = _dummy_board(
        buildings={vertex_id: VertexBuilding(player_id="p3", building_type=BuildingType.SETTLEMENT)},
        roads={edge_id: "p2"},
    )
    state = _make_state(4, board=board, blackjack_mode=True)
    state.players["p1"].hand[ResourceType.LUMBER] = 3

    # Fixed "deck": build_shuffled_deck() is monkeypatched to hand back
    # this exact list. See the module-level draw-order note below.
    draw_sequence = [
        CardRank.TEN, CardRank.SEVEN,  # dealer's initial hand: 17 (stands)
        CardRank.TEN, CardRank.NINE,  # p1 (resource bet): 19 (stands) -> beats dealer -> WIN
        CardRank.TEN, CardRank.SIX,  # p2 (road bet): 16 -> will hit
        CardRank.TEN, CardRank.SEVEN,  # p3 (settlement bet): 17 (stands) -> ties dealer -> PUSH
        CardRank.TEN,  # p2's hit card: 16+10 = 26 -> bust -> LOSS
    ]
    # `deck.pop()` draws from the end, so build the list in reverse draw
    # order (this is exactly the same trick `list.pop()`-based Bank
    # .dev_card_pile dealing already relies on elsewhere in this codebase).
    fixed_deck = [Card(rank=r, suit=CardSuit.SPADES) for r in reversed(draw_sequence)]
    monkeypatch.setattr(blackjack, "build_shuffled_deck", lambda: list(fixed_deck))

    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingRobberPlacement(actor="p0", reason="dice_roll")
    rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=OTHER_HEX))
    )
    assert state.phase == Phase.BLACKJACK_ROUND
    assert set(state.blackjack_round.responses_pending) == {"p1", "p2", "p3"}

    # p1 bets 2 lumber; p2 stakes their road; p3 stakes their settlement.
    rules_engine.validate_and_apply(
        state,
        "p1",
        BlackjackPlaceBetAction(payload=BlackjackPlaceBetPayload(resources={ResourceType.LUMBER: 2})),
    )
    rules_engine.validate_and_apply(
        state, "p2", BlackjackPlaceBetAction(payload=BlackjackPlaceBetPayload(edge_id=edge_id))
    )
    rules_engine.validate_and_apply(
        state, "p3", BlackjackPlaceBetAction(payload=BlackjackPlaceBetPayload(vertex_id=vertex_id))
    )

    # Betting closed the moment the last response came in -- dealt and
    # walking the bettor queue now, in seat/turn order.
    round_ = state.blackjack_round
    assert round_.status == "bettor_turn"
    assert round_.bettor_queue == ["p1", "p2", "p3"]
    assert blackjack.hand_value(round_.dealer_hand) == 17
    assert blackjack.hand_value(round_.participants["p1"].hand) == 19
    assert blackjack.hand_value(round_.participants["p2"].hand) == 16
    assert blackjack.hand_value(round_.participants["p3"].hand) == 17

    # p2 can't jump the queue ahead of p1.
    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p2", BlackjackStandAction())
    assert exc.value.code == "not_your_turn"

    rules_engine.validate_and_apply(state, "p1", BlackjackStandAction())
    assert state.blackjack_round.bettor_queue == ["p2", "p3"]

    rules_engine.validate_and_apply(state, "p2", BlackjackHitAction())
    assert state.blackjack_round.bettor_queue == ["p3"]
    assert round_.participants["p2"].status == "busted"

    rules_engine.validate_and_apply(state, "p3", BlackjackStandAction())

    # Queue exhausted -> dealer reveals/auto-plays and the round resolves
    # synchronously, back to Phase.MAIN.
    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None

    dealer = state.players["p0"]
    p1, p2, p3 = state.players["p1"], state.players["p2"], state.players["p3"]

    # p1: resource WIN -- keeps original 1 lumber (bet 2 of 3) plus the
    # bank's matching 2 = 3 + 2 = 5.
    assert p1.hand[ResourceType.LUMBER] == 5

    # p2: road LOSS -- road removed from the board, returned to p2's own
    # supply, dealer compensated from the bank at ROAD_COST (lumber +
    # brick, 1 each) -- the only source of the dealer's lumber/brick here,
    # since p1's win was bank-funded, never touching the dealer's hand.
    assert edge_id not in state.board.roads
    assert p2.roads_remaining == 15 + 1
    for resource, amount in blackjack.ROAD_COST.items():
        assert dealer.hand[resource] == amount

    # p3: settlement PUSH -- completely untouched.
    assert state.board.buildings[vertex_id].player_id == "p3"
    assert p3.settlements_remaining == 5


def test_stalled_blackjack_betting_auto_declines_via_timer_helper():
    """Exercises the same decision `app.api.websocket
    ._force_advance_stalled_blackjack` drives (force-decline every
    remaining non-responder) directly through `rules_engine`, without
    needing the real asyncio timer loop.
    """
    state = _make_state(3, blackjack_mode=True)
    state.phase = Phase.BLACKJACK_ROUND
    state.blackjack_round = BlackjackRoundState(dealer_id="p0", responses_pending=["p1", "p2"])

    for pid in list(state.blackjack_round.responses_pending):
        rules_engine.validate_and_apply(state, pid, BlackjackDeclineAction())

    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None
