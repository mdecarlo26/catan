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
import time
import uuid
from dataclasses import dataclass
from typing import Callable, Literal

from pydantic import BaseModel

from app.game import board as board_mod
from app.game import dev_cards, scoring
from app.game.actions import (
    ActionType,
    BankTradePayload,
    BlackjackPlaceBetPayload,
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
from app.game.rules import blackjack, bot_ai, nuke_mode, robber_strategies, setup_strategies
from app.game.state import (
    AwaitingDiscard,
    AwaitingRobberPlacement,
    AwaitingSteal,
    AwaitingTradeResponse,
    BlackjackRoundState,
    BlackjackStake,
    GameState,
    Phase,
)
from app.game import turn_state_machine
from app.protocol.events import (
    BlackjackBetDeclinedPayload,
    BlackjackBetPlacedPayload,
    BlackjackDealerRevealedPayload,
    BlackjackHandUpdatedPayload,
    BlackjackOutcome,
    BlackjackRoundResolvedPayload,
    BlackjackRoundStartedPayload,
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


def _effective_special_build_phase(settings) -> bool:
    """The concrete on/off value of `settings.special_build_phase`: the
    explicit host override if set, else `player_count >= 5` per the
    official 5-6p expansion default. See that field's docstring.

    Rush mode has no concept of turns, so the Special Build Phase --
    fundamentally an extra build-only round appended to the end of one
    specific player's turn -- is inapplicable whenever `settings.rush_mode`
    is on, regardless of `special_build_phase`'s own value (explicit
    override or derived default) or `player_count`. This always returns
    `False` in that case. In practice `_apply_end_turn`'s SBP branch is
    already unreachable while rush_mode is on (rules_engine rejects
    `END_TURN` outright then -- see `_validate_end_turn`), but this
    short-circuit keeps the function itself correct/safe to call from
    anywhere (e.g. a future settings-form "what will actually happen"
    UI helper) rather than relying on that indirection alone.
    """
    if settings.rush_mode:
        return False
    if settings.special_build_phase is not None:
        return settings.special_build_phase
    return settings.player_count >= 5


def _require_rush_unblocked(state: GameState, actor_id: PlayerId) -> None:
    """Rush mode has no "current player" to gate on -- any seated player
    may build/trade/buy or play dev cards at any time -- EXCEPT a player
    with their own unresolved robber obligation
    (`rush_pending_robber.actor == actor_id`) or discard debt
    (`actor_id in rush_pending_discard.required_counts`), who must
    resolve that first. This mirrors, per-player, the same block normal
    mode achieves globally via `Phase.ROBBER_MOVE` / `Phase.ROBBER_DISCARD`
    -- see `GameState.rush_pending_discard` / `rush_pending_robber`'s
    docstrings for why rush mode can't just reuse the single `pending`
    slot the way normal mode does.
    """
    pending_robber = state.rush_pending_robber
    if pending_robber is not None and pending_robber.actor == actor_id:
        raise RuleViolation(
            "action_pending", "Resolve your pending robber move before doing anything else."
        )
    pending_discard = state.rush_pending_discard
    if pending_discard is not None and actor_id in pending_discard.required_counts:
        raise RuleViolation(
            "action_pending", "Resolve your pending discard before doing anything else."
        )


def _require_actionable_player(state: GameState, actor_id: PlayerId) -> None:
    """Whoever may currently spend resources / build right now: whoever
    is up in `GameState.special_build_queue` during `Phase.SPECIAL_BUILD`,
    any not-individually-blocked seated player during rush mode's
    `Phase.MAIN` (see `_require_rush_unblocked`), or else the normal
    current player.
    """
    if state.phase == Phase.SPECIAL_BUILD:
        if not state.special_build_queue or actor_id != state.special_build_queue[0]:
            raise RuleViolation("not_your_turn", "It is not your special build turn.")
        return
    if state.settings.rush_mode and state.phase == Phase.MAIN:
        _require_rush_unblocked(state, actor_id)
        return
    _require_current_player(state, actor_id)


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
    if state.settings.rush_mode:
        # Simultaneous placement: only actor_id's OWN progress matters --
        # see setup_strategies.RushModeSetup's module-level docstring for
        # why this doesn't share SnakeDraftSetup's single-global-next
        # query shape.
        strategy: setup_strategies.RushModeSetup = setup_strategies.SETUP_STRATEGIES[True]  # type: ignore[assignment]
        expected = strategy.next_expected_action_for_player(state, actor_id)
        if expected is None:
            raise RuleViolation(
                "setup_already_complete",
                "You have already placed your full setup allotment (2 settlements + 2 roads).",
            )
        if expected != expected_action:
            raise RuleViolation(
                "wrong_setup_action",
                f"Expected {expected.value} next, not {expected_action.value}.",
            )
        return

    strategy = setup_strategies.SETUP_STRATEGIES[False]  # type: ignore[assignment]
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
    if state.settings.rush_mode:
        raise RuleViolation(
            "rush_mode_auto_roll",
            "Dice roll automatically in rush mode; ROLL_DICE cannot be submitted directly.",
        )
    _require_current_player(state, actor_id)
    if state.pending is not None:
        raise RuleViolation("action_pending", "Cannot roll while another action is pending.")


def _apply_roll_dice(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    die1 = random.randint(1, 6)
    die2 = random.randint(1, 6)
    total = die1 + die2
    state.last_dice_roll = (die1, die2)
    state.last_dice_roll_ts = time.time()

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
# Rush mode: system-driven auto-roll
# ---------------------------------------------------------------------


def _next_rush_robber_mover(state: GameState) -> PlayerId | None:
    """Assign (and advance the rotation pointer past) the next connected,
    seated player starting at `GameState.rush_robber_turn_index`, per
    that field's docstring. Returns `None` (leaving the pointer
    untouched) only if literally nobody is connected, which shouldn't
    normally happen but is handled defensively rather than crashing the
    game loop.
    """
    order = state.turn_order
    n = len(order)
    if n == 0:
        return None
    for offset in range(n):
        idx = (state.rush_robber_turn_index + offset) % n
        candidate = order[idx]
        if state.players[candidate].is_connected:
            state.rush_robber_turn_index = (idx + 1) % n
            return candidate
    return None


def _advance_rush_robber_pointer(state: GameState) -> None:
    """Skip the rotation pointer forward by one slot without assigning
    anyone -- used when a 7 fires while a previous rush-mode robber-move
    is still unresolved, per `GameState.rush_robber_turn_index`'s
    documented "queue by skipping" tradeoff (don't double-assign the one
    physical robber to two people at once).
    """
    n = len(state.turn_order)
    if n:
        state.rush_robber_turn_index = (state.rush_robber_turn_index + 1) % n


def apply_rush_auto_roll(state: GameState) -> list[RuleEvent]:
    """System-driven dice roll for rush mode's `Phase.MAIN`: called by the
    per-room background task in `app.api.websocket` on a fixed interval
    (`settings.rush_roll_interval_seconds`), never in response to a
    client action -- `ROLL_DICE` is rejected outright while `rush_mode`
    is on (see `_validate_roll_dice`). There is deliberately no `actor_id`
    parameter: nobody "did" this roll.

    Reuses `_distribute_resources` exactly as the client-invoked
    `ROLL_DICE` path does for a non-7 roll, so the actual resource-
    distribution mechanics (bank-shortage gating, city double-yield, ...)
    aren't duplicated between the two call sites. `_discard_requirements`
    is reused the same way for computing who owes a discard.

    On a 7, unlike normal mode, this does NOT change `state.phase` or
    touch `state.pending` -- see `GameState.rush_pending_discard` /
    `rush_pending_robber`'s docstrings for the concurrent, per-player
    obligation model this uses instead, and `_next_rush_robber_mover` /
    `_advance_rush_robber_pointer` for the rotation/"don't double-assign"
    logic.

    Design choice -- dev-card "not the turn you bought it" eligibility:
    rush mode has no per-player turn boundary at which to fold
    `PlayerState.dev_cards_bought_this_turn` into playable `dev_cards`
    (normally an `END_TURN`-time step -- see
    `dev_cards.fold_bought_this_turn_into_hand` -- and `END_TURN` is
    rejected outright in rush mode). Each auto-roll is the closest analog
    to "a new round begins" for every player at once, so it's used as
    that boundary instead: a dev card bought becomes playable starting
    with the *next* auto-roll tick, not instantly. This is a deliberate,
    documented interpretation where the feature spec is silent.
    """
    if not state.settings.rush_mode:
        raise RuntimeError("apply_rush_auto_roll() called for a non-rush-mode game")

    for player in state.players.values():
        dev_cards.fold_bought_this_turn_into_hand(player)

    die1 = random.randint(1, 6)
    die2 = random.randint(1, 6)
    total = die1 + die2
    state.last_dice_roll = (die1, die2)
    state.last_dice_roll_ts = time.time()

    events: list[RuleEvent] = [
        _event(
            EventType.DICE_ROLLED,
            # No single roller in rush mode -- see this function's docstring.
            DiceRolledPayload(player_id=None, die1=die1, die2=die2, total=total),
        )
    ]

    if total == 7:
        # (a) Discard debts: reused as-is from `_discard_requirements`,
        # merged into any still-outstanding debt from an earlier
        # unresolved 7 (recomputing is safe/idempotent here since a
        # player's owed count can only change by actually discarding,
        # which clears their entry entirely -- see `_apply_discard_cards`).
        required = _discard_requirements(state)
        if required:
            merged = dict(state.rush_pending_discard.required_counts) if state.rush_pending_discard else {}
            merged.update(required)
            state.rush_pending_discard = AwaitingDiscard(required_counts=merged)
            events.append(
                _event(EventType.DISCARD_REQUIRED, DiscardRequiredPayload(required_counts=merged))
            )

        # (b) Robber-mover assignment: separate from (a) above -- a
        # player can owe a discard AND be assigned the robber at the same
        # time, and each is resolved independently (`_require_rush_unblocked`
        # blocks that player from other actions until BOTH are cleared).
        if state.rush_pending_robber is None:
            assignee = _next_rush_robber_mover(state)
            if assignee is not None:
                state.rush_pending_robber = AwaitingRobberPlacement(actor=assignee, reason="dice_roll")
        else:
            _advance_rush_robber_pointer(state)
    else:
        distribution = _distribute_resources(state, total)
        events.append(
            _event(EventType.RESOURCES_DISTRIBUTED, ResourcesDistributedPayload(distribution=distribution))
        )

    events.extend(_check_win_condition(state))
    return events


# ---------------------------------------------------------------------
# BUILD_SETTLEMENT / BUILD_ROAD / BUILD_CITY
# ---------------------------------------------------------------------


def _validate_build_settlement(state: GameState, actor_id: PlayerId, payload: BuildSettlementPayload) -> None:
    if state.phase == Phase.SETUP:
        _validate_setup_turn(state, actor_id, ActionType.BUILD_SETTLEMENT)
    else:
        _require_actionable_player(state, actor_id)
        if not _has_resources(state.players[actor_id].hand, SETTLEMENT_COST):
            raise RuleViolation("insufficient_resources", "Not enough resources for a settlement.")

    player = state.players[actor_id]
    if player.settlements_remaining <= 0:
        raise RuleViolation("no_settlements_left", "No settlement pieces left to build.")

    _vertex_free_and_far_enough(state.board, payload.vertex_id)

    if state.phase in (Phase.MAIN, Phase.SPECIAL_BUILD) and not _player_has_connection_to_vertex(
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
        _require_actionable_player(state, actor_id)
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
    _require_actionable_player(state, actor_id)
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
    _require_actionable_player(state, actor_id)
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
    _require_actionable_player(state, actor_id)

    if payload.card_type == DevCardType.VICTORY_POINT:
        raise RuleViolation(
            "cannot_play_victory_point",
            "Victory point cards can't be played; they count automatically toward your win check.",
        )
    if state.players[actor_id].dev_cards.get(payload.card_type, 0) < 1:
        raise RuleViolation(
            "card_not_owned", "You don't own that dev card (or it was bought this turn)."
        )
    if (
        payload.card_type == DevCardType.KNIGHT
        and state.settings.rush_mode
        and state.rush_pending_robber is not None
    ):
        # There's only one physical robber -- rush mode can't let a
        # second player start moving it while another player's
        # dice-roll-triggered (or an earlier Knight-triggered) robber
        # move is still unresolved. Unlike the "don't double-assign,
        # advance the pointer" tradeoff for consecutive auto-rolled 7s
        # (see GameState.rush_robber_turn_index), a Knight play is a
        # discretionary player action, not a scheduled system event, so
        # simply rejecting it (the player can retry once the current
        # robber move resolves) is the more honest behavior here -- this
        # is a genuine shared-resource contention, not a turn gate.
        raise RuleViolation(
            "robber_move_in_progress",
            "Another robber move is already pending; try playing this Knight again shortly.",
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
        if state.settings.rush_mode:
            # Stays in Phase.MAIN -- only the playing actor is blocked
            # (via rush_pending_robber, checked by
            # _require_rush_unblocked), not the whole game. Validated
            # unassigned by _validate_play_dev_card above.
            state.rush_pending_robber = AwaitingRobberPlacement(actor=actor_id, reason="knight_card")
        else:
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
    _require_actionable_player(state, actor_id)
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
    _require_actionable_player(state, actor_id)
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
    # Scope decision: even in rush mode, only one PROPOSE_TRADE may be
    # outstanding at a time game-wide (via the shared `state.pending`
    # slot, same as normal mode) -- true concurrent multi-trade
    # negotiation would need a per-trade id'd collection instead of one
    # global slot, which is a materially bigger redesign than this
    # feature's scope. This is treated as a shared-resource legality
    # precondition ("no trade is currently pending"), not a turn gate:
    # any player, not just "whoever's turn it is", can hit it, and it
    # never blocks anyone from building/buying/using bank or port trades
    # in the meantime -- only from *proposing a second* PROPOSE_TRADE.
    _require_actionable_player(state, actor_id)
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
    if state.settings.rush_mode:
        if state.rush_pending_discard and actor_id in state.rush_pending_discard.required_counts:
            # Same ordering as normal mode (discard resolves before the
            # robber moves), just enforced per-player instead of via a
            # global phase, since it's the SAME player who'd otherwise be
            # both discard-owing and the assigned robber-mover.
            raise RuleViolation(
                "discard_owed", "Discard your owed cards before moving the robber."
            )
        pending = state.rush_pending_robber
    else:
        pending = state.pending
    if not isinstance(pending, AwaitingRobberPlacement):
        raise RuleViolation("no_pending_robber_move", "The robber isn't waiting to be moved.")
    if actor_id != pending.actor:
        raise RuleViolation("not_your_turn", "It is not your turn to move the robber.")
    if payload.hex not in state.board.hexes:
        raise RuleViolation("invalid_hex", "That hex isn't on the board.")
    if payload.hex == state.board.robber_hex:
        raise RuleViolation("must_move", "The robber must move to a different hex.")


def _robber_move_and_gather_steal_candidates(
    state: GameState, actor_id: PlayerId, hex_coord
) -> tuple[list[RuleEvent], list[PlayerId]]:
    """Move the robber (unconditional board mutation) and compute steal
    candidates, per `friendly_robber` vs. normal rules. Shared by normal
    mode's and rush mode's `MOVE_ROBBER` handling so this logic -- which
    doesn't depend on "whose turn it is", only on the actor and the
    target hex -- isn't duplicated between the two.
    """
    state.board.robber_hex = hex_coord
    events: list[RuleEvent] = [
        _event(EventType.ROBBER_MOVED, RobberMovedPayload(actor=actor_id, hex=hex_coord))
    ]

    vertex_owners: list[PlayerId] = []
    for vertex_id in board_mod.get_adjacent_vertices(hex_coord):
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
    return events, candidates


def _apply_move_robber(state: GameState, actor_id: PlayerId, payload: MoveRobberPayload) -> list[RuleEvent]:
    pending_before = state.rush_pending_robber if state.settings.rush_mode else state.pending
    assert isinstance(pending_before, AwaitingRobberPlacement)
    reason = pending_before.reason

    events, candidates = _robber_move_and_gather_steal_candidates(state, actor_id, payload.hex)

    resolved_steal_event: RuleEvent | None = None
    next_pending: AwaitingSteal | None = None
    if candidates:
        if len(candidates) == 1:
            stolen = robber_strategies.normal_steal(state.players, actor_id, candidates[0])
            resolved_steal_event = _event(
                EventType.RESOURCE_STOLEN,
                ResourceStolenPayload(actor=actor_id, victim=candidates[0], resource=stolen),
            )
        else:
            next_pending = AwaitingSteal(actor=actor_id, candidate_targets=candidates, reason=reason)

    if resolved_steal_event is not None:
        events.append(resolved_steal_event)

    if state.settings.rush_mode:
        # Stays in Phase.MAIN throughout -- only `actor_id` is
        # blocked (via rush_pending_robber, see _require_rush_unblocked),
        # not the whole game. Blackjack is never offered in rush mode --
        # see _maybe_enter_blackjack_round -- so this branch never needs
        # to consider it.
        state.rush_pending_robber = next_pending
    else:
        state.pending = next_pending
        if next_pending is None:
            # The robber-move/steal sequence has fully resolved (0 or 1
            # steal candidates) -- this is the exact point that would
            # normally return to Phase.MAIN. Offer blackjack first if
            # applicable; either way this sets state.phase.
            events.extend(_maybe_enter_blackjack_round(state, dealer_id=actor_id, reason=reason))
        else:
            state.phase = Phase.ROBBER_MOVE
            # (Phase.ROBBER_MOVE is a no-op re-assignment here -- state.phase
            # was already ROBBER_MOVE per the phase table's MOVE_ROBBER gate.)

    events.extend(_check_win_condition(state))
    return events


def _validate_steal_resource(state: GameState, actor_id: PlayerId, payload: StealResourcePayload) -> None:
    pending = state.rush_pending_robber if state.settings.rush_mode else state.pending
    if not isinstance(pending, AwaitingSteal):
        raise RuleViolation("no_pending_steal", "No steal is currently pending.")
    if actor_id != pending.actor:
        raise RuleViolation("not_your_turn", "It is not your turn to steal.")
    if payload.target_player_id not in pending.candidate_targets:
        raise RuleViolation("invalid_target", "That player isn't a valid steal target.")


def _apply_steal_resource(state: GameState, actor_id: PlayerId, payload: StealResourcePayload) -> list[RuleEvent]:
    pending = state.rush_pending_robber if state.settings.rush_mode else state.pending
    assert isinstance(pending, AwaitingSteal)
    reason = pending.reason

    stolen = robber_strategies.normal_steal(state.players, actor_id, payload.target_player_id)
    events = [
        _event(
            EventType.RESOURCE_STOLEN,
            ResourceStolenPayload(actor=actor_id, victim=payload.target_player_id, resource=stolen),
        )
    ]

    if state.settings.rush_mode:
        state.rush_pending_robber = None
    else:
        state.pending = None
        # This is the exact point that would normally return to
        # Phase.MAIN once the (multi-candidate) steal resolves -- see
        # _apply_move_robber's identical call for the 0/1-candidate path.
        events.extend(_maybe_enter_blackjack_round(state, dealer_id=actor_id, reason=reason))

    events.extend(_check_win_condition(state))
    return events


# ---------------------------------------------------------------------
# Blackjack-on-7 (settings.blackjack_mode)
# ---------------------------------------------------------------------


def _blackjack_eligible_bettors(state: GameState, dealer_id: PlayerId) -> list[PlayerId]:
    """Every other seated, connected player, in seat/turn order -- "any
    other connected player may opt in" per the plan."""
    return [
        pid
        for pid in state.turn_order
        if pid != dealer_id and state.players[pid].is_connected
    ]


def _maybe_enter_blackjack_round(
    state: GameState, dealer_id: PlayerId, reason: Literal["dice_roll", "knight_card"]
) -> list[RuleEvent]:
    """Called at the exact point the post-robber sequence would normally
    set `state.phase = Phase.MAIN` (see this function's two call sites in
    `_apply_move_robber` / `_apply_steal_resource`). Always sets
    `state.phase` itself -- callers never separately assign `Phase.MAIN`
    on this path.

    Per the plan: blackjack is only ever offered for a *dice-roll* 7 (a
    Knight-triggered robber move has no "roll" to attach a dealer to --
    `reason == "knight_card"` always skips this), only when
    `settings.blackjack_mode` is on, and never in rush mode (no single
    "current player"/dealer concept there -- structurally unreachable
    from `_apply_move_robber`'s rush branch anyway, but checked directly
    here too for the same "safe to call from anywhere" reasoning as
    `_effective_special_build_phase`). Also a no-op (straight to MAIN) if
    there's nobody currently connected to even offer a bet to.
    """
    if reason != "dice_roll" or not state.settings.blackjack_mode or state.settings.rush_mode:
        state.phase = Phase.MAIN
        return []

    eligible = _blackjack_eligible_bettors(state, dealer_id)
    if not eligible:
        state.phase = Phase.MAIN
        return []

    state.phase = Phase.BLACKJACK_ROUND
    state.blackjack_round = BlackjackRoundState(dealer_id=dealer_id, responses_pending=eligible)
    return [
        _event(
            EventType.BLACKJACK_ROUND_STARTED,
            BlackjackRoundStartedPayload(dealer_id=dealer_id, eligible_player_ids=eligible),
        )
    ]


def _close_blackjack_betting(state: GameState) -> list[RuleEvent]:
    """Called once `blackjack_round.responses_pending` empties (everyone
    answered, or the stalled-turn timer force-declined the rest -- see
    `app.api.websocket._force_advance_stalled_blackjack`). Either aborts
    the round back to `Phase.MAIN` with no cards dealt (nobody bet, per
    the plan's explicit no-op case) or deals the dealer + every
    participant two cards and starts the bettor-turn queue walk.

    Betting-window completion policy: this codebase reuses the
    `turn_timer_seconds` stalled-turn-timer pattern (see
    `app.api.websocket`) for "don't let one idle player freeze the
    round" rather than a separate timed window -- betting itself closes
    via this simpler, deterministic "everyone has explicitly responded"
    check, which is easier to reason about/test than a wall-clock
    window and needs no extra setting. This is a documented
    implementation-level call the plan left open ("a simpler explicit
    ... completion check if that's simpler to get right").
    """
    round_ = state.blackjack_round
    assert round_ is not None

    if not round_.participants:
        state.phase = Phase.MAIN
        state.blackjack_round = None
        return []

    round_.deck = blackjack.build_shuffled_deck()
    # Deal order (a documented simplification of a real table's
    # alternating one-card-at-a-time deal, which makes no mathematical
    # difference against a freshly shuffled deck): the dealer's two cards
    # first, then each bettor's two cards in turn_order -- not
    # interleaved. See test_blackjack.py's
    # test_full_round_queue_order_and_all_payout_outcomes for the exact
    # draw sequence this produces.
    round_.dealer_hand = [blackjack.draw_card(round_.deck), blackjack.draw_card(round_.deck)]
    # Seat-order queue, mirroring special_build_queue's shape exactly.
    round_.bettor_queue = [pid for pid in state.turn_order if pid in round_.participants]
    for bettor_id in round_.bettor_queue:
        round_.participants[bettor_id].hand = [
            blackjack.draw_card(round_.deck), blackjack.draw_card(round_.deck)
        ]
    round_.status = "bettor_turn"
    return []


def _advance_blackjack_queue_or_resolve(state: GameState) -> list[RuleEvent]:
    round_ = state.blackjack_round
    assert round_ is not None
    if round_.bettor_queue:
        return []
    return _resolve_blackjack_round(state)


def _resolve_blackjack_round(state: GameState) -> list[RuleEvent]:
    """Once every participant has stood or busted: reveal + auto-play the
    dealer's hand, resolve every participant's payout, then return to
    Phase.MAIN and clear `blackjack_round`. All synchronous, in one
    `apply()` call -- see `BlackjackRoundState`'s docstring.
    """
    round_ = state.blackjack_round
    assert round_ is not None
    dealer_id = round_.dealer_id

    blackjack.play_dealer_hand(round_.deck, round_.dealer_hand)
    round_.dealer_hole_card_revealed = True
    dealer_total = blackjack.hand_value(round_.dealer_hand)
    dealer_busted = blackjack.is_bust(round_.dealer_hand)

    events: list[RuleEvent] = [
        _event(
            EventType.BLACKJACK_DEALER_REVEALED,
            BlackjackDealerRevealedPayload(
                dealer_id=dealer_id,
                dealer_hand=list(round_.dealer_hand),
                dealer_total=dealer_total,
                dealer_busted=dealer_busted,
            ),
        )
    ]

    outcomes: dict[PlayerId, BlackjackOutcome] = {}
    road_removed = False
    for bettor_id, participant in round_.participants.items():
        bettor_total = blackjack.hand_value(participant.hand)
        bettor_busted = participant.status == "busted"
        result = blackjack.resolve_outcome(bettor_total, bettor_busted, dealer_total, dealer_busted)
        blackjack.resolve_stake_payout(state, dealer_id, bettor_id, participant.stake, result)
        if participant.stake.kind == "road" and result == "loss":
            road_removed = True
        outcomes[bettor_id] = BlackjackOutcome(
            result=result,
            stake=participant.stake,
            final_hand=list(participant.hand),
            final_total=bettor_total,
            busted=bettor_busted,
        )

    events.append(
        _event(
            EventType.BLACKJACK_ROUND_RESOLVED,
            BlackjackRoundResolvedPayload(
                dealer_id=dealer_id,
                dealer_hand=list(round_.dealer_hand),
                dealer_total=dealer_total,
                dealer_busted=dealer_busted,
                outcomes=outcomes,
            ),
        )
    )

    if road_removed:
        # Full recompute across all players, mirroring PLAY_NUKE's road
        # removal -- breaking one player's road segment can affect
        # anyone's longest-road length. This also recomputes VP.
        scoring.recompute_longest_road(state)
    scoring.recompute_victory_points(state)

    state.phase = Phase.MAIN
    state.blackjack_round = None
    events.extend(_check_win_condition(state))
    return events


def _validate_blackjack_place_bet(
    state: GameState, actor_id: PlayerId, payload: BlackjackPlaceBetPayload
) -> None:
    round_ = state.blackjack_round
    if round_ is None or round_.status != "betting":
        raise RuleViolation("no_blackjack_betting", "No blackjack betting window is open.")
    if actor_id not in round_.responses_pending:
        raise RuleViolation(
            "not_eligible", "You are not eligible to bet in this round (or already responded)."
        )

    provided = [
        value for value in (payload.resources, payload.vertex_id, payload.edge_id) if value is not None
    ]
    if len(provided) != 1:
        raise RuleViolation(
            "invalid_bet", "Exactly one of resources/vertex_id/edge_id must be given."
        )

    if payload.resources is not None:
        total = sum(amount for amount in payload.resources.values() if amount > 0)
        if total <= 0:
            raise RuleViolation("invalid_bet", "Must bet at least one resource card.")
        if not _has_resources(state.players[actor_id].hand, payload.resources):
            raise RuleViolation(
                "insufficient_resources", "You don't hold the resources you're trying to bet."
            )
    elif payload.vertex_id is not None:
        building = state.board.buildings.get(payload.vertex_id)
        if building is None or building.player_id != actor_id:
            raise RuleViolation("invalid_target", "You don't own a settlement/city there.")
    else:
        if state.board.roads.get(payload.edge_id) != actor_id:
            raise RuleViolation("invalid_target", "You don't own a road there.")


def _apply_blackjack_place_bet(
    state: GameState, actor_id: PlayerId, payload: BlackjackPlaceBetPayload
) -> list[RuleEvent]:
    round_ = state.blackjack_round
    assert round_ is not None
    round_.responses_pending.remove(actor_id)

    if payload.resources is not None:
        stake = BlackjackStake(
            kind="resources",
            resources={r: a for r, a in payload.resources.items() if a > 0},
        )
    else:
        kind = blackjack.stake_kind_for_bet(state, payload.vertex_id, payload.edge_id)
        stake = BlackjackStake(kind=kind, vertex_id=payload.vertex_id, edge_id=payload.edge_id)

    round_.participants[actor_id] = blackjack.new_participant(stake)
    events: list[RuleEvent] = [
        _event(
            EventType.BLACKJACK_BET_PLACED,
            BlackjackBetPlacedPayload(player_id=actor_id, stake=stake),
        )
    ]
    if not round_.responses_pending:
        events.extend(_close_blackjack_betting(state))
    return events


def _validate_blackjack_decline(state: GameState, actor_id: PlayerId, payload) -> None:
    round_ = state.blackjack_round
    if round_ is None or round_.status != "betting":
        raise RuleViolation("no_blackjack_betting", "No blackjack betting window is open.")
    if actor_id not in round_.responses_pending:
        raise RuleViolation(
            "not_eligible", "You are not eligible to respond in this round (or already responded)."
        )


def _apply_blackjack_decline(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    round_ = state.blackjack_round
    assert round_ is not None
    round_.responses_pending.remove(actor_id)
    events: list[RuleEvent] = [
        _event(EventType.BLACKJACK_BET_DECLINED, BlackjackBetDeclinedPayload(player_id=actor_id))
    ]
    if not round_.responses_pending:
        events.extend(_close_blackjack_betting(state))
    return events


def _validate_blackjack_turn(state: GameState, actor_id: PlayerId) -> BlackjackRoundState:
    round_ = state.blackjack_round
    if round_ is None or round_.status != "bettor_turn":
        raise RuleViolation(
            "no_blackjack_turn", "No blackjack hand is waiting on a hit/stand decision."
        )
    if not round_.bettor_queue or actor_id != round_.bettor_queue[0]:
        raise RuleViolation("not_your_turn", "It is not your blackjack turn.")
    return round_


def _validate_blackjack_hit(state: GameState, actor_id: PlayerId, payload) -> None:
    _validate_blackjack_turn(state, actor_id)


def _apply_blackjack_hit(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    round_ = state.blackjack_round
    assert round_ is not None
    participant = round_.participants[actor_id]
    participant.hand.append(blackjack.draw_card(round_.deck))

    events: list[RuleEvent]
    if blackjack.is_bust(participant.hand):
        participant.status = "busted"
        round_.bettor_queue.pop(0)
        events = [
            _event(
                EventType.BLACKJACK_HAND_UPDATED,
                BlackjackHandUpdatedPayload(
                    player_id=actor_id, hand=list(participant.hand), status="busted"
                ),
            )
        ]
        events.extend(_advance_blackjack_queue_or_resolve(state))
    else:
        events = [
            _event(
                EventType.BLACKJACK_HAND_UPDATED,
                BlackjackHandUpdatedPayload(
                    player_id=actor_id, hand=list(participant.hand), status="playing"
                ),
            )
        ]
    return events


def _validate_blackjack_stand(state: GameState, actor_id: PlayerId, payload) -> None:
    _validate_blackjack_turn(state, actor_id)


def _apply_blackjack_stand(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    round_ = state.blackjack_round
    assert round_ is not None
    participant = round_.participants[actor_id]
    participant.status = "stood"
    round_.bettor_queue.pop(0)
    events: list[RuleEvent] = [
        _event(
            EventType.BLACKJACK_HAND_UPDATED,
            BlackjackHandUpdatedPayload(player_id=actor_id, hand=list(participant.hand), status="stood"),
        )
    ]
    events.extend(_advance_blackjack_queue_or_resolve(state))
    return events


def _validate_discard_cards(state: GameState, actor_id: PlayerId, payload: DiscardCardsPayload) -> None:
    pending = state.rush_pending_discard if state.settings.rush_mode else state.pending
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
    pending = state.rush_pending_discard if state.settings.rush_mode else state.pending
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
        if state.settings.rush_mode:
            state.rush_pending_discard = None
            # Unlike normal mode, this does NOT trigger a robber-move
            # assignment -- rush mode decides that independently, at
            # roll time (see apply_rush_auto_roll), not gated behind
            # discard resolution. A rush-mode 7's discard debts and its
            # robber-mover assignment are resolved on separate,
            # independent tracks (see that function's own comment).
        else:
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
    # Nuke is a resource-spend, not a turn-taking action -- allow whoever
    # may currently act (any unblocked player in rush mode's MAIN phase,
    # else the normal current player) rather than hard-coding "current
    # player", so nuke_mode still works when combined with rush_mode.
    _require_actionable_player(state, actor_id)
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
    if state.settings.rush_mode:
        raise RuleViolation(
            "rush_mode_no_turns",
            "END_TURN is meaningless in rush mode; there are no turns to end.",
        )
    if state.phase == Phase.SPECIAL_BUILD:
        # Reused for "I'm done with my special build turn" -- its meaning
        # is unambiguous from phase context. Only the player currently up
        # in the queue may submit it.
        if not state.special_build_queue or actor_id != state.special_build_queue[0]:
            raise RuleViolation("not_your_turn", "It is not your special build turn.")
        return
    _require_current_player(state, actor_id)
    if isinstance(state.pending, (AwaitingDiscard, AwaitingRobberPlacement, AwaitingSteal)):
        raise RuleViolation(
            "action_pending", "Resolve the pending robber/discard action before ending your turn."
        )


def _apply_end_turn(state: GameState, actor_id: PlayerId, payload) -> list[RuleEvent]:
    events = _cancel_pending_trade_if_any(state)
    player = state.players[actor_id]
    dev_cards.fold_bought_this_turn_into_hand(player)

    if state.phase == Phase.SPECIAL_BUILD:
        # `actor_id` is guaranteed by `_validate_end_turn` to be
        # `special_build_queue[0]`: pop them and either move on to the
        # next player in the queue or, once it's exhausted, perform the
        # real turn advance that was deferred when the special build
        # round started (see the MAIN branch below).
        state.special_build_queue.pop(0)
        if not state.special_build_queue:
            state.current_player_index = (state.current_player_index + 1) % len(state.turn_order)
            state.phase = Phase.ROLL
        return events

    # Ending a normal full turn. Per the official 5-6p expansion, when
    # the effective special_build_phase setting is on (see
    # `_effective_special_build_phase`) and there's more than one player,
    # don't advance the turn yet -- give every other player, in turn
    # order starting right after `actor_id`, a build-only mini-turn
    # first. `current_player_index` is deliberately left unchanged for
    # the whole special build round; see `GameState.special_build_queue`.
    if _effective_special_build_phase(state.settings) and len(state.turn_order) > 1:
        player_count = len(state.turn_order)
        state.special_build_queue = [
            state.turn_order[(state.current_player_index + 1 + offset) % player_count]
            for offset in range(player_count - 1)
        ]
        state.phase = Phase.SPECIAL_BUILD
        return events

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
    ActionType.BLACKJACK_PLACE_BET: _validate_blackjack_place_bet,
    ActionType.BLACKJACK_DECLINE: _validate_blackjack_decline,
    ActionType.BLACKJACK_HIT: _validate_blackjack_hit,
    ActionType.BLACKJACK_STAND: _validate_blackjack_stand,
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
    ActionType.BLACKJACK_PLACE_BET: _apply_blackjack_place_bet,
    ActionType.BLACKJACK_DECLINE: _apply_blackjack_decline,
    ActionType.BLACKJACK_HIT: _apply_blackjack_hit,
    ActionType.BLACKJACK_STAND: _apply_blackjack_stand,
    ActionType.END_TURN: _apply_end_turn,
}


#: Defensive iteration cap for `_drain_bot_actions`, guarding against an
#: unforeseen bug producing an infinite bot-action handoff loop (e.g. two
#: bots somehow perpetually re-triggering each other). A real game never
#: gets remotely close to this -- even a full 8-bot SETUP snake draft is
#: only 32 placements.
BOT_ACTION_ITERATION_CAP = 200


def _drain_bot_actions(state: GameState) -> list[RuleEvent]:
    """After `state` settles into a new shape (following any applied
    action -- human or bot), check whether a bot now owes the next
    decision at any pending decision point and, if so, synchronously
    compute and apply it, looping since one bot's action can immediately
    hand off to another bot's turn/obligation (e.g. several bots in a row
    during SETUP's snake draft, or a bot passing all the way through a
    SPECIAL_BUILD turn). Bounded by `BOT_ACTION_ITERATION_CAP` as a
    defensive guard, not an expected limit.

    Decision logic lives in `app.game.rules.bot_ai.decide_bot_action`
    (a pure function of `state`, no mutation/events of its own); this
    function owns applying whatever it decides through the *exact same*
    `validate()` -> `apply()` path a real client action takes -- no
    bypassing legality for bots. Does nothing at all while
    `settings.rush_mode` is on -- see `bot_ai.decide_bot_action`'s own
    early return and its docstring for that explicit, documented scope
    limitation.
    """
    events: list[RuleEvent] = []
    for _ in range(BOT_ACTION_ITERATION_CAP):
        decision = bot_ai.decide_bot_action(state)
        if decision is None:
            break
        actor_id, action = decision
        validate(state, actor_id, action)
        events.extend(apply(state, actor_id, action))
    return events


def drain_bot_actions(state: GameState) -> list[RuleEvent]:
    """Public entry point for callers that mutate `state` outside
    `validate_and_apply` -- specifically
    `app.api.websocket._handle_start_game`, right after constructing the
    freshly-started `GameState`: the very first `Phase.SETUP` placement
    may already belong to a bot (e.g. if `turn_order[0]` is a bot seat).
    `validate_and_apply` below calls the same underlying drain
    automatically after every action it applies, so callers going through
    that path never need to call this separately.
    """
    return _drain_bot_actions(state)


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
    """Convenience wrapper: `validate()` then `apply()`, then drains any
    bot decisions that become due as a result (see `_drain_bot_actions`)
    -- every call site in `app.api.websocket` (a real client action, a
    stalled-turn-timer forced action, a forced blackjack decline/stand)
    goes through this one function, so bot turns/obligations are always
    picked up automatically without each call site needing to remember to
    drain them itself.
    """
    validate(state, actor_id, action)
    events = apply(state, actor_id, action)
    events.extend(_drain_bot_actions(state))
    return events
