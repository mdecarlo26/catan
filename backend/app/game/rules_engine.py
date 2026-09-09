"""Central gate for every in-game action: `validate()` -> `apply()`.

``validate(state, actor_id, action)`` checks the action is legal without
mutating anything (raises `RuleViolation` if not); ``apply(state,
actor_id, action)`` performs the mutation and returns the list of
`RuleEvent`s it produced. Callers that just want both are one line:
``validate_and_apply(state, actor_id, action)``.

Per the plan, `validate()` always checks
`app.game.turn_state_machine.is_action_allowed()` first -- is this
action type even legal during the current `Phase` -- before any
rule-specific validation (turn ownership, resource costs, board
legality, ...).

This module only handles actual gameplay actions (`ROLL_DICE` through
`CHAT_MESSAGE` in `ActionType`, excluding the room-lifecycle actions
`JOIN_ROOM`/`LEAVE_ROOM`/`KICK_PLAYER`/`UPDATE_SETTINGS`/`START_GAME`,
which are handled by `app.core.room` and never reach here -- see
`app.game.turn_state_machine`'s module docstring).

Events returned by `apply()` are decoupled from the WS envelope: `seq`
and `ts` are assigned by `app.core.room.Room` when it actually broadcasts
something (see `app.protocol.events`'s envelope docstring), not here.
`RuleEvent` below pairs an `EventType` with its (unwrapped) payload model
from `app.protocol.events`; the caller is responsible for stamping
`seq`/`ts` and constructing the concrete `ServerEvent` subclass.

Board-geometry calls (`get_adjacent_vertices`, `get_adjacent_edges`,
`vertex_neighbors`, `edge_endpoints`) are always made through the
`board_mod` module reference (never via a bare `from app.game.board
import get_adjacent_vertices`-style name import) so tests can
`monkeypatch.setattr(board_mod, "get_adjacent_vertices", ...)` -- the
real geometry implementation is Wave 1 work happening on a parallel
branch and isn't available in this worktree.
"""

from __future__ import annotations

import random
import uuid
from dataclasses import dataclass
from typing import Callable

from pydantic import BaseModel

from app.game import board as board_mod
from app.game import dev_cards, scoring
from app.game.actions import (
    ActionType,
    BankTradePayload,
    BuildCityPayload,
    BuildRoadPayload,
    BuildSettlementPayload,
    ClientAction,
    DiscardCardsPayload,
    MoveRobberPayload,
    PlayDevCardPayload,
    PlayNukePayload,
    PortTradePayload,
    ProposeTradePayload,
    RespondTradePayload,
    StealResourcePayload,
)
from app.game.board import (
    BuildingType,
    EdgeId,
    PlayerId,
    PortType,
    Terrain,
    VertexBuilding,
    VertexId,
)
from app.game.players import DevCardType, ResourceHand, ResourceType
from app.game.rules import nuke_mode, robber_strategies, setup_strategies
from app.game.state import (
    AwaitingDiscard,
    AwaitingRobberPlacement,
    AwaitingSteal,
    AwaitingTradeResponse,
    GameState,
    Phase,
)
from app.game import turn_state_machine
from app.protocol.events import (
    DevCardCountChangedPayload,
    DiceRolledPayload,
    DiscardRequiredPayload,
    EventType,
    GameOverPayload,
    LargestArmyChangedPayload,
    LongestRoadChangedPayload,
    NukeDroppedPayload,
    ResourceStolenPayload,
    ResourcesDistributedPayload,
    RobberMovedPayload,
    TradeOfferedPayload,
    TradeResolvedPayload,
)


class RuleViolation(Exception):
    """Raised by `validate()` (or, defensively, `apply()`) for any illegal
    action. `code` is a short machine-readable tag suitable for
    `app.protocol.events.ErrorPayload.code`; `message` is human-readable.
    """

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class RuleEvent:
    """One event produced by `apply()`: an `EventType` plus its unwrapped
    payload model. See the module docstring for why `seq`/`ts` aren't
    included here.
    """

    type: EventType
    payload: BaseModel


def _event(event_type: EventType, payload: BaseModel) -> RuleEvent:
    return RuleEvent(type=event_type, payload=payload)


# ---------------------------------------------------------------------
# Resource costs
# ---------------------------------------------------------------------

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
DEV_CARD_COST = dev_cards.DEV_CARD_COST

BANK_TRADE_RATE = 4

TERRAIN_RESOURCE: dict[Terrain, ResourceType] = {
    Terrain.FOREST: ResourceType.LUMBER,
    Terrain.HILLS: ResourceType.BRICK,
    Terrain.MOUNTAINS: ResourceType.ORE,
    Terrain.FIELDS: ResourceType.GRAIN,
    Terrain.PASTURE: ResourceType.WOOL,
}


# ---------------------------------------------------------------------
# Small shared helpers
# ---------------------------------------------------------------------


def _has_resources(hand: ResourceHand, cost: dict[ResourceType, int]) -> bool:
    return all(hand.get(resource, 0) >= amount for resource, amount in cost.items())


def _pay(hand: ResourceHand, bank: ResourceHand, cost: dict[ResourceType, int]) -> None:
    for resource, amount in cost.items():
        if amount <= 0:
            continue
        hand[resource] = hand.get(resource, 0) - amount
        bank[resource] = bank.get(resource, 0) + amount


def _current_player(state: GameState) -> PlayerId:
    return state.turn_order[state.current_player_index]


def _require_current_player(state: GameState, actor_id: PlayerId) -> None:
    if actor_id != _current_player(state):
        raise RuleViolation("not_your_turn", "It is not your turn.")


def _edges_incident_to_vertex(board, vertex_id: VertexId) -> list[EdgeId]:
    """Edges touching `vertex_id`, derived only from the frozen `board.py`
    signatures (there's no direct "edges of a vertex" function): for each
    hex making up the vertex id, gather that hex's adjacent edges, then
    keep only the ones whose endpoints actually include this vertex.
    """
    candidates: set[EdgeId] = set()
    for hex_coord in vertex_id:
        candidates.update(board_mod.get_adjacent_edges(hex_coord))
    return [edge_id for edge_id in candidates if vertex_id in board_mod.edge_endpoints(edge_id)]


def _vertex_free_and_far_enough(board, vertex_id: VertexId) -> None:
    if vertex_id in board.buildings:
        raise RuleViolation("vertex_occupied", "That vertex already has a building.")
    for neighbor in board_mod.vertex_neighbors(vertex_id):
        if neighbor in board.buildings:
            raise RuleViolation(
                "distance_rule", "Too close to an existing settlement/city."
            )


def _player_has_connection_to_vertex(board, player_id: PlayerId, vertex_id: VertexId) -> bool:
    return any(
        board.roads.get(edge_id) == player_id
        for edge_id in _edges_incident_to_vertex(board, vertex_id)
    )


def _player_has_connection_to_edge(board, player_id: PlayerId, edge_id: EdgeId) -> bool:
    v1, v2 = board_mod.edge_endpoints(edge_id)
    for vertex_id in (v1, v2):
        building = board.buildings.get(vertex_id)
        if building is not None and building.player_id == player_id:
            return True
        if _player_has_connection_to_vertex(board, player_id, vertex_id):
            return True
    return False


def _cancel_pending_trade_if_any(state: GameState) -> list[RuleEvent]:
    if not isinstance(state.pending, AwaitingTradeResponse):
        return []
    trade_id = state.pending.trade_id
    state.pending = None
    return [
        _event(
            EventType.TRADE_RESOLVED,
            TradeResolvedPayload(trade_id=trade_id, status="cancelled", accepted_by=None),
        )
    ]


def _check_win_condition(state: GameState) -> list[RuleEvent]:
    if state.phase == Phase.GAME_OVER:
        return []
    for player_id in state.turn_order:
        total = scoring.total_victory_points_including_hidden(state, player_id)
        if total >= state.settings.victory_points_target:
            state.phase = Phase.GAME_OVER
            final_scores = {
                pid: scoring.total_victory_points_including_hidden(state, pid)
                for pid in state.players
            }
            return [
                _event(
                    EventType.GAME_OVER,
                    GameOverPayload(winner=player_id, final_scores=final_scores),
                )
            ]
    return []


# ---------------------------------------------------------------------
# Setup-phase helpers
# ---------------------------------------------------------------------


def _validate_setup_turn(state: GameState, actor_id: PlayerId, expected_action: ActionType) -> None:
    strategy = setup_strategies.SETUP_STRATEGIES[state.settings.rush_mode]
    expected_player, expected_action_type = strategy.get_next_setup_action(state)
    if actor_id != expected_player:
        raise RuleViolation("not_your_turn", "It is not your turn to place during setup.")
    if expected_action_type != expected_action:
        raise RuleViolation(
            "wrong_setup_action",
            f"Expected {expected_action_type.value} next, not {expected_action.value}.",
        )


def _grant_setup_resources(state: GameState, vertex_id: VertexId) -> ResourceHand:
    """Grant starting resources for a player's *second* setup settlement:
    one card for each resource-producing hex adjacent to it (the vertex
    id itself is the tuple of adjacent hexes, so no `board_mod` call is
    needed here). Standard rule: no resources for the first settlement.
    """
    board = state.board
    player_id = board.buildings[vertex_id].player_id
    granted: ResourceHand = {}
    for hex_coord in vertex_id:
        tile = board.hexes.get(hex_coord)
        if tile is None or hex_coord == board.robber_hex:
            continue
        resource = TERRAIN_RESOURCE.get(tile.terrain)
        if resource is None:
            continue
        state.players[player_id].hand[resource] = state.players[player_id].hand.get(resource, 0) + 1
        state.bank.resources[resource] = state.bank.resources.get(resource, 0) - 1
        granted[resource] = granted.get(resource, 0) + 1
    return granted


def _maybe_complete_setup(state: GameState) -> None:
    strategy = setup_strategies.SETUP_STRATEGIES[state.settings.rush_mode]
    total_expected = 2 * len(state.turn_order)
    settlements_built = sum(
        1
        for building in state.board.buildings.values()
        if building.building_type == BuildingType.SETTLEMENT
    )
    roads_built = len(state.board.roads)
    if settlements_built >= total_expected and roads_built >= total_expected:
        strategy.on_setup_complete(state)


# ---------------------------------------------------------------------
# ROLL_DICE
# ---------------------------------------------------------------------


def _discard_requirements(state: GameState) -> dict[PlayerId, int]:
    limit = state.settings.discard_limit
    required: dict[PlayerId, int] = {}
    for player_id, player in state.players.items():
        total = dev_cards.hand_total(player.hand)
        if total > limit:
            required[player_id] = total // 2
    return required


def _distribute_resources(state: GameState, total: int) -> dict[PlayerId, ResourceHand]:
    board = state.board
    demand: dict[PlayerId, dict[ResourceType, int]] = {pid: {} for pid in state.players}
    resource_demand_total: dict[ResourceType, int] = {r: 0 for r in ResourceType}

    for hex_coord, tile in board.hexes.items():
        if tile.number_token != total or hex_coord == board.robber_hex:
            continue
        resource = TERRAIN_RESOURCE.get(tile.terrain)
        if resource is None:
            continue
        for vertex_id in board_mod.get_adjacent_vertices(hex_coord):
            building = board.buildings.get(vertex_id)
            if building is None:
                continue
            amount = 2 if building.building_type == BuildingType.CITY else 1
            demand[building.player_id][resource] = demand[building.player_id].get(resource, 0) + amount
            resource_demand_total[resource] += amount

    # Bank-shortage rule: if the bank can't fully satisfy total demand for
    # a resource this roll, nobody gets any of that resource this roll.
    payable = {
        resource
        for resource, needed in resource_demand_total.items()
        if needed > 0 and needed <= state.bank.resources.get(resource, 0)
    }

    distribution: dict[PlayerId, ResourceHand] = {}
    for player_id, per_resource in demand.items():
        granted: ResourceHand = {}
        for resource, amount in per_resource.items():
            if resource not in payable:
                continue
            state.players[player_id].hand[resource] = state.players[player_id].hand.get(resource, 0) + amount
            state.bank.resources[resource] -= amount
            granted[resource] = amount
        if granted:
            distribution[player_id] = granted
    return distribution


def _validate_roll_dice(state: GameState, actor_id: PlayerId, payload) -> None:
    _require_current_player(state, actor_id)
    if state.pending is not None:
        raise RuleViolation("action_pending", "Cannot roll while another action is pending.")


def _apply_roll_dice(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    die1 = random.randint(1, 6)
    die2 = random.randint(1, 6)
    total = die1 + die2
    state.last_dice_roll = (die1, die2)

    events = [
        _event(
            EventType.DICE_ROLLED,
            DiceRolledPayload(player_id=actor_id, die1=die1, die2=die2, total=total),
        )
    ]

    if total == 7:
        required = _discard_requirements(state)
        if required:
            state.phase = Phase.ROBBER_DISCARD
            state.pending = AwaitingDiscard(required_counts=required)
            events.append(
                _event(EventType.DISCARD_REQUIRED, DiscardRequiredPayload(required_counts=required))
            )
        else:
            state.phase = Phase.ROBBER_MOVE
            state.pending = AwaitingRobberPlacement(actor=actor_id, reason="dice_roll")
    else:
        distribution = _distribute_resources(state, total)
        state.phase = Phase.MAIN
        events.append(
            _event(EventType.RESOURCES_DISTRIBUTED, ResourcesDistributedPayload(distribution=distribution))
        )

    return events


# ---------------------------------------------------------------------
# BUILD_SETTLEMENT / BUILD_ROAD / BUILD_CITY
# ---------------------------------------------------------------------


def _validate_build_settlement(state: GameState, actor_id: PlayerId, payload: BuildSettlementPayload) -> None:
    if state.phase == Phase.SETUP:
        _validate_setup_turn(state, actor_id, ActionType.BUILD_SETTLEMENT)
    else:
        _require_current_player(state, actor_id)
        if not _has_resources(state.players[actor_id].hand, SETTLEMENT_COST):
            raise RuleViolation("insufficient_resources", "Not enough resources for a settlement.")

    player = state.players[actor_id]
    if player.settlements_remaining <= 0:
        raise RuleViolation("no_settlements_left", "No settlement pieces left to build.")

    _vertex_free_and_far_enough(state.board, payload.vertex_id)

    if state.phase == Phase.MAIN and not _player_has_connection_to_vertex(
        state.board, actor_id, payload.vertex_id
    ):
        raise RuleViolation("not_connected", "Settlement must connect to your own road network.")


def _apply_build_settlement(state: GameState, actor_id: PlayerId, payload: BuildSettlementPayload) -> list[RuleEvent]:
    board = state.board
    player = state.players[actor_id]
    is_setup = state.phase == Phase.SETUP

    settlements_before = sum(
        1
        for building in board.buildings.values()
        if building.player_id == actor_id and building.building_type == BuildingType.SETTLEMENT
    )

    board.buildings[payload.vertex_id] = VertexBuilding(
        player_id=actor_id, building_type=BuildingType.SETTLEMENT
    )
    player.settlements_remaining -= 1

    events: list[RuleEvent] = []

    if is_setup:
        if settlements_before == 1:
            granted = _grant_setup_resources(state, payload.vertex_id)
            if granted:
                events.append(
                    _event(
                        EventType.RESOURCES_DISTRIBUTED,
                        ResourcesDistributedPayload(distribution={actor_id: granted}),
                    )
                )
    else:
        _pay(player.hand, state.bank.resources, SETTLEMENT_COST)

    scoring.recompute_victory_points(state)

    if is_setup:
        _maybe_complete_setup(state)

    events.extend(_check_win_condition(state))
    return events


def _validate_road_slot(state: GameState, actor_id: PlayerId, edge_id: EdgeId) -> None:
    player = state.players[actor_id]
    if player.roads_remaining <= 0:
        raise RuleViolation("no_roads_left", "No road pieces left to build.")
    if edge_id in state.board.roads:
        raise RuleViolation("edge_occupied", "That edge already has a road.")
    if not _player_has_connection_to_edge(state.board, actor_id, edge_id):
        raise RuleViolation(
            "not_connected", "Road must connect to your existing network or a settlement/city."
        )


def _place_road(state: GameState, actor_id: PlayerId, edge_id: EdgeId, pay: bool) -> list[RuleEvent]:
    board = state.board
    player = state.players[actor_id]
    board.roads[edge_id] = actor_id
    player.roads_remaining -= 1
    if pay:
        _pay(player.hand, state.bank.resources, ROAD_COST)

    previous_holder = state.longest_road_holder
    scoring.recompute_longest_road(state)

    events: list[RuleEvent] = []
    if state.longest_road_holder != previous_holder:
        events.append(
            _event(
                EventType.LONGEST_ROAD_CHANGED,
                LongestRoadChangedPayload(
                    new_holder=state.longest_road_holder, previous_holder=previous_holder
                ),
            )
        )
    return events


def _validate_build_road(state: GameState, actor_id: PlayerId, payload: BuildRoadPayload) -> None:
    if state.phase == Phase.SETUP:
        _validate_setup_turn(state, actor_id, ActionType.BUILD_ROAD)
    else:
        _require_current_player(state, actor_id)
        if not _has_resources(state.players[actor_id].hand, ROAD_COST):
            raise RuleViolation("insufficient_resources", "Not enough resources for a road.")
    _validate_road_slot(state, actor_id, payload.edge_id)


def _apply_build_road(state: GameState, actor_id: PlayerId, payload: BuildRoadPayload) -> list[RuleEvent]:
    is_setup = state.phase == Phase.SETUP
    events = _place_road(state, actor_id, payload.edge_id, pay=not is_setup)

    if is_setup:
        _maybe_complete_setup(state)

    events.extend(_check_win_condition(state))
    return events


def _validate_build_city(state: GameState, actor_id: PlayerId, payload: BuildCityPayload) -> None:
    _require_current_player(state, actor_id)
    player = state.players[actor_id]
    if player.cities_remaining <= 0:
        raise RuleViolation("no_cities_left", "No city pieces left to build.")
    if not _has_resources(player.hand, CITY_COST):
        raise RuleViolation("insufficient_resources", "Not enough resources for a city.")
    building = state.board.buildings.get(payload.vertex_id)
    if (
        building is None
        or building.player_id != actor_id
        or building.building_type != BuildingType.SETTLEMENT
    ):
        raise RuleViolation("invalid_target", "Must upgrade your own settlement.")


def _apply_build_city(state: GameState, actor_id: PlayerId, payload: BuildCityPayload) -> list[RuleEvent]:
    player = state.players[actor_id]
    state.board.buildings[payload.vertex_id] = VertexBuilding(
        player_id=actor_id, building_type=BuildingType.CITY
    )
    player.cities_remaining -= 1
    player.settlements_remaining += 1
    _pay(player.hand, state.bank.resources, CITY_COST)
    scoring.recompute_victory_points(state)
    return _check_win_condition(state)


# ---------------------------------------------------------------------
# BUY_DEV_CARD / PLAY_DEV_CARD
# ---------------------------------------------------------------------


def _validate_buy_dev_card(state: GameState, actor_id: PlayerId, payload) -> None:
    _require_current_player(state, actor_id)
    if not state.bank.dev_card_pile:
        raise RuleViolation("dev_card_pile_empty", "No development cards left in the bank.")
    if not _has_resources(state.players[actor_id].hand, DEV_CARD_COST):
        raise RuleViolation("insufficient_resources", "Not enough resources to buy a dev card.")


def _apply_buy_dev_card(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    player = state.players[actor_id]
    card = state.bank.dev_card_pile.pop()
    player.dev_cards_bought_this_turn[card] = player.dev_cards_bought_this_turn.get(card, 0) + 1
    _pay(player.hand, state.bank.resources, DEV_CARD_COST)

    events = [
        _event(
            EventType.DEV_CARD_COUNT_CHANGED,
            DevCardCountChangedPayload(
                player_id=actor_id,
                player_dev_card_count=dev_cards.total_owned(player),
                bank_dev_card_count=len(state.bank.dev_card_pile),
            ),
        )
    ]
    # A bought Victory Point card may be revealed the same turn to win.
    events.extend(_check_win_condition(state))
    return events


def _validate_play_dev_card(state: GameState, actor_id: PlayerId, payload: PlayDevCardPayload) -> None:
    _require_current_player(state, actor_id)

    if payload.card_type == DevCardType.VICTORY_POINT:
        raise RuleViolation(
            "cannot_play_victory_point",
            "Victory point cards can't be played; they count automatically toward your win check.",
        )
    if state.players[actor_id].dev_cards.get(payload.card_type, 0) < 1:
        raise RuleViolation(
            "card_not_owned", "You don't own that dev card (or it was bought this turn)."
        )

    if payload.card_type == DevCardType.MONOPOLY:
        if payload.monopoly_resource is None:
            raise RuleViolation("missing_payload", "monopoly_resource is required.")

    elif payload.card_type == DevCardType.YEAR_OF_PLENTY:
        resources = payload.year_of_plenty_resources
        if not resources or len(resources) != 2:
            raise RuleViolation(
                "missing_payload", "year_of_plenty_resources must have exactly 2 entries."
            )
        needed: dict[ResourceType, int] = {}
        for resource in resources:
            needed[resource] = needed.get(resource, 0) + 1
        for resource, amount in needed.items():
            if state.bank.resources.get(resource, 0) < amount:
                raise RuleViolation("bank_shortage", f"Bank doesn't have enough {resource.value}.")

    elif payload.card_type == DevCardType.ROAD_BUILDING:
        edges = payload.road_building_edges
        if not edges or not (1 <= len(edges) <= 2):
            raise RuleViolation(
                "missing_payload", "road_building_edges must have 1-2 entries."
            )
        if len(edges) > state.players[actor_id].roads_remaining:
            raise RuleViolation("no_roads_left", "Not enough road pieces left.")
        seen: set[EdgeId] = set()
        for edge_id in edges:
            if edge_id in state.board.roads or edge_id in seen:
                raise RuleViolation("edge_occupied", "That edge already has a road.")
            seen.add(edge_id)
        # Connectivity is re-checked incrementally at apply time, since the
        # second free road may rely on the first having just been placed.


def _apply_play_dev_card(state: GameState, actor_id: PlayerId, payload: PlayDevCardPayload) -> list[RuleEvent]:
    player = state.players[actor_id]
    player.dev_cards[payload.card_type] = player.dev_cards.get(payload.card_type, 0) - 1
    events: list[RuleEvent] = []

    if payload.card_type == DevCardType.KNIGHT:
        player.knights_played += 1
        previous_holder = state.largest_army_holder
        scoring.recompute_largest_army(state)
        if state.largest_army_holder != previous_holder:
            events.append(
                _event(
                    EventType.LARGEST_ARMY_CHANGED,
                    LargestArmyChangedPayload(
                        new_holder=state.largest_army_holder, previous_holder=previous_holder
                    ),
                )
            )
        state.phase = Phase.ROBBER_MOVE
        state.pending = AwaitingRobberPlacement(actor=actor_id, reason="knight_card")

    elif payload.card_type == DevCardType.ROAD_BUILDING:
        for edge_id in payload.road_building_edges:
            _validate_road_slot(state, actor_id, edge_id)
            events.extend(_place_road(state, actor_id, edge_id, pay=False))

    elif payload.card_type == DevCardType.YEAR_OF_PLENTY:
        dev_cards.year_of_plenty_grant(state.bank, player, payload.year_of_plenty_resources)

    elif payload.card_type == DevCardType.MONOPOLY:
        dev_cards.monopoly_transfer(state.players, actor_id, payload.monopoly_resource)

    scoring.recompute_victory_points(state)
    events.extend(_check_win_condition(state))
    return events


# ---------------------------------------------------------------------
# BANK_TRADE / PORT_TRADE
# ---------------------------------------------------------------------


def _validate_trade_shape(
    offered: ResourceHand, requested: ResourceHand, rate_for: dict[ResourceType, int]
) -> None:
    offered_positive = {r: a for r, a in offered.items() if a > 0}
    requested_positive = {r: a for r, a in requested.items() if a > 0}
    if not offered_positive or not requested_positive:
        raise RuleViolation("invalid_trade", "Both offered and requested must be non-empty.")

    total_paid_units = 0
    for resource, amount in offered_positive.items():
        rate = rate_for[resource]
        if amount % rate != 0:
            raise RuleViolation(
                "invalid_ratio", f"{resource.value} must be offered in multiples of {rate}."
            )
        total_paid_units += amount // rate

    total_requested = sum(requested_positive.values())
    if total_requested != total_paid_units:
        raise RuleViolation("invalid_ratio", "Requested amount does not match the trade rate.")

    if set(offered_positive) & set(requested_positive):
        raise RuleViolation("invalid_trade", "Cannot request a resource you're also offering.")


def _validate_bank_trade(state: GameState, actor_id: PlayerId, payload: BankTradePayload) -> None:
    _require_current_player(state, actor_id)
    rate_for = {resource: BANK_TRADE_RATE for resource in ResourceType}
    _validate_trade_shape(payload.offered, payload.requested, rate_for)
    if not _has_resources(state.players[actor_id].hand, payload.offered):
        raise RuleViolation("insufficient_resources", "You don't hold the offered resources.")
    if not _has_resources(state.bank.resources, payload.requested):
        raise RuleViolation("bank_shortage", "Bank doesn't hold enough of the requested resources.")


def _apply_bank_trade(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    player = state.players[actor_id]
    _pay(player.hand, state.bank.resources, {r: a for r, a in payload.offered.items() if a > 0})
    for resource, amount in payload.requested.items():
        if amount <= 0:
            continue
        state.bank.resources[resource] -= amount
        player.hand[resource] = player.hand.get(resource, 0) + amount
    return []


def _best_port_rate(board, player_id: PlayerId, resource: ResourceType) -> int:
    best = BANK_TRADE_RATE
    for port in board.ports:
        owns = any(
            (board.buildings.get(v) is not None and board.buildings[v].player_id == player_id)
            for v in port.vertices
        )
        if not owns:
            continue
        if port.port_type == PortType.GENERIC:
            best = min(best, 3)
        elif port.port_type.value == resource.value:
            best = min(best, 2)
    return best


def _validate_port_trade(state: GameState, actor_id: PlayerId, payload: PortTradePayload) -> None:
    _require_current_player(state, actor_id)
    rate_for = {
        resource: _best_port_rate(state.board, actor_id, resource) for resource in ResourceType
    }
    _validate_trade_shape(payload.offered, payload.requested, rate_for)
    if not _has_resources(state.players[actor_id].hand, payload.offered):
        raise RuleViolation("insufficient_resources", "You don't hold the offered resources.")
    if not _has_resources(state.bank.resources, payload.requested):
        raise RuleViolation("bank_shortage", "Bank doesn't hold enough of the requested resources.")


def _apply_port_trade(state: GameState, actor_id: PlayerId, payload: PortTradePayload) -> list[RuleEvent]:
    return _apply_bank_trade(state, actor_id, payload)


# ---------------------------------------------------------------------
# PROPOSE_TRADE / RESPOND_TRADE
# ---------------------------------------------------------------------


def _validate_propose_trade(state: GameState, actor_id: PlayerId, payload: ProposeTradePayload) -> None:
    _require_current_player(state, actor_id)
    if state.pending is not None:
        raise RuleViolation("action_pending", "Another action is already pending.")
    if not any(amount > 0 for amount in payload.offered.values()):
        raise RuleViolation("invalid_trade", "Must offer at least one resource.")
    if not _has_resources(state.players[actor_id].hand, payload.offered):
        raise RuleViolation("insufficient_resources", "You don't hold the offered resources.")
    if payload.target_player_ids is not None:
        for target in payload.target_player_ids:
            if target == actor_id or target not in state.players:
                raise RuleViolation("invalid_target", "Invalid trade target.")


def _apply_propose_trade(state: GameState, actor_id: PlayerId, payload: ProposeTradePayload) -> list[RuleEvent]:
    targets = payload.target_player_ids or [pid for pid in state.turn_order if pid != actor_id]
    trade_id = str(uuid.uuid4())
    state.pending = AwaitingTradeResponse(
        trade_id=trade_id,
        proposer=actor_id,
        offered=payload.offered,
        requested=payload.requested,
        responses_pending=list(targets),
    )
    return [
        _event(
            EventType.TRADE_OFFERED,
            TradeOfferedPayload(
                trade_id=trade_id,
                proposer=actor_id,
                offered=payload.offered,
                requested=payload.requested,
                target_player_ids=payload.target_player_ids,
            ),
        )
    ]


def _validate_respond_trade(state: GameState, actor_id: PlayerId, payload: RespondTradePayload) -> None:
    pending = state.pending
    if not isinstance(pending, AwaitingTradeResponse) or pending.trade_id != payload.trade_id:
        raise RuleViolation("no_such_trade", "No matching trade is pending.")
    if actor_id not in pending.responses_pending:
        raise RuleViolation("not_eligible", "You are not eligible to respond to this trade.")
    if payload.accept:
        if not _has_resources(state.players[actor_id].hand, pending.requested):
            raise RuleViolation("insufficient_resources", "You don't hold the requested resources.")
        if not _has_resources(state.players[pending.proposer].hand, pending.offered):
            raise RuleViolation(
                "insufficient_resources", "Proposer no longer holds the offered resources."
            )


def _apply_respond_trade(state: GameState, actor_id: PlayerId, payload: RespondTradePayload) -> list[RuleEvent]:
    pending = state.pending
    assert isinstance(pending, AwaitingTradeResponse)

    if payload.accept:
        proposer = state.players[pending.proposer]
        responder = state.players[actor_id]
        for resource, amount in pending.offered.items():
            if amount <= 0:
                continue
            proposer.hand[resource] -= amount
            responder.hand[resource] = responder.hand.get(resource, 0) + amount
        for resource, amount in pending.requested.items():
            if amount <= 0:
                continue
            responder.hand[resource] -= amount
            proposer.hand[resource] = proposer.hand.get(resource, 0) + amount
        state.pending = None
        return [
            _event(
                EventType.TRADE_RESOLVED,
                TradeResolvedPayload(trade_id=pending.trade_id, status="accepted", accepted_by=actor_id),
            )
        ]

    pending.responses_pending.remove(actor_id)
    if not pending.responses_pending:
        state.pending = None
        return [
            _event(
                EventType.TRADE_RESOLVED,
                TradeResolvedPayload(trade_id=pending.trade_id, status="declined", accepted_by=None),
            )
        ]
    return []


# ---------------------------------------------------------------------
# MOVE_ROBBER / STEAL_RESOURCE / DISCARD_CARDS
# ---------------------------------------------------------------------


def _validate_move_robber(state: GameState, actor_id: PlayerId, payload: MoveRobberPayload) -> None:
    pending = state.pending
    if not isinstance(pending, AwaitingRobberPlacement):
        raise RuleViolation("no_pending_robber_move", "The robber isn't waiting to be moved.")
    if actor_id != pending.actor:
        raise RuleViolation("not_your_turn", "It is not your turn to move the robber.")
    if payload.hex not in state.board.hexes:
        raise RuleViolation("invalid_hex", "That hex isn't on the board.")
    if payload.hex == state.board.robber_hex:
        raise RuleViolation("must_move", "The robber must move to a different hex.")


def _apply_move_robber(state: GameState, actor_id: PlayerId, payload: MoveRobberPayload) -> list[RuleEvent]:
    state.board.robber_hex = payload.hex
    events: list[RuleEvent] = [
        _event(EventType.ROBBER_MOVED, RobberMovedPayload(actor=actor_id, hex=payload.hex))
    ]

    vertex_owners: list[PlayerId] = []
    for vertex_id in board_mod.get_adjacent_vertices(payload.hex):
        building = state.board.buildings.get(vertex_id)
        if building is None or building.player_id == actor_id:
            continue
        if building.player_id in vertex_owners:
            continue
        vertex_owners.append(building.player_id)

    if state.settings.friendly_robber:
        # Blocks production (the robber_hex move above is unconditional)
        # but never yields a steal -- see
        # `robber_strategies.friendly_robber_steal_candidates`.
        candidates = robber_strategies.friendly_robber_steal_candidates(
            state.players, vertex_owners
        )
    else:
        candidates = [
            pid
            for pid in vertex_owners
            if dev_cards.hand_total(state.players[pid].hand) > 0
        ]

    if not candidates:
        state.pending = None
        state.phase = Phase.MAIN
    elif len(candidates) == 1:
        stolen = robber_strategies.normal_steal(state.players, actor_id, candidates[0])
        events.append(
            _event(
                EventType.RESOURCE_STOLEN,
                ResourceStolenPayload(actor=actor_id, victim=candidates[0], resource=stolen),
            )
        )
        state.pending = None
        state.phase = Phase.MAIN
    else:
        state.pending = AwaitingSteal(actor=actor_id, candidate_targets=candidates)
        # Phase stays ROBBER_MOVE until STEAL_RESOURCE resolves it.

    events.extend(_check_win_condition(state))
    return events


def _validate_steal_resource(state: GameState, actor_id: PlayerId, payload: StealResourcePayload) -> None:
    pending = state.pending
    if not isinstance(pending, AwaitingSteal):
        raise RuleViolation("no_pending_steal", "No steal is currently pending.")
    if actor_id != pending.actor:
        raise RuleViolation("not_your_turn", "It is not your turn to steal.")
    if payload.target_player_id not in pending.candidate_targets:
        raise RuleViolation("invalid_target", "That player isn't a valid steal target.")


def _apply_steal_resource(state: GameState, actor_id: PlayerId, payload: StealResourcePayload) -> list[RuleEvent]:
    stolen = robber_strategies.normal_steal(state.players, actor_id, payload.target_player_id)
    state.pending = None
    state.phase = Phase.MAIN
    events = [
        _event(
            EventType.RESOURCE_STOLEN,
            ResourceStolenPayload(actor=actor_id, victim=payload.target_player_id, resource=stolen),
        )
    ]
    events.extend(_check_win_condition(state))
    return events


def _validate_discard_cards(state: GameState, actor_id: PlayerId, payload: DiscardCardsPayload) -> None:
    pending = state.pending
    if not isinstance(pending, AwaitingDiscard):
        raise RuleViolation("no_discard_owed", "No discard is currently owed.")
    owed = pending.required_counts.get(actor_id)
    if owed is None:
        raise RuleViolation("no_discard_owed", "You don't owe a discard.")
    total = sum(amount for amount in payload.resources.values() if amount > 0)
    if total != owed:
        raise RuleViolation("wrong_discard_count", f"Must discard exactly {owed} cards.")
    if not _has_resources(state.players[actor_id].hand, payload.resources):
        raise RuleViolation("insufficient_resources", "You don't hold that many of those resources.")


def _apply_discard_cards(state: GameState, actor_id: PlayerId, payload: DiscardCardsPayload) -> list[RuleEvent]:
    pending = state.pending
    assert isinstance(pending, AwaitingDiscard)
    player = state.players[actor_id]

    for resource, amount in payload.resources.items():
        if amount <= 0:
            continue
        player.hand[resource] -= amount
        state.bank.resources[resource] = state.bank.resources.get(resource, 0) + amount

    del pending.required_counts[actor_id]
    events = [
        _event(
            EventType.DISCARD_REQUIRED,
            DiscardRequiredPayload(required_counts=dict(pending.required_counts)),
        )
    ]

    if not pending.required_counts:
        roller = _current_player(state)
        state.phase = Phase.ROBBER_MOVE
        state.pending = AwaitingRobberPlacement(actor=roller, reason="dice_roll")

    return events


# ---------------------------------------------------------------------
# PLAY_NUKE
# ---------------------------------------------------------------------


def _validate_play_nuke(state: GameState, actor_id: PlayerId, payload: PlayNukePayload) -> None:
    if not state.settings.nuke_mode:
        raise RuleViolation("nuke_disabled", "Nuke mode is not enabled for this game.")
    _require_current_player(state, actor_id)
    if payload.target_player_id == actor_id:
        raise RuleViolation("invalid_target", "Cannot nuke yourself.")
    if payload.target_player_id not in state.players:
        raise RuleViolation("invalid_target", "Unknown target player.")
    if not nuke_mode.player_has_nuke_hand(state.players[actor_id].hand):
        raise RuleViolation(
            "insufficient_resources", "Need at least 2 of each resource type to play Nuke."
        )
    try:
        nuke_mode.validate_nuke_target(
            state.board, payload.target_player_id, payload.target_vertex_id, payload.target_edge_id
        )
    except ValueError as exc:
        raise RuleViolation("invalid_target", str(exc)) from exc


def _apply_play_nuke(state: GameState, actor_id: PlayerId, payload: PlayNukePayload) -> list[RuleEvent]:
    nuke_mode.apply_nuke(
        state, actor_id, payload.target_player_id, payload.target_vertex_id, payload.target_edge_id
    )

    previous_holder = state.longest_road_holder
    # Full recompute across all players: removing a road can break
    # contiguous paths for anyone, not just the victim.
    scoring.recompute_longest_road(state)

    events = [
        _event(
            EventType.NUKE_DROPPED,
            NukeDroppedPayload(
                actor=actor_id,
                target=payload.target_player_id,
                destroyed_vertex=payload.target_vertex_id,
                destroyed_edge=payload.target_edge_id,
            ),
        )
    ]
    if state.longest_road_holder != previous_holder:
        events.append(
            _event(
                EventType.LONGEST_ROAD_CHANGED,
                LongestRoadChangedPayload(
                    new_holder=state.longest_road_holder, previous_holder=previous_holder
                ),
            )
        )
    events.extend(_check_win_condition(state))
    return events


# ---------------------------------------------------------------------
# END_TURN
# ---------------------------------------------------------------------


def _validate_end_turn(state: GameState, actor_id: PlayerId, payload) -> None:
    _require_current_player(state, actor_id)
    if isinstance(state.pending, (AwaitingDiscard, AwaitingRobberPlacement, AwaitingSteal)):
        raise RuleViolation(
            "action_pending", "Resolve the pending robber/discard action before ending your turn."
        )


def _apply_end_turn(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    events = _cancel_pending_trade_if_any(state)

    player = state.players[actor_id]
    dev_cards.fold_bought_this_turn_into_hand(player)

    state.current_player_index = (state.current_player_index + 1) % len(state.turn_order)
    state.phase = Phase.ROLL
    return events


# ---------------------------------------------------------------------
# Dispatch tables + public entry points
# ---------------------------------------------------------------------

_Validator = Callable[[GameState, PlayerId, object], None]
_Applier = Callable[[GameState, PlayerId, object], list[RuleEvent]]

VALIDATORS: dict[ActionType, _Validator] = {
    ActionType.ROLL_DICE: _validate_roll_dice,
    ActionType.BUILD_SETTLEMENT: _validate_build_settlement,
    ActionType.BUILD_ROAD: _validate_build_road,
    ActionType.BUILD_CITY: _validate_build_city,
    ActionType.BUY_DEV_CARD: _validate_buy_dev_card,
    ActionType.PLAY_DEV_CARD: _validate_play_dev_card,
    ActionType.BANK_TRADE: _validate_bank_trade,
    ActionType.PORT_TRADE: _validate_port_trade,
    ActionType.PROPOSE_TRADE: _validate_propose_trade,
    ActionType.RESPOND_TRADE: _validate_respond_trade,
    ActionType.MOVE_ROBBER: _validate_move_robber,
    ActionType.STEAL_RESOURCE: _validate_steal_resource,
    ActionType.DISCARD_CARDS: _validate_discard_cards,
    ActionType.PLAY_NUKE: _validate_play_nuke,
    ActionType.END_TURN: _validate_end_turn,
}

APPLIERS: dict[ActionType, _Applier] = {
    ActionType.ROLL_DICE: _apply_roll_dice,
    ActionType.BUILD_SETTLEMENT: _apply_build_settlement,
    ActionType.BUILD_ROAD: _apply_build_road,
    ActionType.BUILD_CITY: _apply_build_city,
    ActionType.BUY_DEV_CARD: _apply_buy_dev_card,
    ActionType.PLAY_DEV_CARD: _apply_play_dev_card,
    ActionType.BANK_TRADE: _apply_bank_trade,
    ActionType.PORT_TRADE: _apply_port_trade,
    ActionType.PROPOSE_TRADE: _apply_propose_trade,
    ActionType.RESPOND_TRADE: _apply_respond_trade,
    ActionType.MOVE_ROBBER: _apply_move_robber,
    ActionType.STEAL_RESOURCE: _apply_steal_resource,
    ActionType.DISCARD_CARDS: _apply_discard_cards,
    ActionType.PLAY_NUKE: _apply_play_nuke,
    ActionType.END_TURN: _apply_end_turn,
}


def validate(state: GameState, actor_id: PlayerId, action: ClientAction) -> None:
    """Raise `RuleViolation` if `action` is illegal for `actor_id` right
    now. Never mutates `state`. Always checks the phase table first.
    """
    if not turn_state_machine.is_action_allowed(state.phase, action.type):
        raise RuleViolation(
            "illegal_phase", f"{action.type.value} is not allowed during {state.phase.value}."
        )
    if actor_id not in state.players:
        raise RuleViolation("unknown_player", "Unknown player.")

    validator = VALIDATORS.get(action.type)
    if validator is None:
        raise RuleViolation(
            "unsupported_action", f"{action.type.value} is not handled by the rules engine."
        )
    validator(state, actor_id, action.payload)


def apply(state: GameState, actor_id: PlayerId, action: ClientAction) -> list[RuleEvent]:
    """Mutate `state` per `action` and return the events produced.
    Assumes `validate()` already passed -- see `validate_and_apply()` for
    the common "do both" case.
    """
    applier = APPLIERS.get(action.type)
    if applier is None:
        raise RuleViolation(
            "unsupported_action", f"{action.type.value} is not handled by the rules engine."
        )
    return applier(state, actor_id, action.payload)


def validate_and_apply(state: GameState, actor_id: PlayerId, action: ClientAction) -> list[RuleEvent]:
    """Convenience wrapper: `validate()` then `apply()`."""
    validate(state, actor_id, action)
    return apply(state, actor_id, action)
