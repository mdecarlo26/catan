"""Board geometry & static layout types for Catan.

Coordinate system
------------------
Hexes use **axial coordinates** ``(q, r)`` (see redblobgames.com/grids/hexagons
for the standard reference this project follows). :class:`HexCoord` is a
plain ``(q, r)`` ``NamedTuple`` -- hashable and orderable by default tuple
comparison, so it can be used directly as a dict key, inside ``set``s, and
inside the sorted tuples that make up vertex/edge ids.

Vertex and edge ids are *derived*, never stored independently, and both
sides of this project (Python backend, TypeScript frontend) MUST derive
them the same way from the same board data so no floating-point geometry
comparison is ever required over the wire:

- A **vertex** sits at a corner shared by up to three mutually-adjacent
  hexes (in a full/infinite hex tiling, exactly three hexes always meet at
  every vertex). Its id, :data:`VertexId`, is the ascending-sorted tuple
  of the :class:`HexCoord` values of the hexes that are (a) mutually
  adjacent at that corner and (b) actually present as tiles on *this*
  board. An interior vertex therefore has a 3-tuple id; a vertex on the
  outer boundary of the play area, where one of the three theoretical
  neighbouring hex positions has no tile placed on this board, has a
  2-tuple id instead. Vertex ids are always sorted ascending so the same
  physical corner always produces the same id regardless of which
  neighbouring hex you started walking from.
- An **edge** sits on the border between exactly two adjacent hex
  positions (this is always true, even at the board boundary, since the
  two hex coordinate slots on either side of an edge exist in coordinate
  space regardless of whether a tile has been placed there). Its id,
  :data:`EdgeId`, is the ascending-sorted 2-tuple of those two hexes'
  :class:`HexCoord` values.

Because both ids are derived purely from hex-coordinate adjacency, a
backend and a frontend that start from the same set of hex coordinates
and apply the same adjacency rules always agree on ids independently.
The TypeScript mirror of ``HexCoord`` / ``VertexId`` / ``EdgeId`` lives in
``frontend/src/types/protocol.ts``; the mirror of the derivation
*functions* declared below belongs in the frontend board module (see
``frontend/src/board/``) and MUST stay in lockstep with whatever
implementation eventually fills in the bodies here.

Scope of this module
---------------------
This module defines the static data shapes (`HexTile`, `Port`, `Board`,
...) plus the geometry functions other modules (`board_generator.py`,
`rules_engine.py`, the frontend) are written against. The function
*signatures* were frozen in Wave 0; their bodies (below) are the Wave 1
"Board & Board Generator" implementation (see ``AGENT_BUILD_PROMPTS.md``).
`get_adjacent_vertices`/`get_adjacent_edges` are pure, board-agnostic
hex-tiling geometry (they always return the full theoretical
corners/edges of a hex, since a bare `HexCoord` carries no information
about which other hexes are actually tiles on a given `Board`); trimming
a vertex down to the 2-hex boundary form described below happens in
`board_generator.py`, which has that board-wide context. See each
function's own docstring for the exact contract it implements.

Note on JSON wire safety
-------------------------
``HexCoord``/``VertexId``/``EdgeId`` are tuples, which are not valid JSON
object keys. The models in this module (``Board`` in particular) are the
backend's *internal* in-memory representation and are never serialized
to JSON directly -- ``app.game.state.GameState`` (which embeds ``Board``)
is only ever turned into wire data via
``app.game.serialization.to_client_view()``, which produces the
JSON-safe, list-of-records shapes declared in ``app.protocol.events``
(see ``WireBoardView`` there). Keep that separation in mind: don't
``model_dump(mode="json")`` a ``Board`` directly.
"""

from typing import NamedTuple, TypeAlias

from pydantic import BaseModel, ConfigDict, Field
from enum import Enum


class HexCoord(NamedTuple):
    """Axial coordinate of a single hex tile.

    ``q`` is the column axis, ``r`` is the row axis, following the
    standard axial layout described at redblobgames.com/grids/hexagons.
    Being a ``NamedTuple`` makes this hashable and orderable (ascending by
    ``q`` then ``r``) for free, which is exactly what sorted vertex/edge
    id construction relies on.
    """

    q: int
    r: int


# A vertex id is the ascending-sorted tuple of the HexCoords of the 2 or 3
# hexes (that actually have tiles on this board) meeting at that corner.
# Length is always 2 or 3 -- see the module docstring for why.
VertexId: TypeAlias = tuple[HexCoord, ...]

# An edge id is the ascending-sorted 2-tuple of the HexCoords of the two
# hex positions the edge separates.
EdgeId: TypeAlias = tuple[HexCoord, HexCoord]

# A player id is a uuid4 string, issued by the server on JOIN_ROOM and
# stable across reconnects (unlike the underlying WebSocket connection).
PlayerId: TypeAlias = str


class Terrain(str, Enum):
    """Terrain type of a single hex tile.

    The five resource terrains each produce exactly one `ResourceType`
    (see `app.game.players.ResourceType`) on a matching dice roll:
    FOREST -> lumber, HILLS -> brick, MOUNTAINS -> ore, FIELDS -> grain,
    PASTURE -> wool. DESERT produces nothing and is the tile the robber
    starts on. SEA is not a resource-producing land tile; it represents
    ocean/border hexes some board layouts use to anchor port placement
    for rendering purposes and never has a ``number_token``.
    """

    FOREST = "forest"
    HILLS = "hills"
    MOUNTAINS = "mountains"
    FIELDS = "fields"
    PASTURE = "pasture"
    DESERT = "desert"
    SEA = "sea"


class PortType(str, Enum):
    """Trade ratio a `Port` offers.

    GENERIC is the standard 3:1-any-resource port. The other five values
    are the 2:1 single-resource ports, one per `ResourceType`.
    """

    GENERIC = "generic"
    BRICK = "brick"
    LUMBER = "lumber"
    ORE = "ore"
    GRAIN = "grain"
    WOOL = "wool"


class BuildingType(str, Enum):
    """What, if anything, is built on a given vertex."""

    SETTLEMENT = "settlement"
    CITY = "city"


class HexTile(BaseModel):
    """A single placed hex on the board."""

    model_config = ConfigDict(frozen=True)

    coord: HexCoord
    terrain: Terrain

    #: The dice-roll number token (2-12, never 7) painted on this tile.
    #: ``None`` for `Terrain.DESERT` and `Terrain.SEA`, which never
    #: produce resources and are never assigned a token.
    number_token: int | None = None


class Port(BaseModel):
    """A trading port anchored to two adjacent coastal vertices.

    A player with a settlement or city on *either* vertex in
    :attr:`vertices` may trade at this port's rate. Whether a port is
    still usable is therefore always derived live from current vertex
    ownership (see `Board.buildings`), never cached on `Port` itself --
    this is what lets Nuke Mode's "port access lost if the anchoring
    building is destroyed" rule fall out for free instead of needing
    special-case invalidation logic.
    """

    model_config = ConfigDict(frozen=True)

    port_type: PortType
    vertices: tuple[VertexId, VertexId]


class VertexBuilding(BaseModel):
    """A settlement or city built at a vertex, and who owns it."""

    player_id: PlayerId
    building_type: BuildingType


class Board(BaseModel):
    """The full physical board: static layout plus what has been built.

    ``hexes`` and ``ports`` are fixed once the board is generated at game
    start (see ``board_generator.py``). ``buildings`` and ``roads`` are
    mutated over the course of the game as players build, and by
    Nuke Mode when a piece is destroyed and its vertex/edge re-enters the
    pool of legal build spots. ``robber_hex`` moves whenever a 7 is
    rolled or a Knight is played.

    All dict keys here (`HexCoord`, `VertexId`, `EdgeId`) are derived per
    the coordinate-system convention documented at the top of this
    module. See the module docstring's "Note on JSON wire safety" -- this
    model is for internal use only and is never serialized to JSON
    as-is.
    """

    hexes: dict[HexCoord, HexTile]
    ports: list[Port]
    buildings: dict[VertexId, VertexBuilding] = Field(default_factory=dict)
    #: Edge id -> the player_id who built a road there.
    roads: dict[EdgeId, PlayerId] = Field(default_factory=dict)
    robber_hex: HexCoord


#: The six axial-coordinate unit direction vectors, in a consistent
#: cyclic (winding) order -- i.e. direction ``i`` and direction
#: ``(i + 1) % 6`` are themselves always mutually-adjacent hex
#: coordinates. That cyclic-adjacency property is exactly what makes
#: ``{hex, hex + DIRECTIONS[i], hex + DIRECTIONS[(i + 1) % 6]}`` a valid
#: mutually-adjacent 3-hex vertex for every ``i`` -- see
#: ``get_adjacent_vertices`` below. Order/orientation follows the axial
#: layout described at redblobgames.com/grids/hexagons; any internally
#: consistent cyclic order works equally well since nothing outside this
#: module depends on a particular winding direction.
_DIRECTIONS: tuple[tuple[int, int], ...] = (
    (1, 0),
    (1, -1),
    (0, -1),
    (-1, 0),
    (-1, 1),
    (0, 1),
)


def hex_neighbors(hex: HexCoord) -> list[HexCoord]:
    """Return the (always 6) axial coordinates adjacent to ``hex``.

    Unlike `get_adjacent_vertices`/`get_adjacent_edges`, this is not part
    of the Wave 0 frozen contract -- it's a small additional pure-geometry
    helper (no board/tile awareness) that `board_generator.py` and this
    module's own vertex/edge derivation both build on, so the single
    direction-vector table above has one implementation.
    """
    return [HexCoord(hex.q + dq, hex.r + dr) for dq, dr in _DIRECTIONS]


def _common_hex_neighbors(a: HexCoord, b: HexCoord) -> list[HexCoord]:
    """The hexes that are neighbors of both ``a`` and ``b``.

    For any two mutually-adjacent hexes in an (infinite) hex tiling this
    is always exactly the two hexes that, together with ``a`` and ``b``,
    form the two vertices at either end of the ``a``-``b`` edge -- e.g.
    `edge_endpoints` is built directly on this.
    """
    neighbors_of_a = set(hex_neighbors(a))
    neighbors_of_b = set(hex_neighbors(b))
    return sorted(neighbors_of_a & neighbors_of_b)


def _vertex_id(hexes: tuple[HexCoord, ...]) -> VertexId:
    return tuple(sorted(set(hexes)))


def get_adjacent_vertices(hex: HexCoord) -> list[VertexId]:
    """Return the ids of the (up to 6) vertices at the corners of ``hex``.

    This is pure hex-tiling geometry with no notion of which hexes are
    actually present as tiles on any particular `Board` -- it always
    returns the 6 full, 3-hex theoretical corners of ``hex`` in an
    (infinite) tiling, one per pair of cyclically-consecutive neighbors
    (see `_DIRECTIONS`). Trimming a corner down to the 2-hex boundary
    form described in the module docstring is a board-aware step (a
    corner's third hex isn't actually a tile on *this* board) that
    belongs to whatever is assembling a concrete `Board` --
    `board_generator.py` does this when it builds a board's real vertex
    set, by intersecting each returned id's hexes with the board's actual
    `Board.hexes` keys.

    Every returned id contains ``hex`` itself as one of its 3 component
    `HexCoord` values, per the contract.
    """
    neighbors = hex_neighbors(hex)
    return [
        _vertex_id((hex, neighbors[i], neighbors[(i + 1) % 6]))
        for i in range(6)
    ]


def get_adjacent_edges(hex: HexCoord) -> list[EdgeId]:
    """Return the ids of the (up to 6) edges bordering ``hex``.

    Every returned id is a 2-tuple containing ``hex`` as one of its two
    component `HexCoord` values. Per the module docstring, edges are
    never trimmed for board presence -- the hex coordinate slot on the
    far side of a boundary edge exists in coordinate space regardless of
    whether a tile has been placed there (this is what lets a road be
    built along the outer edge of the play area).
    """
    return [tuple(sorted((hex, neighbor))) for neighbor in hex_neighbors(hex)]


def vertex_neighbors(v: VertexId) -> list[VertexId]:
    """Return the vertex ids directly connected to ``v`` by a single edge.

    A vertex has degree 3 in the interior of the board (fewer along the
    board boundary). For a 3-hex ``v`` this is unambiguous: each of the
    3 pairs of hexes within ``v`` borders exactly one other vertex
    (``edge_endpoints`` of that pair's edge, discarding ``v`` itself).

    For a 2-hex boundary ``v`` (one theoretical third hex missing from
    the board), which of ``v``'s two hexes' *other* common neighbor is
    the "real" missing hex isn't recoverable from ``v`` alone -- this
    function has no board to check presence against (see
    `get_adjacent_vertices`). It resolves this safely by considering
    *both* theoretical completions of ``v`` and returning the union of
    their neighbors: every genuinely-on-board neighbor of ``v`` is
    guaranteed to be included, and any extra candidates this produces
    reference hex coordinates that are absent from the board, so they can
    never collide with a real vertex id a caller looks up (e.g. in
    `Board.buildings`).
    """
    hexes = list(v)
    if len(hexes) == 3:
        completions = [hexes]
    else:
        a, b = hexes
        completions = [[a, b, c] for c in _common_hex_neighbors(a, b)]

    results: set[VertexId] = set()
    for triple in completions:
        for i in range(3):
            pair = [triple[j] for j in range(3) if j != i]
            excluded = triple[i]
            for candidate in _common_hex_neighbors(pair[0], pair[1]):
                if candidate != excluded:
                    results.add(_vertex_id((pair[0], pair[1], candidate)))
    results.discard(v)
    return sorted(results)


def edge_endpoints(e: EdgeId) -> tuple[VertexId, VertexId]:
    """Return the two vertex ids that ``e`` connects.

    Like `get_adjacent_vertices`, this returns the full, untrimmed 3-hex
    theoretical vertex ids -- the two hexes adjacent to *both* ends of
    ``e`` (always exactly two, for any pair of mutually-adjacent hexes in
    a hex tiling).
    """
    a, b = e
    ends = sorted(_vertex_id((a, b, c)) for c in _common_hex_neighbors(a, b))
    return (ends[0], ends[1])
