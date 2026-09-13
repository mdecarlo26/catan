"""Simple-heuristic bot decision logic, selected/applied by
`app.game.rules_engine`'s bot-draining loop.

Per the plan's "Add bot players that auto-fill empty seats" feature: bots
are seated exactly like human players (same `PlayerState` shape, same
`Room`/`Seat` shape -- see `app.game.players.PlayerState.is_bot` and
`app.core.room.Room.add_bot`) and their actions are validated/applied
through the *exact same* `rules_engine.validate()`/`apply()` path a real
client action takes -- no bypassing legality anywhere. This module's only
job is deciding *what* a bot should do right now, as a pure function of
`GameState`: `decide_bot_action(state)` returns `(actor_id, ClientAction)`
for the single next bot decision that's due, or `None` if no bot owes a
decision right now. It never mutates `state` and never emits events --
`rules_engine._drain_bot_actions` owns calling `validate()`/`apply()`
with whatever this returns, in a loop (since one bot's action can hand
off immediately to another bot's turn/obligation).

Module convention: follows the same "pure functions, no side effects, no
event emission" shape as `app.game.rules.nuke_mode` /
`robber_strategies` / `setup_strategies` / `blackjack`.

Resource-cost duplication note: `SETTLEMENT_COST` / `ROAD_COST` /
`CITY_COST` are owned by `rules_engine` (not a shared module) and are
duplicated here rather than imported, specifically to avoid a circular
import -- `rules_engine` imports *this* module to drive its bot-draining
loop. `DEV_CARD_COST` doesn't have this problem (it's owned by
`app.game.dev_cards`, which depends on neither `rules_engine` nor this
module) and is imported directly.

Scope / intelligence level (see the feature plan for the full rationale):
  - Setup placement: maximize (total pip value, resource-type diversity)
    of the up-to-3 hexes touching a candidate vertex, among legal spots.
  - MAIN / SPECIAL_BUILD turns: build priority settlement > city > road >
    buy a dev card, stopping (END_TURN) once nothing useful/affordable is
    left. Bots never play a dev card they hold (no Knight-timing,
    Monopoly, Year of Plenty, or Road Building logic) -- buying is the
    full extent of dev-card interaction, a deliberate, documented scope
    cut per the plan ("keep dev card usage minimal").
  - Robber: moves to a hex touching an opponent, preferring a hex that
    doesn't also touch one of the bot's own settlements/cities; steals
    from the richest-handed candidate (random among ties).
  - Discard: greedily discards from its currently most-abundant resource
    type first.
  - Trade proposals / BLACKJACK betting: a bot always and immediately
    declines/auto-responds negatively -- see `decide_bot_action`'s own
    top-of-function checks. This isn't optional politeness: a
    `PROPOSE_TRADE` sits in `AwaitingTradeResponse.responses_pending`
    (and a blackjack round in `BlackjackRoundState.responses_pending`)
    until every recipient responds, so a bot that never responded would
    deadlock the proposer/round forever.
  - Bots never propose trades and never play `PLAY_NUKE`.

Explicit, documented scope limitation -- rush mode: `decide_bot_action`
returns `None` unconditionally whenever `state.settings.rush_mode` is on.
A bot seated in a rush-mode game therefore never acts: never builds,
never resolves a rush-mode discard (`GameState.rush_pending_discard`) or
robber obligation (`GameState.rush_pending_robber`) that falls on it, and
never auto-declines a rush-mode trade proposal. This is an explicit,
accepted limitation per the feature's plan (rush mode's concurrent,
no-turns gameplay is out of scope for this pass), not a bug -- see
`app.game.tests.test_bot_ai`'s dedicated inert-in-rush-mode test.
"""

from __future__ import annotations

import random

from app.game import board as board_mod
from app.game import dev_cards
from app.game.actions import (
    ActionType,
    BlackjackDeclineAction,
    BuildCityAction,
    BuildCityPayload,
    BuildRoadAction,
    BuildRoadPayload,
    BuildSettlementAction,
    BuildSettlementPayload,
    BuyDevCardAction,
    ClientAction,
    DiscardCardsAction,
    DiscardCardsPayload,
    EndTurnAction,
    MoveRobberAction,
    MoveRobberPayload,
    RespondTradeAction,
    RespondTradePayload,
    RollDiceAction,
    StealResourceAction,
    StealResourcePayload,
)
from app.game.board import BuildingType, EdgeId, HexCoord, PlayerId, Terrain, VertexId
from app.game.players import ResourceHand, ResourceType
from app.game.rules import setup_strategies
from app.game.state import (
    AwaitingDiscard,
    AwaitingRobberPlacement,
    AwaitingSteal,
    AwaitingTradeResponse,
    GameState,
    Phase,
)

#: Duplicated from `rules_engine.SETTLEMENT_COST` / `ROAD_COST` /
#: `CITY_COST` -- see this module's docstring for why (circular import).
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

TERRAIN_RESOURCE: dict[Terrain, ResourceType] = {
    Terrain.FOREST: ResourceType.LUMBER,
    Terrain.HILLS: ResourceType.BRICK,
    Terrain.MOUNTAINS: ResourceType.ORE,
    Terrain.FIELDS: ResourceType.GRAIN,
    Terrain.PASTURE: ResourceType.WOOL,
}


# ---------------------------------------------------------------------
# Small geometry/legality helpers (deliberately independent of
# rules_engine's private helpers -- see the module docstring).
# ---------------------------------------------------------------------


def _has_resources(hand: ResourceHand, cost: dict[ResourceType, int]) -> bool:
    return all(hand.get(resource, 0) >= amount for resource, amount in cost.items())


def _edges_incident_to_vertex(board, vertex_id: VertexId) -> list[EdgeId]:
    candidates: set[EdgeId] = set()
    for hex_coord in vertex_id:
        candidates.update(board_mod.get_adjacent_edges(hex_coord))
    return [edge_id for edge_id in candidates if vertex_id in board_mod.edge_endpoints(edge_id)]


def _vertex_free_and_far_enough(board, vertex_id: VertexId) -> bool:
    if vertex_id in board.buildings:
        return False
    return all(neighbor not in board.buildings for neighbor in board_mod.vertex_neighbors(vertex_id))


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


def _all_vertices(board) -> set[VertexId]:
    """Every legal (2- or 3-hex, trimmed to hexes actually on this board)
    vertex id, skipping vertices made up entirely of `Terrain.SEA` tiles
    -- mirrors the test suite's `_all_land_vertices` helper convention
    for "where a settlement could ever sensibly go".
    """
    verts: set[VertexId] = set()
    for coord, tile in board.hexes.items():
        if tile.terrain == Terrain.SEA:
            continue
        for candidate in board_mod.get_adjacent_vertices(coord):
            trimmed = tuple(sorted(c for c in candidate if c in board.hexes))
            if len(trimmed) >= 2:
                verts.add(trimmed)
    return verts


def _all_edges(board) -> set[EdgeId]:
    edges: set[EdgeId] = set()
    for coord in board.hexes:
        edges.update(board_mod.get_adjacent_edges(coord))
    return edges


def _pip_value(number_token: int | None) -> int:
    """Standard Catan pip count for a dice-roll number token (2 -> 1,
    ..., 6/8 -> 5, ..., 12 -> 1); 0 for no token (desert) or an unplaced
    hex.
    """
    if number_token is None:
        return 0
    return 6 - abs(7 - number_token)


def _vertex_score(board, vertex_id: VertexId) -> tuple[int, int]:
    """(total pip value, distinct resource-type count) of the hexes
    touching `vertex_id` -- higher is better, compared lexicographically
    (pip total first, resource diversity as the tiebreaker/secondary
    factor). Used for both setup placement and in-game settlement/city
    targeting.
    """
    total_pips = 0
    resources: set[ResourceType] = set()
    for hex_coord in vertex_id:
        tile = board.hexes.get(hex_coord)
        if tile is None:
            continue
        total_pips += _pip_value(tile.number_token)
        resource = TERRAIN_RESOURCE.get(tile.terrain)
        if resource is not None:
            resources.add(resource)
    return (total_pips, len(resources))


def _best_scored_vertex(board, candidates: list[VertexId]) -> VertexId | None:
    if not candidates:
        return None
    return max(sorted(candidates), key=lambda v: _vertex_score(board, v))


def _own_settlements(board, player_id: PlayerId) -> list[VertexId]:
    return [
        vertex_id
        for vertex_id, building in board.buildings.items()
        if building.player_id == player_id and building.building_type == BuildingType.SETTLEMENT
    ]


def _score_road_edge(board, edge_id: EdgeId) -> tuple[int, int]:
    """A cheap "does this road lead somewhere good" heuristic: the best
    `_vertex_score` of either endpoint that's still a legal future
    settlement spot, or `(-1, -1)` if neither endpoint is (e.g. both
    already occupied) -- still a perfectly legal road, just not
    obviously headed toward a future settlement.
    """
    best = (-1, -1)
    for vertex_id in board_mod.edge_endpoints(edge_id):
        if vertex_id not in board.buildings and _vertex_free_and_far_enough(board, vertex_id):
            score = _vertex_score(board, vertex_id)
            if score > best:
                best = score
    return best


def _best_road_edge(board, candidates: list[EdgeId]) -> EdgeId | None:
    if not candidates:
        return None
    return max(sorted(candidates), key=lambda e: _score_road_edge(board, e))


def _find_unpaired_setup_settlement(board, player_id: PlayerId) -> VertexId:
    """During `Phase.SETUP`, the settlement `player_id` just placed that
    doesn't yet have its paired road -- see `SnakeDraftSetup`'s docstring
    for why a player always owes at most one such settlement at a time.
    """
    for vertex_id, building in board.buildings.items():
        if building.player_id != player_id:
            continue
        has_paired_road = any(
            board.roads.get(edge_id) == player_id
            for edge_id in _edges_incident_to_vertex(board, vertex_id)
        )
        if not has_paired_road:
            return vertex_id
    raise RuntimeError(f"bot {player_id!r} has no unpaired setup settlement to place a road for")


# ---------------------------------------------------------------------
# Discard / robber heuristics
# ---------------------------------------------------------------------


def _choose_discard(hand: ResourceHand, owed: int) -> ResourceHand:
    """Greedily discard from the currently most-abundant resource type
    first, `owed` cards total.
    """
    discard: ResourceHand = {resource: 0 for resource in ResourceType}
    pool = dict(hand)
    remaining = owed
    while remaining > 0:
        resource = max(pool, key=lambda r: pool.get(r, 0))
        if pool.get(resource, 0) <= 0:
            break  # defensive: shouldn't happen if owed <= hand total
        pool[resource] -= 1
        discard[resource] += 1
        remaining -= 1
    return discard


def _hex_opponent_owners(board, actor_id: PlayerId, hex_coord: HexCoord) -> list[PlayerId]:
    owners: list[PlayerId] = []
    for vertex_id in board_mod.get_adjacent_vertices(hex_coord):
        building = board.buildings.get(vertex_id)
        if building is not None and building.player_id != actor_id and building.player_id not in owners:
            owners.append(building.player_id)
    return owners


def _choose_robber_hex(
    state: GameState, actor_id: PlayerId, rng: random.Random | None = None
) -> HexCoord:
    """A hex touching at least one opponent, preferring one that doesn't
    also touch one of `actor_id`'s own settlements/cities. Falls back to
    any other hex if literally no opponent-adjacent hex exists (a
    near-empty-board edge case).
    """
    board = state.board
    chooser = rng or random
    own_hexes = {
        hex_coord
        for vertex_id, building in board.buildings.items()
        if building.player_id == actor_id
        for hex_coord in vertex_id
    }

    with_opponents = [
        coord
        for coord in board.hexes
        if coord != board.robber_hex and _hex_opponent_owners(board, actor_id, coord)
    ]
    if not with_opponents:
        fallback = [coord for coord in board.hexes if coord != board.robber_hex]
        return chooser.choice(sorted(fallback))

    non_own = [coord for coord in with_opponents if coord not in own_hexes]
    pool = non_own or with_opponents
    return chooser.choice(sorted(pool))


def _choose_steal_target(
    state: GameState, candidate_targets: list[PlayerId], rng: random.Random | None = None
) -> PlayerId:
    """The richest-handed candidate (most total resource cards), random
    among ties.
    """
    chooser = rng or random
    counts = {pid: dev_cards.hand_total(state.players[pid].hand) for pid in candidate_targets}
    richest_count = max(counts.values())
    richest = sorted(pid for pid, count in counts.items() if count == richest_count)
    return chooser.choice(richest)


# ---------------------------------------------------------------------
# MAIN / SPECIAL_BUILD: build-priority-then-pass
# ---------------------------------------------------------------------


def _decide_build_or_pass(state: GameState, player_id: PlayerId) -> tuple[PlayerId, ClientAction]:
    """Build priority settlement > city > road > buy a dev card, whatever
    is legal/affordable; `EndTurnAction` once nothing useful is left.
    Shared as-is by `Phase.MAIN` and `Phase.SPECIAL_BUILD` (the latter
    supports the same build/trade actions minus dice/dev-card-play, and
    `END_TURN` means "pass my special build turn" there -- see
    `rules_engine._apply_end_turn`).
    """
    board = state.board
    player = state.players[player_id]
    hand = player.hand

    if player.settlements_remaining > 0 and _has_resources(hand, SETTLEMENT_COST):
        candidates = [
            vertex_id
            for vertex_id in _all_vertices(board)
            if _vertex_free_and_far_enough(board, vertex_id)
            and _player_has_connection_to_vertex(board, player_id, vertex_id)
        ]
        vertex = _best_scored_vertex(board, candidates)
        if vertex is not None:
            return player_id, BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=vertex))

    if player.cities_remaining > 0 and _has_resources(hand, CITY_COST):
        settlements = _own_settlements(board, player_id)
        if settlements:
            vertex = max(sorted(settlements), key=lambda v: _vertex_score(board, v))
            return player_id, BuildCityAction(payload=BuildCityPayload(vertex_id=vertex))

    if player.roads_remaining > 0 and _has_resources(hand, ROAD_COST):
        candidates = [
            edge_id
            for edge_id in _all_edges(board)
            if edge_id not in board.roads and _player_has_connection_to_edge(board, player_id, edge_id)
        ]
        edge = _best_road_edge(board, candidates)
        if edge is not None:
            return player_id, BuildRoadAction(payload=BuildRoadPayload(edge_id=edge))

    if state.bank.dev_card_pile and _has_resources(hand, DEV_CARD_COST):
        return player_id, BuyDevCardAction()

    return player_id, EndTurnAction()


def _decide_setup(state: GameState) -> tuple[PlayerId, ClientAction] | None:
    # Rush mode's simultaneous setup is out of scope here -- the caller
    # (`decide_bot_action`) already returns early whenever
    # `settings.rush_mode` is on, so this only ever runs for the standard
    # `SnakeDraftSetup`.
    strategy: setup_strategies.SnakeDraftSetup = setup_strategies.SETUP_STRATEGIES[False]  # type: ignore[assignment]
    player_id, action_type = strategy.get_next_setup_action(state)
    if not state.players[player_id].is_bot:
        return None

    board = state.board
    if action_type == ActionType.BUILD_SETTLEMENT:
        candidates = [v for v in _all_vertices(board) if _vertex_free_and_far_enough(board, v)]
        vertex = _best_scored_vertex(board, candidates)
        if vertex is None:
            raise RuntimeError(f"bot {player_id!r}: no legal setup settlement vertex available")
        return player_id, BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=vertex))

    vertex = _find_unpaired_setup_settlement(board, player_id)
    edges = sorted(
        edge_id
        for edge_id in _edges_incident_to_vertex(board, vertex)
        if edge_id not in board.roads
    )
    if not edges:
        raise RuntimeError(f"bot {player_id!r}: no legal setup road edge available")
    return player_id, BuildRoadAction(payload=BuildRoadPayload(edge_id=edges[0]))


# ---------------------------------------------------------------------
# Public entry point
# ---------------------------------------------------------------------


def decide_bot_action(state: GameState) -> tuple[PlayerId, ClientAction] | None:
    """The single next bot decision that's due right now, or `None` if no
    bot owes one. Pure function of `state` -- does not mutate anything.
    See the module docstring for the full scope/behavior summary,
    including the rush-mode inertness documented right below.
    """
    if state.settings.rush_mode:
        # Explicit, documented scope limitation -- see module docstring.
        return None

    # Trade auto-decline takes priority over everything else, regardless
    # of phase or whose turn it is: a PROPOSE_TRADE blocks the proposer
    # until every recipient responds, so any bot sitting in
    # `responses_pending` must be resolved before anything else that bot
    # (or the game) does. `AwaitingTradeResponse` can only ever be
    # `state.pending` during `Phase.MAIN`/`Phase.SPECIAL_BUILD` (see
    # `AwaitingTradeResponse`'s docstring), so this never misfires
    # against SETUP/ROLL/robber phases.
    if isinstance(state.pending, AwaitingTradeResponse):
        for pid in state.pending.responses_pending:
            if state.players[pid].is_bot:
                return pid, RespondTradeAction(
                    payload=RespondTradePayload(trade_id=state.pending.trade_id, accept=False)
                )

    if state.phase == Phase.BLACKJACK_ROUND and state.blackjack_round is not None:
        # Bots never bet -- immediate BLACKJACK_DECLINE is required for
        # correctness (a silent bot would stall the round's "everyone
        # responded" completion check), not optional politeness. Bots
        # never end up in `bettor_queue` (they never place a bet), so
        # there's nothing else for a bot to do in this phase.
        for pid in state.blackjack_round.responses_pending:
            if state.players[pid].is_bot:
                return pid, BlackjackDeclineAction()
        return None

    if state.phase == Phase.SETUP:
        return _decide_setup(state)

    if state.phase == Phase.ROLL:
        current = state.turn_order[state.current_player_index]
        if state.players[current].is_bot:
            return current, RollDiceAction()
        return None

    if state.phase == Phase.ROBBER_DISCARD:
        pending = state.pending
        if isinstance(pending, AwaitingDiscard):
            for pid, owed in pending.required_counts.items():
                if state.players[pid].is_bot:
                    return pid, DiscardCardsAction(
                        payload=DiscardCardsPayload(
                            resources=_choose_discard(state.players[pid].hand, owed)
                        )
                    )
        return None

    if state.phase == Phase.ROBBER_MOVE:
        pending = state.pending
        if isinstance(pending, AwaitingRobberPlacement) and state.players[pending.actor].is_bot:
            return pending.actor, MoveRobberAction(
                payload=MoveRobberPayload(hex=_choose_robber_hex(state, pending.actor))
            )
        if isinstance(pending, AwaitingSteal) and state.players[pending.actor].is_bot:
            return pending.actor, StealResourceAction(
                payload=StealResourcePayload(
                    target_player_id=_choose_steal_target(state, pending.candidate_targets)
                )
            )
        return None

    if state.phase == Phase.MAIN:
        current = state.turn_order[state.current_player_index]
        if not state.players[current].is_bot:
            return None
        return _decide_build_or_pass(state, current)

    if state.phase == Phase.SPECIAL_BUILD:
        if not state.special_build_queue:
            return None
        current = state.special_build_queue[0]
        if not state.players[current].is_bot:
            return None
        return _decide_build_or_pass(state, current)

    return None
