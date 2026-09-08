"""Parametric board generator.

One ring-based shape/terrain/number/port algorithm, driven by a small
per-`(player_count_bucket, layout_name)` lookup table (`LayoutSpec`,
registered by the modules under `app.game.rules.board_layouts`), rather
than a hand-authored board per player count. See the plan's "Board
Layout for 2p and 7-8p" section in ``ARCHITECTURE.md``.

Shape algorithm
----------------
`generate_hex_shape` fills complete concentric hex rings around the
origin and then, if the target hex count doesn't land exactly on a
completed ring, adds a symmetric subset of the next (partial) ring --
this is the "3 rings/19 hex (2-4p), 4 rings/30 hex (5-6p official), 5
rings/~42-46 hex (7-8p)" pattern from one parametric algorithm.

Vertex/edge ids and the "sea border"
-------------------------------------
`app.game.board.get_adjacent_vertices`/`get_adjacent_edges` are pure,
board-agnostic hex-tiling geometry (see that module's docstring) -- they
don't know which hexes are actually tiles on a given board, so trimming
a theoretical corner down to the `VertexId` contract's "only the hexes
actually present on this board" form is this module's job.

Naively trimming against *only* the land hex set has a real edge case:
at the 6 sharp corners of a hexagonal land region, a corner hex has
three *consecutive* absent theoretical neighbors, which means two
distinct physical corners of that hex both degenerate to the same
single-hex id -- an unrecoverable id collision (there'd be two different
board vertices claiming the same `VertexId`). This module resolves it
the same way tabletop Catan effectively does: a one-hex-deep ring of
`Terrain.SEA` tiles is added around the land region (`_sea_border`)
before any trimming happens, so every land hex's 6 theoretical neighbors
are always present as *some* tile (land or sea). That guarantees every
land-hex corner keeps its full, unique 3-hex id -- no collisions, for
any land shape this module can produce, not just the symmetric 19-hex
hexagon. Sea tiles carry no `number_token` and produce no resources (see
`Terrain.SEA`); their only role is anchoring correct vertex ids and
coastal port placement.

Edges are never trimmed for presence at all, per the `board.py` module
docstring -- a road can be built along the outer edge of the play area.
"""

from __future__ import annotations

import math
import random
import zlib
from dataclasses import dataclass

from app.game.board import (
    Board,
    EdgeId,
    HexCoord,
    HexTile,
    Port,
    PortType,
    Terrain,
    VertexId,
    get_adjacent_edges,
    get_adjacent_vertices,
    hex_neighbors,
)

#: Generation mode names accepted by `generate_board`. "random" shuffles
#: terrain/number placement fresh each call (respecting the standard
#: distribution ratios and the no-adjacent-6/8 rule); "fixed" uses a
#: constant seed so the same layout is reproduced deterministically every
#: time, per the plan's "random/fixed board layout" host setting.
GENERATION_MODES: tuple[str, ...] = ("random", "fixed")

#: Player-count buckets used to key the layout registry, matching the
#: plan's "Board Layout for 2p and 7-8p" section language.
PlayerCountBucket = str  # "2p" | "3-4p" | "5-6p" | "7-8p"


def player_count_bucket(player_count: int) -> PlayerCountBucket:
    """Map a raw player count (2-8) to its layout-registry bucket."""
    if player_count == 2:
        return "2p"
    if 3 <= player_count <= 4:
        return "3-4p"
    if 5 <= player_count <= 6:
        return "5-6p"
    if 7 <= player_count <= 8:
        return "7-8p"
    raise ValueError(f"Unsupported player_count for a board: {player_count!r}")


@dataclass(frozen=True)
class LayoutSpec:
    """One named, registered board shape for one player-count bucket.

    This is the "lookup table" the parametric generator is driven by --
    adding a new layout is adding a new `LayoutSpec` + a
    `register_layout` call in `app.game.rules.board_layouts`, not writing
    a new generator.
    """

    layout_name: str
    player_count_bucket: PlayerCountBucket
    #: Total hex tiles (land, including desert(s)) on this layout.
    hex_count: int
    #: Number of `Port` objects to place around the coastline.
    port_count: int
    #: Relative terrain counts, must sum to exactly `hex_count`. Always
    #: includes `Terrain.DESERT` (never `Terrain.SEA` -- sea tiles are
    #: added automatically as the coastal border, not part of a layout's
    #: land composition).
    terrain_counts: dict[Terrain, int]
    description: str = ""

    def __post_init__(self) -> None:
        total = sum(self.terrain_counts.values())
        if total != self.hex_count:
            raise ValueError(
                f"{self.layout_name}: terrain_counts sums to {total}, "
                f"expected hex_count={self.hex_count}"
            )
        if Terrain.SEA in self.terrain_counts:
            raise ValueError(
                f"{self.layout_name}: SEA is added automatically as the "
                "coastal border and must not appear in terrain_counts"
            )
        if self.terrain_counts.get(Terrain.DESERT, 0) < 1:
            raise ValueError(f"{self.layout_name}: needs at least 1 desert hex")


#: (player_count_bucket, layout_name) -> LayoutSpec. Populated by
#: `register_layout`, called from each module under
#: `app.game.rules.board_layouts` at import time (see the bottom of this
#: file, which imports that package so registration always happens).
LAYOUT_REGISTRY: dict[tuple[PlayerCountBucket, str], LayoutSpec] = {}

#: bucket -> the layout_name used when the host picks "random"/"fixed"
#: without naming a specific layout. Populated by `register_layout` when
#: a spec is registered with `default_for_bucket=True`.
DEFAULT_LAYOUT_BY_BUCKET: dict[PlayerCountBucket, str] = {}


def register_layout(spec: LayoutSpec, *, default_for_bucket: bool = False) -> None:
    """Register a `LayoutSpec` into the extensible board_layouts registry.

    Called by `app.game.rules.board_layouts.{standard,expansion_5_6,
    extended_7_8,two_player}` at import time. `default_for_bucket=True`
    marks the layout used when a setting selects a generation mode
    ("random"/"fixed") without naming a specific layout for that player
    count -- see `resolve_layout_choice`.
    """
    key = (spec.player_count_bucket, spec.layout_name)
    LAYOUT_REGISTRY[key] = spec
    if default_for_bucket:
        DEFAULT_LAYOUT_BY_BUCKET[spec.player_count_bucket] = spec.layout_name


def get_layout_spec(bucket: PlayerCountBucket, layout_name: str) -> LayoutSpec:
    try:
        return LAYOUT_REGISTRY[(bucket, layout_name)]
    except KeyError as exc:
        available = sorted(name for (b, name) in LAYOUT_REGISTRY if b == bucket)
        raise ValueError(
            f"No layout {layout_name!r} registered for bucket {bucket!r}; "
            f"available: {available}"
        ) from exc


def resolve_layout_choice(player_count: int, board_layout: str) -> tuple[str, str]:
    """Parse a `GameSettings.board_layout` string into (layout_name, mode).

    `board_layout` is deliberately a single free-form string (see
    `app.game.settings_schema.GameSettings.board_layout`): it's either a
    bare generation mode ("random"/"fixed"), meaning "use this bucket's
    default layout with that mode", or a specific registered layout name
    for this player count's bucket, in which case the mode defaults to
    "random".
    """
    bucket = player_count_bucket(player_count)
    if board_layout in GENERATION_MODES:
        layout_name = DEFAULT_LAYOUT_BY_BUCKET.get(bucket)
        if layout_name is None:
            raise ValueError(f"No default layout registered for bucket {bucket!r}")
        return layout_name, board_layout
    # A specific layout name; validate it exists for this bucket.
    get_layout_spec(bucket, board_layout)
    return board_layout, "random"


# ---------------------------------------------------------------------
# Hex shape generation
# ---------------------------------------------------------------------


def _full_hex_disk(radius: int) -> set[HexCoord]:
    """All axial coordinates within `radius` hex-steps of the origin."""
    hexes: set[HexCoord] = set()
    for q in range(-radius, radius + 1):
        for r in range(-radius, radius + 1):
            s = -q - r
            if max(abs(q), abs(r), abs(s)) <= radius:
                hexes.add(HexCoord(q, r))
    return hexes


def _ring(radius: int) -> list[HexCoord]:
    """Hexes at exactly hex-distance `radius` from the origin.

    Ordered by closeness to the r=0 "equator" first -- when only a
    partial ring is needed (see `generate_hex_shape`), taking a prefix of
    this order produces a band-like extension through the middle of the
    shape, similar in spirit to how the official 5-6p expansion board
    elongates the standard hexagon rather than growing it into a bigger
    regular hexagon.
    """
    if radius == 0:
        return [HexCoord(0, 0)]
    ring = _full_hex_disk(radius) - _full_hex_disk(radius - 1)
    return sorted(ring, key=lambda h: (abs(h.r), h.q, h.r))


def generate_hex_shape(hex_count: int) -> set[HexCoord]:
    """Build a connected, roughly-hexagonal set of `hex_count` hexes.

    Fills complete rings around the origin (1, +6, +12, +18, ... hexes
    per ring) and, once the target can't be reached by a whole ring,
    takes a symmetric prefix of the next ring. Every hex in ring *k* is
    adjacent to at least one hex in ring *k-1* by construction, so the
    result is always connected with no gaps, for any `hex_count >= 1`.
    """
    if hex_count < 1:
        raise ValueError("hex_count must be >= 1")
    hexes: set[HexCoord] = {HexCoord(0, 0)}
    radius = 0
    while len(hexes) < hex_count:
        radius += 1
        remaining = hex_count - len(hexes)
        hexes.update(_ring(radius)[:remaining])
    return hexes


def _sea_border(land_hexes: set[HexCoord]) -> set[HexCoord]:
    """The one-hex-deep ring of non-land coordinates bordering `land_hexes`.

    See the module docstring -- this exists purely so every land hex's 6
    theoretical neighbors are always present as *some* tile, which is
    what keeps every land-hex corner's `VertexId` a full, collision-free
    3-tuple.
    """
    border: set[HexCoord] = set()
    for h in land_hexes:
        for n in hex_neighbors(h):
            if n not in land_hexes:
                border.add(n)
    return border


# ---------------------------------------------------------------------
# Terrain & number tokens
# ---------------------------------------------------------------------

#: The standard Catan number-token pip weighting (18 tokens for 18
#: non-desert hexes on the classic board): one 2, two each of
#: 3/4/5/6/8/9/10/11, one 12. Larger boards tile this cycle to keep the
#: same relative "which numbers are common" shape at any size.
_STANDARD_NUMBER_CYCLE: tuple[int, ...] = (
    2, 3, 3, 4, 4, 5, 5, 6, 6, 8, 8, 9, 9, 10, 10, 11, 11, 12,
)


def _generate_number_tokens(count: int) -> list[int]:
    tokens: list[int] = []
    while len(tokens) < count:
        tokens.extend(_STANDARD_NUMBER_CYCLE)
    return tokens[:count]


def _expand_terrain_counts(terrain_counts: dict[Terrain, int]) -> list[Terrain]:
    terrains: list[Terrain] = []
    for terrain, count in terrain_counts.items():
        terrains.extend([terrain] * count)
    return terrains


def _assign_terrain(
    land_hexes: list[HexCoord], terrain_counts: dict[Terrain, int], rng: random.Random
) -> dict[HexCoord, Terrain]:
    terrains = _expand_terrain_counts(terrain_counts)
    assert len(terrains) == len(land_hexes)
    rng.shuffle(terrains)
    return dict(zip(land_hexes, terrains))


def _place_number_tokens(
    non_desert_hexes: list[HexCoord],
    tokens: list[int],
    rng: random.Random,
    max_attempts: int = 5000,
) -> dict[HexCoord, int]:
    """Assign `tokens` to `non_desert_hexes` so no two adjacent hexes both
    carry a 6 or 8 (the "red numbers", statistically hottest rolls --
    keeping them apart is a standard Catan house rule for fairer random
    boards). Retries with a fresh shuffle on failure; converges quickly
    at these board sizes since there's normally ample slack.
    """
    hex_set = set(non_desert_hexes)
    neighbor_map = {h: [n for n in hex_neighbors(h) if n in hex_set] for h in non_desert_hexes}

    for _ in range(max_attempts):
        order = non_desert_hexes[:]
        rng.shuffle(order)
        pool = tokens[:]
        rng.shuffle(pool)
        assignment: dict[HexCoord, int] = {}
        ok = True
        for h in order:
            candidates = list(range(len(pool)))
            rng.shuffle(candidates)
            chosen_index: int | None = None
            for idx in candidates:
                tok = pool[idx]
                if tok in (6, 8) and any(
                    assignment.get(n) in (6, 8) for n in neighbor_map[h]
                ):
                    continue
                chosen_index = idx
                break
            if chosen_index is None:
                ok = False
                break
            assignment[h] = pool.pop(chosen_index)
        if ok:
            return assignment
    raise RuntimeError(
        "Failed to place number tokens without adjacent 6/8 after "
        f"{max_attempts} attempts"
    )


# ---------------------------------------------------------------------
# Vertex / edge universe
# ---------------------------------------------------------------------


def _trim_vertex(candidate: VertexId, presence: set[HexCoord]) -> VertexId:
    return tuple(sorted(c for c in candidate if c in presence))


def _build_vertex_universe(
    land_hexes: set[HexCoord], presence: set[HexCoord]
) -> set[VertexId]:
    vertices: set[VertexId] = set()
    for h in land_hexes:
        for candidate in get_adjacent_vertices(h):
            trimmed = _trim_vertex(candidate, presence)
            if len(trimmed) >= 2:
                vertices.add(trimmed)
    return vertices


# ---------------------------------------------------------------------
# Ports
# ---------------------------------------------------------------------

_RESOURCE_PORT_TYPES: tuple[PortType, ...] = (
    PortType.BRICK,
    PortType.LUMBER,
    PortType.ORE,
    PortType.GRAIN,
    PortType.WOOL,
)

#: The classic 9-port composition (4 generic 3:1, 5 resource-specific
#: 2:1) as a repeating pattern -- tiled for larger boards so the same
#: "somewhat more generic than resource-specific" ratio holds at any
#: `port_count`.
_PORT_TYPE_PATTERN: tuple[PortType, ...] = (
    PortType.GENERIC,
    PortType.BRICK,
    PortType.GENERIC,
    PortType.LUMBER,
    PortType.ORE,
    PortType.GENERIC,
    PortType.GRAIN,
    PortType.WOOL,
    PortType.GENERIC,
)


def _port_type_sequence(port_count: int) -> list[PortType]:
    sequence: list[PortType] = []
    i = 0
    while len(sequence) < port_count:
        sequence.append(_PORT_TYPE_PATTERN[i % len(_PORT_TYPE_PATTERN)])
        i += 1
    return sequence[:port_count]


def _axial_to_pixel(h: HexCoord) -> tuple[float, float]:
    # Standard pointy-top axial -> pixel conversion (redblobgames.com).
    # Only used to order coastal edges around the perimeter for even
    # port spacing -- never compared to anything on the wire.
    x = h.q + h.r / 2.0
    y = h.r * 0.8660254037844387  # sqrt(3)/2
    return x, y


def _place_ports(
    land_hexes: set[HexCoord],
    presence: set[HexCoord],
    vertex_universe: set[VertexId],
    port_count: int,
    rng: random.Random,
) -> list[Port]:
    coastal_edges = [
        e
        for h in land_hexes
        for e in get_adjacent_edges(h)
        if (e[0] not in land_hexes) or (e[1] not in land_hexes)
    ]
    coastal_edges = sorted(set(coastal_edges))

    def angle(e: EdgeId) -> float:
        ax, ay = _axial_to_pixel(e[0])
        bx, by = _axial_to_pixel(e[1])
        return math.atan2((ay + by) / 2.0, (ax + bx) / 2.0)

    coastal_edges.sort(key=angle)

    if not coastal_edges:
        return []

    n = len(coastal_edges)
    port_count = min(port_count, n)
    step = n / port_count
    chosen = [coastal_edges[int(i * step)] for i in range(port_count)]

    types = _port_type_sequence(port_count)
    rng.shuffle(types)

    ports: list[Port] = []
    for edge, port_type in zip(chosen, types):
        a, b = edge
        # The two theoretical corners of this edge, trimmed to what's
        # actually present.
        endpoints = []
        for third in _third_hex_candidates(a, b):
            candidate = _trim_vertex((a, b, third), presence)
            if candidate in vertex_universe:
                endpoints.append(candidate)
        if len(endpoints) != 2:
            continue
        ports.append(Port(port_type=port_type, vertices=(endpoints[0], endpoints[1])))
    return ports


def _third_hex_candidates(a: HexCoord, b: HexCoord) -> list[HexCoord]:
    na = set(hex_neighbors(a))
    nb = set(hex_neighbors(b))
    return sorted(na & nb)


# ---------------------------------------------------------------------
# Top-level generation
# ---------------------------------------------------------------------


def _fixed_seed_for(spec: LayoutSpec) -> int:
    # A stable, process-independent seed derived from the layout name
    # (Python's built-in hash() of strings is salted per-process, so it
    # isn't safe for a "fixed" mode that must reproduce the same board on
    # every call/every process).
    return zlib.crc32(spec.layout_name.encode("utf-8"))


def _build_board_from_spec(spec: LayoutSpec, rng: random.Random) -> Board:
    land_hexes_set = generate_hex_shape(spec.hex_count)
    land_hexes = sorted(land_hexes_set)

    terrain_by_hex = _assign_terrain(land_hexes, dict(spec.terrain_counts), rng)
    desert_hexes = sorted(h for h, t in terrain_by_hex.items() if t == Terrain.DESERT)
    non_desert_hexes = sorted(h for h, t in terrain_by_hex.items() if t != Terrain.DESERT)

    tokens = _generate_number_tokens(len(non_desert_hexes))
    numbers_by_hex = _place_number_tokens(non_desert_hexes, tokens, rng)

    sea_hexes = _sea_border(land_hexes_set)
    presence = land_hexes_set | sea_hexes

    hexes: dict[HexCoord, HexTile] = {}
    for h in land_hexes:
        terrain = terrain_by_hex[h]
        hexes[h] = HexTile(
            coord=h, terrain=terrain, number_token=numbers_by_hex.get(h)
        )
    for h in sea_hexes:
        hexes[h] = HexTile(coord=h, terrain=Terrain.SEA, number_token=None)

    vertex_universe = _build_vertex_universe(land_hexes_set, presence)

    ports = _place_ports(land_hexes_set, presence, vertex_universe, spec.port_count, rng)

    robber_hex = desert_hexes[0]

    return Board(hexes=hexes, ports=ports, buildings={}, roads={}, robber_hex=robber_hex)


def generate_board(
    player_count: int,
    layout_name: str | None = None,
    mode: str = "random",
    seed: int | None = None,
) -> Board:
    """Generate a `Board` for `player_count` players.

    `layout_name` selects a registered layout for this player count's
    bucket (defaults to that bucket's default layout -- e.g. "standard"
    for 3-4p). `mode` is "random" (fresh shuffled terrain/numbers each
    call, respecting distribution ratios and the no-adjacent-6/8 rule) or
    "fixed" (a deterministic, reproducible preset). `seed` optionally
    pins "random" mode's RNG too (useful for reproducible tests); it's
    ignored in "fixed" mode, which always uses its own stable seed.
    """
    if mode not in GENERATION_MODES:
        raise ValueError(
            f"Unknown board generation mode {mode!r}; expected one of {GENERATION_MODES}"
        )
    bucket = player_count_bucket(player_count)
    if layout_name is None:
        layout_name = DEFAULT_LAYOUT_BY_BUCKET.get(bucket)
        if layout_name is None:
            raise ValueError(f"No default layout registered for bucket {bucket!r}")
    spec = get_layout_spec(bucket, layout_name)

    rng = random.Random(_fixed_seed_for(spec) if mode == "fixed" else seed)
    return _build_board_from_spec(spec, rng)


def generate_board_for_settings(settings: "GameSettings") -> Board:  # noqa: F821
    """Convenience wrapper: generate a `Board` straight from a locked
    `app.game.settings_schema.GameSettings` (`player_count` +
    `board_layout`). Kept separate from `generate_board` (which takes
    plain args) so this module doesn't need `settings_schema` imported
    at module load time for its core API to be usable/testable.
    """
    from app.game.settings_schema import GameSettings  # local import, see above

    assert isinstance(settings, GameSettings)
    layout_name, mode = resolve_layout_choice(settings.player_count, settings.board_layout)
    return generate_board(settings.player_count, layout_name=layout_name, mode=mode)


# Import the layout registry modules for their registration side effects.
# Must be last in this file -- see the module docstring's "Vertex/edge
# ids and the sea border" section and `register_layout`'s docstring for
# why the import-order bootstrapping this relies on is safe.
from app.game.rules import board_layouts as _board_layouts  # noqa: E402,F401
