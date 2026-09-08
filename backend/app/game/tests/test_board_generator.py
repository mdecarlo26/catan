"""Tests for board geometry derivation (`app.game.board`) and the
parametric board generator + layout registry (`app.game.board_generator`,
`app.game.rules.board_layouts`).

Covers:
- hex/vertex/edge id derivation correctness (a vertex shared by 3
  mutually-adjacent hexes gets a 3-entry id, boundary vertices trim to
  2-entry ids, adjacency is symmetric).
- correct hex count and port count per player-count bucket (2-8
  players).
- no invalid number-token adjacency (no two 6/8 tokens on adjacent
  hexes) in generated boards.
- "fixed" layout mode is deterministic across calls; "random" varies.
"""

from __future__ import annotations

import pytest

from app.game.board import (
    Board,
    HexCoord,
    Terrain,
    edge_endpoints,
    get_adjacent_edges,
    get_adjacent_vertices,
    hex_neighbors,
    vertex_neighbors,
)
from app.game.board_generator import (
    DEFAULT_LAYOUT_BY_BUCKET,
    GENERATION_MODES,
    LAYOUT_REGISTRY,
    generate_board,
    get_layout_spec,
    player_count_bucket,
)


# ---------------------------------------------------------------------
# Geometry: hex/vertex/edge id derivation
# ---------------------------------------------------------------------


def test_hex_neighbors_returns_six_unique_coords():
    origin = HexCoord(0, 0)
    neighbors = hex_neighbors(origin)
    assert len(neighbors) == 6
    assert len(set(neighbors)) == 6
    # every neighbor really is one step away (symmetric: origin is also
    # a neighbor of each of its neighbors).
    for n in neighbors:
        assert origin in hex_neighbors(n)


def test_get_adjacent_vertices_returns_six_full_triples_containing_hex():
    h = HexCoord(0, 0)
    vertices = get_adjacent_vertices(h)
    assert len(vertices) == 6
    assert len(set(vertices)) == 6
    for v in vertices:
        assert len(v) == 3
        assert h in v
        # ids are ascending-sorted tuples of HexCoord.
        assert list(v) == sorted(v)


def test_get_adjacent_edges_returns_six_pairs_containing_hex():
    h = HexCoord(1, -1)
    edges = get_adjacent_edges(h)
    assert len(edges) == 6
    assert len(set(edges)) == 6
    for e in edges:
        assert len(e) == 2
        assert h in e
        assert tuple(e) == tuple(sorted(e))


def test_vertex_shared_by_three_mutually_adjacent_hexes_has_three_entries():
    # Three known mutually-adjacent hexes around the origin.
    a, b, c = HexCoord(0, 0), HexCoord(1, 0), HexCoord(1, -1)
    assert b in hex_neighbors(a)
    assert c in hex_neighbors(a)
    assert c in hex_neighbors(b)

    shared_vertex = tuple(sorted((a, b, c)))
    # Every one of the 3 hexes' own get_adjacent_vertices output must
    # include this exact corner -- deriving from any of the 3 hexes
    # produces the same id (per the module docstring's "same physical
    # corner always produces the same id" guarantee).
    assert shared_vertex in get_adjacent_vertices(a)
    assert shared_vertex in get_adjacent_vertices(b)
    assert shared_vertex in get_adjacent_vertices(c)
    assert len(shared_vertex) == 3


def test_vertex_trims_to_two_entries_on_a_boundary():
    # `a` and `b` are adjacent and present; of their two common
    # neighbors (the hexes completing each of the 2 vertices on the a-b
    # edge), only one (`c_present`) is present. That corner's theoretical
    # 3rd hex (`c_absent`) is missing from this tiny board, so it must
    # trim down to a 2-entry id containing exactly the present hexes --
    # while the *other* end of the same edge, whose 3rd hex *is*
    # present, stays a full 3-entry id. (Leaving both common neighbors
    # absent would collide the two ends of the edge onto the same
    # 2-entry id -- exactly the degenerate case `board_generator`'s sea
    # border exists to avoid; picking one present neighbor here tests
    # the well-defined, unambiguous boundary case instead.)
    a, b = HexCoord(0, 0), HexCoord(1, 0)
    common = sorted(set(hex_neighbors(a)) & set(hex_neighbors(b)))
    assert len(common) == 2
    c_present, c_absent = common
    present = {a, b, c_present}

    trimmed_candidates = set()
    for v in get_adjacent_vertices(a):
        trimmed = tuple(sorted(c for c in v if c in present))
        trimmed_candidates.add(trimmed)

    assert tuple(sorted((a, b, c_present))) in trimmed_candidates
    # The edge-(a,b) corner whose 3rd hex (c_absent) is missing trims to
    # exactly the present pair.
    assert tuple(sorted((a, b))) in trimmed_candidates
    for v in trimmed_candidates:
        if len(v) == 2:
            assert set(v) <= present and len(set(v)) == 2


def test_edge_endpoints_are_the_two_common_neighbor_completions():
    a, b = HexCoord(0, 0), HexCoord(1, 0)
    endpoints = edge_endpoints((a, b))
    assert len(endpoints) == 2
    v1, v2 = endpoints
    assert v1 != v2
    for v in endpoints:
        assert len(v) == 3
        assert a in v and b in v
    # The edge (a, b) must itself appear in each endpoint's own
    # get_adjacent_vertices, i.e. edge_endpoints and
    # get_adjacent_vertices agree with each other.
    assert v1 in get_adjacent_vertices(a)
    assert v2 in get_adjacent_vertices(a)


def test_get_adjacent_edges_matches_edge_endpoints_round_trip():
    h = HexCoord(0, 0)
    for e in get_adjacent_edges(h):
        v1, v2 = edge_endpoints(e)
        # both endpoints of an edge bordering h must contain h.
        assert h in v1
        assert h in v2


def test_vertex_neighbors_symmetric_for_interior_vertices():
    # Build a small cluster of mutually-adjacent hexes around the origin
    # (the origin hex plus all 6 neighbors) so we have genuine interior
    # (3-hex) vertices to check.
    origin = HexCoord(0, 0)
    cluster = {origin} | set(hex_neighbors(origin))

    interior_vertices = set()
    for h in cluster:
        for v in get_adjacent_vertices(h):
            if set(v) <= cluster:
                interior_vertices.add(v)

    assert interior_vertices  # sanity: we actually found some

    for v in interior_vertices:
        for n in vertex_neighbors(v):
            if n not in interior_vertices:
                continue
            assert v in vertex_neighbors(n), f"{v} -> {n} not symmetric"


# ---------------------------------------------------------------------
# Board generator: hex/port counts per bucket
# ---------------------------------------------------------------------


@pytest.mark.parametrize(
    "player_count,expected_bucket,expected_hex_count",
    [
        (2, "2p", 19),
        (3, "3-4p", 19),
        (4, "3-4p", 19),
        (5, "5-6p", 30),
        (6, "5-6p", 30),
        (7, "7-8p", 44),
        (8, "7-8p", 44),
    ],
)
def test_hex_count_per_player_count_bucket(
    player_count, expected_bucket, expected_hex_count
):
    assert player_count_bucket(player_count) == expected_bucket
    board = generate_board(player_count, mode="fixed")
    land_hexes = [
        h for h, tile in board.hexes.items() if tile.terrain != Terrain.SEA
    ]
    assert len(land_hexes) == expected_hex_count


@pytest.mark.parametrize(
    "player_count,expected_port_count",
    [
        (2, 9),
        (3, 9),
        (4, 9),
        (5, 11),
        (6, 11),
        (7, 13),
        (8, 13),
    ],
)
def test_port_count_per_player_count_bucket(player_count, expected_port_count):
    board = generate_board(player_count, mode="fixed")
    assert len(board.ports) == expected_port_count


def test_two_player_bucket_reuses_the_standard_19_hex_shape():
    standard_spec = get_layout_spec("3-4p", "standard")
    two_player_spec = get_layout_spec("2p", "two_player")
    assert two_player_spec.hex_count == standard_spec.hex_count == 19
    assert two_player_spec.terrain_counts == standard_spec.terrain_counts
    assert DEFAULT_LAYOUT_BY_BUCKET["2p"] == "two_player"


def test_registry_has_one_default_layout_per_bucket():
    for bucket in ("2p", "3-4p", "5-6p", "7-8p"):
        assert bucket in DEFAULT_LAYOUT_BY_BUCKET
        layout_name = DEFAULT_LAYOUT_BY_BUCKET[bucket]
        assert (bucket, layout_name) in LAYOUT_REGISTRY


def test_unsupported_player_count_raises():
    with pytest.raises(ValueError):
        player_count_bucket(1)
    with pytest.raises(ValueError):
        player_count_bucket(9)


# ---------------------------------------------------------------------
# Number token placement
# ---------------------------------------------------------------------


def _has_adjacent_six_or_eight(board: Board) -> bool:
    numbers = {
        h: tile.number_token
        for h, tile in board.hexes.items()
        if tile.number_token is not None
    }
    for h, num in numbers.items():
        if num not in (6, 8):
            continue
        for n in hex_neighbors(h):
            if numbers.get(n) in (6, 8):
                return True
    return False


@pytest.mark.parametrize("player_count", [2, 3, 4, 5, 6, 7, 8])
def test_no_adjacent_six_or_eight_random_mode(player_count):
    # A handful of distinct seeds per bucket to exercise the placement
    # algorithm's retry logic across different shuffles.
    for seed in range(5):
        board = generate_board(player_count, mode="random", seed=seed)
        assert not _has_adjacent_six_or_eight(board)


@pytest.mark.parametrize("player_count", [2, 4, 6, 8])
def test_no_adjacent_six_or_eight_fixed_mode(player_count):
    board = generate_board(player_count, mode="fixed")
    assert not _has_adjacent_six_or_eight(board)


def test_number_tokens_never_placed_on_desert_or_sea():
    board = generate_board(4, mode="fixed")
    for h, tile in board.hexes.items():
        if tile.terrain in (Terrain.DESERT, Terrain.SEA):
            assert tile.number_token is None
        else:
            assert tile.number_token is not None
            assert 2 <= tile.number_token <= 12
            assert tile.number_token != 7


def test_robber_starts_on_a_desert_hex():
    board = generate_board(4, mode="fixed")
    assert board.hexes[board.robber_hex].terrain == Terrain.DESERT


# ---------------------------------------------------------------------
# Fixed vs. random determinism
# ---------------------------------------------------------------------


def test_fixed_mode_is_deterministic_across_calls():
    b1 = generate_board(4, mode="fixed")
    b2 = generate_board(4, mode="fixed")
    assert b1.model_dump() == b2.model_dump()


def test_fixed_mode_is_deterministic_per_bucket():
    # Every bucket's fixed board should also be internally reproducible.
    for player_count in (2, 5, 7):
        b1 = generate_board(player_count, mode="fixed")
        b2 = generate_board(player_count, mode="fixed")
        assert b1.model_dump() == b2.model_dump()


def test_random_mode_varies_across_calls():
    boards = [generate_board(4, mode="random") for _ in range(5)]
    dumps = [b.model_dump() for b in boards]
    # Not every pair need differ (astronomically unlikely to collide,
    # but we only assert at least one difference across the sample to
    # avoid any flakiness).
    assert len(set(str(d) for d in dumps)) > 1


def test_random_mode_with_explicit_seed_is_reproducible():
    # `seed` is an optional escape hatch for reproducible "random" mode
    # boards (used by the adjacency test above); pinning it must behave
    # deterministically just like "fixed" mode does.
    b1 = generate_board(4, mode="random", seed=123)
    b2 = generate_board(4, mode="random", seed=123)
    assert b1.model_dump() == b2.model_dump()


def test_unknown_generation_mode_rejected():
    with pytest.raises(ValueError):
        generate_board(4, mode="bogus")


# ---------------------------------------------------------------------
# Vertex/edge universe sanity on a real generated board
# ---------------------------------------------------------------------


def _land_vertex_universe(board: Board) -> set:
    presence = set(board.hexes.keys())
    land = {h for h, t in board.hexes.items() if t.terrain != Terrain.SEA}
    vertices = set()
    for h in land:
        for candidate in get_adjacent_vertices(h):
            trimmed = tuple(sorted(c for c in candidate if c in presence))
            if len(trimmed) >= 2:
                vertices.add(trimmed)
    return vertices


def test_standard_board_has_classic_54_vertices_and_72_edges():
    board = generate_board(4, mode="fixed")
    vertices = _land_vertex_universe(board)
    land = {h for h, t in board.hexes.items() if t.terrain != Terrain.SEA}
    edges = set()
    for h in land:
        edges.update(get_adjacent_edges(h))

    assert len(vertices) == 54
    assert len(edges) == 72
    # No degenerate (single-hex) vertex ids -- see the board_generator
    # module docstring on why the sea border exists.
    assert all(len(v) in (2, 3) for v in vertices)


def test_vertex_neighbors_symmetric_on_generated_board():
    board = generate_board(4, mode="fixed")
    vertices = _land_vertex_universe(board)
    for v in vertices:
        for n in vertex_neighbors(v):
            if n not in vertices:
                continue
            assert v in vertex_neighbors(n)


def test_ports_anchor_to_real_vertices_with_valid_ids():
    board = generate_board(5, mode="fixed")
    vertices = _land_vertex_universe(board)
    assert len(board.ports) > 0
    for port in board.ports:
        assert len(port.vertices) == 2
        for v in port.vertices:
            assert v in vertices
            assert len(v) in (2, 3)
