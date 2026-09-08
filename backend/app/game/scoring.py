"""Victory point calculation and longest-road / largest-army recompute.

Nothing here mutates the board or hands -- this module only reads
`GameState` and writes back the derived fields it owns
(`PlayerState.victory_points`/`has_longest_road`/`has_largest_army` and
`GameState.longest_road_holder`/`largest_army_holder`). `rules_engine` is
responsible for calling these after any action that could change them.
"""

from __future__ import annotations

from app.game import board as board_mod
from app.game.board import BuildingType, EdgeId, PlayerId, VertexId
from app.game.players import DevCardType, PlayerState
from app.game.state import GameState

#: Longest road only counts if it reaches at least this many road segments.
MIN_LONGEST_ROAD_LENGTH = 5
#: Largest army only counts once a player has played at least this many
#: Knight cards.
MIN_LARGEST_ARMY_KNIGHTS = 3

LONGEST_ROAD_VP = 2
LARGEST_ARMY_VP = 2
SETTLEMENT_VP = 1
CITY_VP = 2


def recompute_victory_points(state: GameState) -> None:
    """Recompute every player's public `victory_points`.

    Counted from `state.board.buildings` directly (the source of truth
    for what's actually built) rather than from remaining-piece counts,
    since upgrading a settlement to a city returns the settlement piece
    to the player's supply -- deriving VP from "pieces used" would need
    to untangle that, while the board's building list is unambiguous.
    Does NOT include hidden `VICTORY_POINT` dev cards -- see
    `total_victory_points_including_hidden` for the actor's own win
    check, which does.
    """
    built_vp: dict[PlayerId, int] = {pid: 0 for pid in state.players}
    for building in state.board.buildings.values():
        if building.player_id not in built_vp:
            continue
        built_vp[building.player_id] += (
            CITY_VP if building.building_type == BuildingType.CITY else SETTLEMENT_VP
        )

    for player_id, player in state.players.items():
        vp = built_vp.get(player_id, 0)
        if state.longest_road_holder == player_id:
            vp += LONGEST_ROAD_VP
        if state.largest_army_holder == player_id:
            vp += LARGEST_ARMY_VP
        player.victory_points = vp


def hidden_victory_points(player: PlayerState) -> int:
    """VICTORY_POINT dev cards the player holds but hasn't (needed to)
    reveal yet -- both already-owned and bought-this-turn (a VP card may
    be revealed the same turn it's bought to win, per standard rules).
    """
    return player.dev_cards.get(
        DevCardType.VICTORY_POINT, 0
    ) + player.dev_cards_bought_this_turn.get(DevCardType.VICTORY_POINT, 0)


def total_victory_points_including_hidden(state: GameState, player_id: PlayerId) -> int:
    """Total VP including unrevealed VICTORY_POINT dev cards -- used only
    for a player's own win-condition check, never shown to opponents.
    """
    player = state.players[player_id]
    return player.victory_points + hidden_victory_points(player)


def _player_road_edges(board, player_id: PlayerId) -> list[EdgeId]:
    return [edge_id for edge_id, owner in board.roads.items() if owner == player_id]


def _longest_road_length_for_player(board, player_id: PlayerId) -> int:
    """Longest simple trail (no repeated edges) through `player_id`'s own
    roads, broken at any vertex occupied by an *opponent's*
    settlement/city (an opponent's building cuts the road network there,
    per standard Catan rules) -- except as the very start of a branch,
    since a player's own road can legally originate at a junction an
    opponent later built on.
    """
    edges = _player_road_edges(board, player_id)
    if not edges:
        return 0

    adjacency: dict[VertexId, list[tuple[EdgeId, VertexId]]] = {}
    for edge_id in edges:
        v1, v2 = board_mod.edge_endpoints(edge_id)
        adjacency.setdefault(v1, []).append((edge_id, v2))
        adjacency.setdefault(v2, []).append((edge_id, v1))

    def _blocked_by_opponent(vertex_id: VertexId) -> bool:
        building = board.buildings.get(vertex_id)
        return building is not None and building.player_id != player_id

    best = 0

    def _dfs(vertex_id: VertexId, visited_edges: set[EdgeId], length: int) -> None:
        nonlocal best
        best = max(best, length)
        if length > 0 and _blocked_by_opponent(vertex_id):
            return
        for edge_id, next_vertex in adjacency.get(vertex_id, []):
            if edge_id in visited_edges:
                continue
            visited_edges.add(edge_id)
            _dfs(next_vertex, visited_edges, length + 1)
            visited_edges.remove(edge_id)

    for start_vertex in adjacency:
        _dfs(start_vertex, set(), 0)

    return best


def recompute_longest_road(state: GameState) -> PlayerId | None:
    """Recompute every player's longest-road length and update
    `state.longest_road_holder` (and each player's `has_longest_road` /
    `victory_points`) accordingly.

    Tie-breaking: a new player only takes the title with a *strictly*
    longer road than everyone else. If the longest length is tied among
    multiple players, the current holder keeps the title if they're one
    of the tied leaders; otherwise the title goes vacant (`None`) until
    someone breaks the tie. A holder whose road drops below
    `MIN_LONGEST_ROAD_LENGTH` loses the title even with no challenger.
    """
    lengths = {
        player_id: _longest_road_length_for_player(state.board, player_id)
        for player_id in state.players
    }
    current_holder = state.longest_road_holder
    eligible = {
        player_id: length
        for player_id, length in lengths.items()
        if length >= MIN_LONGEST_ROAD_LENGTH
    }

    if not eligible:
        new_holder: PlayerId | None = None
    else:
        max_length = max(eligible.values())
        leaders = [player_id for player_id, length in eligible.items() if length == max_length]
        if len(leaders) == 1:
            new_holder = leaders[0]
        elif current_holder in leaders:
            new_holder = current_holder
        else:
            new_holder = None

    if new_holder != current_holder:
        state.longest_road_holder = new_holder
        for player_id, player in state.players.items():
            player.has_longest_road = player_id == new_holder

    recompute_victory_points(state)
    return new_holder


def recompute_largest_army(state: GameState) -> PlayerId | None:
    """Same tie-breaking convention as `recompute_longest_road`, applied
    to `PlayerState.knights_played` against `MIN_LARGEST_ARMY_KNIGHTS`.
    """
    current_holder = state.largest_army_holder
    eligible = {
        player_id: player.knights_played
        for player_id, player in state.players.items()
        if player.knights_played >= MIN_LARGEST_ARMY_KNIGHTS
    }

    if not eligible:
        new_holder: PlayerId | None = None
    else:
        max_knights = max(eligible.values())
        leaders = [player_id for player_id, k in eligible.items() if k == max_knights]
        if len(leaders) == 1:
            new_holder = leaders[0]
        elif current_holder in leaders:
            new_holder = current_holder
        else:
            new_holder = None

    if new_holder != current_holder:
        state.largest_army_holder = new_holder
        for player_id, player in state.players.items():
            player.has_largest_army = player_id == new_holder

    recompute_victory_points(state)
    return new_holder
