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
This module defines only the static data shapes (`HexTile`, `Port`,
`Board`, ...) plus the frozen geometry function *signatures* other
modules (`board_generator.py`, `rules_engine.py`, the frontend) are
written against. It intentionally contains **no geometry implementation**
-- every function below raises ``NotImplementedError`` and is Wave 1
work (see ``AGENT_BUILD_PROMPTS.md``, "Board & Board Generator").

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


def get_adjacent_vertices(hex: HexCoord) -> list[VertexId]:
    """Return the ids of the (up to 6) vertices at the corners of ``hex``.

    Implementation is Wave 1 work (see module docstring). Once
    implemented, callers can rely on: the returned list has one entry per
    corner of the hexagon at ``hex`` that is a valid vertex on the board
    the caller is working with, and every returned id contains ``hex``
    itself as one of its (2 or 3) component `HexCoord` values.
    """
    raise NotImplementedError


def get_adjacent_edges(hex: HexCoord) -> list[EdgeId]:
    """Return the ids of the (up to 6) edges bordering ``hex``.

    Implementation is Wave 1 work. Once implemented, every returned id is
    a 2-tuple containing ``hex`` as one of its two component
    `HexCoord` values.
    """
    raise NotImplementedError


def vertex_neighbors(v: VertexId) -> list[VertexId]:
    """Return the vertex ids directly connected to ``v`` by a single edge.

    A vertex has degree 3 in the interior of the board (fewer along the
    board boundary). Implementation is Wave 1 work.
    """
    raise NotImplementedError


def edge_endpoints(e: EdgeId) -> tuple[VertexId, VertexId]:
    """Return the two vertex ids that ``e`` connects.

    Implementation is Wave 1 work.
    """
    raise NotImplementedError
