"""`GameState` -> per-recipient masked wire view.

Single choke point for hidden information: everything that ends up on
the wire for a `STATE_SNAPSHOT` (or the initial `GAME_STARTED` state
push) must go through `to_client_view()`. See `app.protocol.events`'s
module docstring ("Wire safety of board/game-state data") for the shapes
this builds, and `app.game.state`/`app.game.board`'s own docstrings for
why the internal models (tuple-keyed dicts, full opponent hands) are
never safe to serialize directly.

What gets masked, per recipient (`viewer_player_id`):
  - Every other player's `hand`/`dev_cards` are omitted (`None`) in their
    `MaskedPlayerView` entry -- only `resource_card_count`/
    `dev_card_count` totals are exposed for them. The viewer's own entry
    gets both populated.
  - `Bank.dev_card_pile` (card identity + draw order) is reduced to
    `bank_dev_card_count`, a bare integer.

What is *not* masked here (deliberately -- these are public information
in real Catan, or per-recipient masking for them happens one layer up,
in `app.api.websocket`, where the "who else is entitled to see this"
question depends on the specific action, not just "am I the state's
owner"):
  - `GameState.pending`: `AwaitingDiscard.required_counts` is counts
    only; `AwaitingSteal.candidate_targets` is player ids only (no hand
    contents); `AwaitingTradeResponse.offered`/`requested` describe an
    already-public trade offer. None of these leak resource identity
    beyond what's already public. `GameState.rush_pending_discard` /
    `rush_pending_robber` reuse those same models (see their own
    docstrings) and are passed through unmasked for the same reason.
  - `ResourceStolenPayload.resource` -- populated for the actor/victim,
    `None` for bystanders, per its own docstring. This is a *transient
    event* payload, not part of `GameState`/`ClientGameStateView`, so
    `app.api.websocket` builds the per-recipient masked copy directly
    when broadcasting a `RESOURCE_STOLEN` `RuleEvent` -- there's nothing
    for this module to do for it.
"""

from __future__ import annotations

from app.game import dev_cards
from app.game.board import PlayerId
from app.game.state import BlackjackRoundState, GameState
from app.protocol.events import (
    BlackjackParticipantView,
    BlackjackRoundView,
    ClientGameStateView,
    MaskedPlayerView,
    WireBoardView,
    WireHexTile,
    WireRoad,
    WireVertexBuilding,
)


def _wire_board(state: GameState) -> WireBoardView:
    board = state.board
    return WireBoardView(
        hexes=[
            WireHexTile(coord=tile.coord, terrain=tile.terrain, number_token=tile.number_token)
            for tile in board.hexes.values()
        ],
        ports=list(board.ports),
        buildings=[
            WireVertexBuilding(
                vertex_id=vertex_id,
                player_id=building.player_id,
                building_type=building.building_type,
            )
            for vertex_id, building in board.buildings.items()
        ],
        roads=[
            WireRoad(edge_id=edge_id, player_id=owner) for edge_id, owner in board.roads.items()
        ],
        robber_hex=board.robber_hex,
    )


def _masked_player_view(state: GameState, player_id: PlayerId, viewer_player_id: PlayerId) -> MaskedPlayerView:
    player = state.players[player_id]
    is_viewer = player_id == viewer_player_id
    return MaskedPlayerView(
        player_id=player.player_id,
        nickname=player.nickname,
        seat=player.seat,
        is_connected=player.is_connected,
        is_bot=player.is_bot,
        victory_points=player.victory_points,
        knights_played=player.knights_played,
        has_longest_road=player.has_longest_road,
        has_largest_army=player.has_largest_army,
        resource_card_count=dev_cards.hand_total(player.hand),
        dev_card_count=dev_cards.total_owned(player),
        hand=dict(player.hand) if is_viewer else None,
        dev_cards=dict(player.dev_cards) if is_viewer else None,
    )


def _blackjack_round_view(round_: BlackjackRoundState | None) -> BlackjackRoundView | None:
    """Mask `GameState.blackjack_round` for the wire: the dealer's hole
    card is split out and hidden until `dealer_hole_card_revealed`, and
    the round's `deck` is dropped entirely (never exposed to any
    recipient -- see `BlackjackRoundView`'s docstring). Identical for
    every viewer; unlike `MaskedPlayerView`, there's no per-recipient
    unmasking here (bettors' bets/hands are always fully public, per the
    plan's "real table" rule).
    """
    if round_ is None:
        return None
    up_card = round_.dealer_hand[0] if len(round_.dealer_hand) > 0 else None
    hole_card = (
        round_.dealer_hand[1]
        if round_.dealer_hole_card_revealed and len(round_.dealer_hand) > 1
        else None
    )
    return BlackjackRoundView(
        dealer_id=round_.dealer_id,
        dealer_up_card=up_card,
        dealer_hole_card=hole_card,
        dealer_hole_card_revealed=round_.dealer_hole_card_revealed,
        dealer_hand=list(round_.dealer_hand) if round_.dealer_hole_card_revealed else [],
        status=round_.status,
        responses_pending=list(round_.responses_pending),
        participants={
            player_id: BlackjackParticipantView(
                stake=participant.stake, hand=list(participant.hand), status=participant.status
            )
            for player_id, participant in round_.participants.items()
        },
        bettor_queue=list(round_.bettor_queue),
    )


def to_client_view(state: GameState, viewer_player_id: str) -> ClientGameStateView:
    """Build the masked `ClientGameStateView` of `state` as seen by
    `viewer_player_id`. Safe to send to that player's socket as-is (the
    payload of `STATE_SNAPSHOT`/`GAME_STARTED`'s accompanying snapshot).

    `viewer_player_id` need not currently be seated (e.g. it's not
    validated against `state.players` here) -- callers are expected to
    only call this for actually-seated players, but nothing here would
    misbehave otherwise beyond `viewer_player_id`'s own entry (if any)
    simply never matching and so no `hand`/`dev_cards` ever being
    unmasked, which is the safe direction to fail in.
    """
    return ClientGameStateView(
        room_code=state.room_code,
        phase=state.phase,
        settings=state.settings,
        turn_order=list(state.turn_order),
        current_player_index=state.current_player_index,
        last_dice_roll=state.last_dice_roll,
        board=_wire_board(state),
        bank_resource_counts=dict(state.bank.resources),
        bank_dev_card_count=len(state.bank.dev_card_pile),
        players={
            player_id: _masked_player_view(state, player_id, viewer_player_id)
            for player_id in state.players
        },
        longest_road_holder=state.longest_road_holder,
        largest_army_holder=state.largest_army_holder,
        pending=state.pending,
        special_build_queue=list(state.special_build_queue),
        blackjack_round=_blackjack_round_view(state.blackjack_round),
        rush_pending_discard=state.rush_pending_discard,
        rush_pending_robber=state.rush_pending_robber,
        last_dice_roll_ts=state.last_dice_roll_ts,
        viewer_player_id=viewer_player_id,
    )
