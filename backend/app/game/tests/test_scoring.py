"""Tests for `app.game.scoring`: VP calculation, longest-road recompute,
and largest-army recompute.

`app.game.board`'s geometry functions (`get_adjacent_vertices`,
`get_adjacent_edges`, `vertex_neighbors`, `edge_endpoints`) raise
`NotImplementedError` in this worktree -- the real implementation is
Wave 1 work happening on a parallel branch. These tests monkeypatch them
with a small, internally-consistent linear "chain" board (hexes in a row,
one vertex per hex-pair junction, one edge per consecutive vertex pair)
built by `_make_chain` below, which is enough to exercise real longest-
road graph search and VP bookkeeping without needing real hex geometry.
"""

from __future__ import annotations

from app.game import board as board_mod
from app.game import scoring
from app.game.board import Board, BuildingType, EdgeId, HexCoord, HexTile, Terrain, VertexBuilding, VertexId
from app.game.players import DevCardType, PlayerState, ResourceType
from app.game.settings_schema import GameSettings
from app.game.state import Bank, GameState, Phase


def _make_chain(start_q: int, length: int) -> dict:
    """A linear chain of `length` hexes (axial q = start_q..start_q+length-1,
    r = 0), `length + 1` vertices (one per hex-to-hex junction, plus the
    two end caps), and `length` edges connecting consecutive vertices.
    `start_q` lets multiple independent chains coexist on one board
    without colliding on hex coordinates.
    """
    hex_coords = [HexCoord(start_q + i, 0) for i in range(length)]
    dummy_cap = HexCoord(start_q + length, 0)
    edge_hexes = hex_coords + [dummy_cap]

    vertices: list[VertexId] = [(hex_coords[0],)]
    for i in range(length - 1):
        vertices.append((hex_coords[i], hex_coords[i + 1]))
    vertices.append((hex_coords[-1],))

    edges: list[EdgeId] = [(edge_hexes[i], edge_hexes[i + 1]) for i in range(length)]

    vertex_neighbors: dict[VertexId, list[VertexId]] = {v: [] for v in vertices}
    for i in range(length):
        vertex_neighbors[vertices[i]].append(vertices[i + 1])
        vertex_neighbors[vertices[i + 1]].append(vertices[i])

    edge_endpoints: dict[EdgeId, tuple[VertexId, VertexId]] = {
        edges[i]: (vertices[i], vertices[i + 1]) for i in range(length)
    }

    adjacent_edges: dict[HexCoord, list[EdgeId]] = {h: [] for h in edge_hexes}
    for i, e in enumerate(edges):
        adjacent_edges[edge_hexes[i]].append(e)
        adjacent_edges[edge_hexes[i + 1]].append(e)

    adjacent_vertices: dict[HexCoord, list[VertexId]] = {h: [] for h in hex_coords}
    for v in vertices:
        for h in v:
            adjacent_vertices.setdefault(h, [])
            if v not in adjacent_vertices[h]:
                adjacent_vertices[h].append(v)

    hexes = {h: HexTile(coord=h, terrain=Terrain.FOREST, number_token=None) for h in hex_coords}

    return {
        "hexes": hexes,
        "vertices": vertices,
        "edges": edges,
        "vertex_neighbors": vertex_neighbors,
        "edge_endpoints": edge_endpoints,
        "adjacent_edges": adjacent_edges,
        "adjacent_vertices": adjacent_vertices,
    }


def _combine(*chains: dict) -> dict:
    combined = {
        "hexes": {},
        "vertex_neighbors": {},
        "edge_endpoints": {},
        "adjacent_edges": {},
        "adjacent_vertices": {},
    }
    for chain in chains:
        combined["hexes"].update(chain["hexes"])
        combined["vertex_neighbors"].update(chain["vertex_neighbors"])
        combined["edge_endpoints"].update(chain["edge_endpoints"])
        combined["adjacent_edges"].update(chain["adjacent_edges"])
        combined["adjacent_vertices"].update(chain["adjacent_vertices"])
    return combined


def _patch_geometry(monkeypatch, geometry: dict) -> None:
    monkeypatch.setattr(
        board_mod, "vertex_neighbors", lambda v: geometry["vertex_neighbors"].get(v, [])
    )
    monkeypatch.setattr(board_mod, "edge_endpoints", lambda e: geometry["edge_endpoints"][e])
    monkeypatch.setattr(
        board_mod, "get_adjacent_edges", lambda h: geometry["adjacent_edges"].get(h, [])
    )
    monkeypatch.setattr(
        board_mod, "get_adjacent_vertices", lambda h: geometry["adjacent_vertices"].get(h, [])
    )


def _make_state(geometry: dict, num_players: int = 2) -> GameState:
    board = Board(
        hexes=geometry["hexes"],
        ports=[],
        buildings={},
        roads={},
        robber_hex=next(iter(geometry["hexes"])),
    )
    player_ids = [f"p{i}" for i in range(num_players)]
    players = {
        pid: PlayerState(player_id=pid, nickname=pid, seat=i) for i, pid in enumerate(player_ids)
    }
    return GameState(
        room_code="TEST",
        settings=GameSettings(player_count=num_players),
        phase=Phase.MAIN,
        turn_order=player_ids,
        board=board,
        bank=Bank(resources={r: 19 for r in ResourceType}),
        players=players,
    )


# ---------------------------------------------------------------------
# Victory points
# ---------------------------------------------------------------------


def test_victory_points_from_settlements_and_cities(monkeypatch):
    chain = _make_chain(0, 6)
    _patch_geometry(monkeypatch, chain)
    state = _make_state(chain)
    vertices = chain["vertices"]

    state.board.buildings[vertices[0]] = VertexBuilding(
        player_id="p0", building_type=BuildingType.SETTLEMENT
    )
    state.board.buildings[vertices[2]] = VertexBuilding(
        player_id="p0", building_type=BuildingType.CITY
    )
    state.board.buildings[vertices[4]] = VertexBuilding(
        player_id="p1", building_type=BuildingType.SETTLEMENT
    )

    scoring.recompute_victory_points(state)

    assert state.players["p0"].victory_points == 3  # 1 settlement + 1 city (2)
    assert state.players["p1"].victory_points == 1


def test_hidden_victory_points_only_count_for_own_win_check(monkeypatch):
    chain = _make_chain(0, 4)
    _patch_geometry(monkeypatch, chain)
    state = _make_state(chain)

    state.players["p0"].dev_cards[DevCardType.VICTORY_POINT] = 2
    state.players["p0"].dev_cards_bought_this_turn[DevCardType.VICTORY_POINT] = 1

    scoring.recompute_victory_points(state)

    assert state.players["p0"].victory_points == 0
    assert scoring.total_victory_points_including_hidden(state, "p0") == 3
    assert scoring.total_victory_points_including_hidden(state, "p1") == 0


# ---------------------------------------------------------------------
# Longest road
# ---------------------------------------------------------------------


def test_longest_road_not_awarded_below_minimum_length(monkeypatch):
    chain = _make_chain(0, 6)
    _patch_geometry(monkeypatch, chain)
    state = _make_state(chain)

    for edge_id in chain["edges"][:4]:  # length 4, below the minimum of 5
        state.board.roads[edge_id] = "p0"

    holder = scoring.recompute_longest_road(state)

    assert holder is None
    assert state.longest_road_holder is None
    assert state.players["p0"].has_longest_road is False


def test_longest_road_awarded_at_minimum_length(monkeypatch):
    chain = _make_chain(0, 6)
    _patch_geometry(monkeypatch, chain)
    state = _make_state(chain)

    for edge_id in chain["edges"][:5]:  # exactly length 5
        state.board.roads[edge_id] = "p0"

    holder = scoring.recompute_longest_road(state)

    assert holder == "p0"
    assert state.longest_road_holder == "p0"
    assert state.players["p0"].has_longest_road is True
    assert state.players["p0"].victory_points == scoring.LONGEST_ROAD_VP


def test_longest_road_tie_stays_with_current_holder_then_flips_on_strict_lead(monkeypatch):
    chain_a = _make_chain(0, 6)
    chain_b = _make_chain(20, 6)
    geometry = _combine(chain_a, chain_b)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry)

    for edge_id in chain_a["edges"][:5]:
        state.board.roads[edge_id] = "p0"
    scoring.recompute_longest_road(state)
    assert state.longest_road_holder == "p0"

    # p1 matches p0's length (5, 5) -- tie stays with the current holder.
    for edge_id in chain_b["edges"][:5]:
        state.board.roads[edge_id] = "p1"
    scoring.recompute_longest_road(state)
    assert state.longest_road_holder == "p0"

    # p1 strictly exceeds -- takes over.
    state.board.roads[chain_b["edges"][5]] = "p1"
    scoring.recompute_longest_road(state)
    assert state.longest_road_holder == "p1"
    assert state.players["p0"].has_longest_road is False
    assert state.players["p1"].has_longest_road is True


def test_longest_road_recompute_after_road_removed_changes_holder(monkeypatch):
    """A road is removed (e.g. by Nuke Mode) mid-chain, splitting the
    current holder's network into two shorter segments and dropping them
    below the eligibility threshold -- the title passes to whichever
    other player still qualifies.
    """
    chain_a = _make_chain(0, 6)
    chain_b = _make_chain(20, 6)
    geometry = _combine(chain_a, chain_b)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry)

    for edge_id in chain_a["edges"][:6]:  # length 6
        state.board.roads[edge_id] = "p0"
    for edge_id in chain_b["edges"][:5]:  # length 5
        state.board.roads[edge_id] = "p1"
    scoring.recompute_longest_road(state)
    assert state.longest_road_holder == "p0"

    # Remove the middle segment of p0's road: splits length-6 into 3 + 2.
    del state.board.roads[chain_a["edges"][3]]
    holder = scoring.recompute_longest_road(state)

    assert holder == "p1"
    assert state.longest_road_holder == "p1"
    assert state.players["p0"].has_longest_road is False
    assert state.players["p1"].has_longest_road is True


def test_longest_road_broken_by_opponent_settlement(monkeypatch):
    chain = _make_chain(0, 6)
    _patch_geometry(monkeypatch, chain)
    state = _make_state(chain)
    vertices = chain["vertices"]

    for edge_id in chain["edges"]:  # length 6, V0..V6
        state.board.roads[edge_id] = "p0"
    scoring.recompute_longest_road(state)
    assert state.longest_road_holder == "p0"

    # p1 builds a settlement at V3 (the midpoint), cutting p0's road into
    # two segments of length 3 each -- both now below the minimum of 5.
    state.board.buildings[vertices[3]] = VertexBuilding(
        player_id="p1", building_type=BuildingType.SETTLEMENT
    )
    holder = scoring.recompute_longest_road(state)

    assert holder is None
    assert state.longest_road_holder is None


# ---------------------------------------------------------------------
# Largest army
# ---------------------------------------------------------------------


def test_largest_army_threshold_and_tiebreak(monkeypatch):
    chain = _make_chain(0, 2)
    _patch_geometry(monkeypatch, chain)
    state = _make_state(chain)

    state.players["p0"].knights_played = 2
    scoring.recompute_largest_army(state)
    assert state.largest_army_holder is None  # below the minimum of 3

    state.players["p0"].knights_played = 3
    scoring.recompute_largest_army(state)
    assert state.largest_army_holder == "p0"
    assert state.players["p0"].has_largest_army is True
    assert state.players["p0"].victory_points == scoring.LARGEST_ARMY_VP

    state.players["p1"].knights_played = 3
    scoring.recompute_largest_army(state)
    assert state.largest_army_holder == "p0"  # tie stays with current holder

    state.players["p1"].knights_played = 4
    scoring.recompute_largest_army(state)
    assert state.largest_army_holder == "p1"
    assert state.players["p0"].has_largest_army is False
    assert state.players["p1"].has_largest_army is True
