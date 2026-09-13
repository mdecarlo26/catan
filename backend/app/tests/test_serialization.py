"""Tests for `app.serialization.to_client_view` -- the single choke point
that must never leak hidden information (opponents' hand/dev-card
contents, the bank's dev-card pile identity/order) into a per-recipient
wire view.

Uses the real `app.game.board_generator` to build a small real board
(rather than a hand-rolled fake), since board geometry is real Wave 1
code now, not a stub.
"""

from __future__ import annotations

import json

from app.game import board_generator
from app.game.board import BuildingType, VertexBuilding
from app.game.players import DevCardType, PlayerState, ResourceType
from app.game.settings_schema import GameSettings
from app.game.state import Bank, GameState, Phase
from app.serialization import to_client_view


def _make_state() -> GameState:
    settings = GameSettings(player_count=3, board_layout="fixed")
    board = board_generator.generate_board_for_settings(settings)

    players = {
        "alice": PlayerState(player_id="alice", nickname="Alice", seat=0),
        "bob": PlayerState(player_id="bob", nickname="Bob", seat=1),
        "carol": PlayerState(player_id="carol", nickname="Carol", seat=2),
    }
    # Give each player a distinctive, easily-greppable hand/dev-card mix.
    players["alice"].hand = {
        ResourceType.BRICK: 3,
        ResourceType.LUMBER: 1,
        ResourceType.ORE: 0,
        ResourceType.GRAIN: 0,
        ResourceType.WOOL: 0,
    }
    players["bob"].hand = {
        ResourceType.BRICK: 0,
        ResourceType.LUMBER: 0,
        ResourceType.ORE: 5,
        ResourceType.GRAIN: 2,
        ResourceType.WOOL: 0,
    }
    players["bob"].dev_cards = {
        DevCardType.KNIGHT: 1,
        DevCardType.ROAD_BUILDING: 0,
        DevCardType.YEAR_OF_PLENTY: 0,
        DevCardType.MONOPOLY: 0,
        DevCardType.VICTORY_POINT: 1,
    }
    players["bob"].dev_cards_bought_this_turn = {
        DevCardType.KNIGHT: 0,
        DevCardType.ROAD_BUILDING: 0,
        DevCardType.YEAR_OF_PLENTY: 1,
        DevCardType.MONOPOLY: 0,
        DevCardType.VICTORY_POINT: 0,
    }
    players["carol"].hand = {
        ResourceType.BRICK: 0,
        ResourceType.LUMBER: 0,
        ResourceType.ORE: 0,
        ResourceType.GRAIN: 0,
        ResourceType.WOOL: 4,
    }

    # A couple of buildings/roads so WireBoardView has real content.
    some_vertex = next(iter(board_generator._build_vertex_universe(  # noqa: SLF001
        {h for h, tile in board.hexes.items() if tile.terrain.value != "sea"},
        set(board.hexes.keys()),
    )))
    board.buildings[some_vertex] = VertexBuilding(player_id="alice", building_type=BuildingType.SETTLEMENT)

    bank = Bank(
        resources={r: 19 for r in ResourceType},
        dev_card_pile=[DevCardType.KNIGHT, DevCardType.MONOPOLY, DevCardType.VICTORY_POINT],
    )

    return GameState(
        room_code="ABCD",
        settings=settings,
        phase=Phase.MAIN,
        turn_order=["alice", "bob", "carol"],
        current_player_index=0,
        board=board,
        bank=bank,
        players=players,
    )


def test_viewer_sees_their_own_hand_and_dev_cards():
    state = _make_state()
    view = to_client_view(state, "bob")

    assert view.viewer_player_id == "bob"
    bob_view = view.players["bob"]
    assert bob_view.hand == {
        ResourceType.BRICK: 0,
        ResourceType.LUMBER: 0,
        ResourceType.ORE: 5,
        ResourceType.GRAIN: 2,
        ResourceType.WOOL: 0,
    }
    assert bob_view.dev_cards == {
        DevCardType.KNIGHT: 1,
        DevCardType.ROAD_BUILDING: 0,
        DevCardType.YEAR_OF_PLENTY: 0,
        DevCardType.MONOPOLY: 0,
        DevCardType.VICTORY_POINT: 1,
    }
    assert bob_view.dev_cards_bought_this_turn == {
        DevCardType.KNIGHT: 0,
        DevCardType.ROAD_BUILDING: 0,
        DevCardType.YEAR_OF_PLENTY: 1,
        DevCardType.MONOPOLY: 0,
        DevCardType.VICTORY_POINT: 0,
    }
    assert bob_view.resource_card_count == 7
    # total_owned counts both playable dev_cards (2) and the 1 bought
    # this turn (not yet playable, but still owned).
    assert bob_view.dev_card_count == 3


def test_opponents_hand_and_dev_cards_are_masked_to_none_and_counts_only():
    state = _make_state()
    view = to_client_view(state, "bob")

    alice_view = view.players["alice"]
    carol_view = view.players["carol"]

    assert alice_view.hand is None
    assert alice_view.dev_cards is None
    assert alice_view.dev_cards_bought_this_turn is None
    assert alice_view.resource_card_count == 4  # 3 brick + 1 lumber
    assert alice_view.dev_card_count == 0

    assert carol_view.hand is None
    assert carol_view.dev_cards is None
    assert carol_view.dev_cards_bought_this_turn is None
    assert carol_view.resource_card_count == 4  # 4 wool


def test_bank_dev_card_pile_is_a_count_only():
    state = _make_state()
    view = to_client_view(state, "alice")

    assert view.bank_dev_card_count == 3
    # bank_resource_counts is legitimately public (everyone can see the
    # bank's remaining resource supply in real Catan).
    assert view.bank_resource_counts[ResourceType.BRICK] == 19


def test_no_hidden_info_leaks_anywhere_in_the_serialized_json_for_a_non_owning_viewer():
    """The load-bearing assertion: serialize a state for player A, then
    grep the *entire* JSON-serialized output for evidence of player B's
    real resource/dev-card identity -- not just check the known
    `hand`/`dev_cards` fields are masked, in case some other field ever
    accidentally carries the same information (e.g. a future field that
    embeds a raw `PlayerState`).
    """
    state = _make_state()
    view = to_client_view(state, "alice")
    dumped = view.model_dump(mode="json")
    raw = json.dumps(dumped)

    # Bob's and Carol's specific resource counts must never appear
    # anywhere in Alice's view except as their public aggregate counts
    # (resource_card_count / dev_card_count), which are legitimately
    # public. So: bob's hand dict and carol's hand dict, keyed by
    # resource, must not appear as sub-objects anywhere.
    assert dumped["players"]["bob"]["hand"] is None
    assert dumped["players"]["bob"]["dev_cards"] is None
    assert dumped["players"]["bob"]["dev_cards_bought_this_turn"] is None
    assert dumped["players"]["carol"]["hand"] is None
    assert dumped["players"]["carol"]["dev_cards"] is None
    assert dumped["players"]["carol"]["dev_cards_bought_this_turn"] is None

    # The bank's actual dev card pile (identity/order) must never appear;
    # only bank_dev_card_count (an int) should carry that information.
    assert "dev_card_pile" not in raw

    # Bob's and Carol's specific hand counts must not appear anywhere in
    # Alice's view as a structural leak (e.g. via some future field that
    # accidentally embeds a raw PlayerState). Card-type/resource-type
    # *names* legitimately appear elsewhere (Alice's own all-zero
    # dev_cards dict, bank_resource_counts, ...), so this checks the
    # specific distinctive counts from each opponent's fixture hand
    # rather than bare enum-value substrings.
    assert '"ore": 5' not in raw  # Bob's hand
    assert '"grain": 2' not in raw  # Bob's hand
    assert '"wool": 4' not in raw  # Carol's hand


def test_viewer_id_not_seated_still_masks_everyone():
    """A defensive case: if `viewer_player_id` doesn't match any seated
    player (shouldn't normally happen -- callers only call this for
    seated players), nothing should accidentally unmask.
    """
    state = _make_state()
    view = to_client_view(state, "not-a-real-player")

    for player_view in view.players.values():
        assert player_view.hand is None
        assert player_view.dev_cards is None
        assert player_view.dev_cards_bought_this_turn is None
