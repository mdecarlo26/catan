"""Nuke Mode: custom house rule. See `ARCHITECTURE.md`'s "Nuke Mode"
section for the full spec this module implements.

`PLAY_NUKE` is validated like any other action in `rules_engine` (it's
resource-spend, not a dev card, so it doesn't go through `dev_cards.py`).
This module owns the precondition check and the pure state mutation;
`rules_engine` owns dispatch, the "does `target_player_id` even exist"
checks, event emission, and triggering `scoring.recompute_longest_road`
afterward (this module deliberately does not call into `scoring` itself
-- see `apply_nuke`'s docstring).
"""

from __future__ import annotations

from app.game.board import Board, BuildingType, EdgeId, PlayerId, VertexId
from app.game.players import ResourceHand, ResourceType
from app.game.state import GameState

#: Precondition: at least this many of each of the 5 resource types.
RESOURCE_COST_PER_TYPE = 2
#: 2 of each of the 5 resource types = 10 cards total.
TOTAL_CARD_COST = RESOURCE_COST_PER_TYPE * len(ResourceType)


def player_has_nuke_hand(hand: ResourceHand) -> bool:
    """Precondition: actor holds >=2 of each of the 5 resource types
    (10 cards total)."""
    return all(hand.get(resource, 0) >= RESOURCE_COST_PER_TYPE for resource in ResourceType)


def nuke_cost() -> ResourceHand:
    """The exact hand spent to play Nuke: 2 of each resource type."""
    return {resource: RESOURCE_COST_PER_TYPE for resource in ResourceType}


def validate_nuke_target(
    board: Board,
    target_player_id: PlayerId,
    target_vertex_id: VertexId,
    target_edge_id: EdgeId,
) -> None:
    """Raises `ValueError` if `target_vertex_id` isn't a settlement/city
    owned by `target_player_id`, or `target_edge_id` isn't a road owned
    by them. `rules_engine` translates this into a `RuleViolation` with
    an appropriate error code.
    """
    building = board.buildings.get(target_vertex_id)
    if building is None or building.player_id != target_player_id:
        raise ValueError(
            "target_vertex_id is not a settlement/city owned by target_player_id"
        )
    road_owner = board.roads.get(target_edge_id)
    if road_owner != target_player_id:
        raise ValueError("target_edge_id is not a road owned by target_player_id")


def apply_nuke(
    state: GameState,
    actor_id: PlayerId,
    target_player_id: PlayerId,
    target_vertex_id: VertexId,
    target_edge_id: EdgeId,
) -> None:
    """Mutates `state` in place. Caller (`rules_engine`) must have already
    run `player_has_nuke_hand` and `validate_nuke_target`.

    Effect, exactly per the plan's "Nuke Mode" section:
    - The 10 cards (2 of each resource) return from the actor to the bank.
    - The target vertex is cleared, and the destroyed piece returns to
      the victim's available-to-build supply (`settlements_remaining` or
      `cities_remaining`) -- they lose the board position and its VP but
      can rebuild elsewhere later, subject to normal distance rules.
    - The target edge is cleared, and the road piece returns to the
      victim's `roads_remaining` supply.
    - Port access is derived live from vertex ownership
      (`Board.ports`/`Board.buildings`), so clearing the vertex is all
      that's needed to lose port access there -- no separate bookkeeping.

    Deliberately NOT done here (left to `rules_engine`, which calls this
    then does the rest):
    - `scoring.recompute_longest_road(state)` -- removing a road segment
      can break contiguous paths for *any* player's network, not just the
      victim's, so the plan calls for a full recompute across all
      players, not an incremental patch. Keeping that call in
      `rules_engine` (alongside its other longest-road recomputes) avoids
      this module needing to import `scoring` for a single call site.
    - Largest army is left completely untouched, per the plan.
    - Emitting `NUKE_DROPPED` and any `LONGEST_ROAD_CHANGED` event --
      that's `rules_engine`'s job, not this module's.
    """
    board = state.board
    actor = state.players[actor_id]
    victim = state.players[target_player_id]

    for resource, amount in nuke_cost().items():
        actor.hand[resource] -= amount
        state.bank.resources[resource] = state.bank.resources.get(resource, 0) + amount

    building = board.buildings.pop(target_vertex_id)
    if building.building_type == BuildingType.CITY:
        victim.cities_remaining += 1
    else:
        victim.settlements_remaining += 1

    if board.roads.pop(target_edge_id, None) is not None:
        victim.roads_remaining += 1
