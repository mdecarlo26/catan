/**
 * Hardcoded WireBoardView fixture for a standard 19-hex (3-4 player)
 * board, used only for local visual verification of the PixiJS renderer
 * before the real backend/live data pipeline exists (see
 * board/BoardPreview.tsx). Terrain/number placement follows a standard
 * Catan-style arrangement (no two 6/8 tokens adjacent); exact tile
 * assignment isn't load-bearing since this is a rendering fixture, not
 * game data.
 *
 * Land hexes alone are NOT enough to keep every vertex id well-defined:
 * the 6 outer "tip" corners of a plain hexagon-of-hexes board have only
 * ONE present hex touching them (both theoretical neighbors fall outside
 * the board), so two *different* physical corners of the same tip hex
 * would collapse to the same 1-hex vertex id -- an id collision. This is
 * exactly what board.py's `Terrain.SEA` is for ("ocean/border hexes some
 * board layouts use to anchor port placement"): a ring of sea hexes
 * around the land guarantees every corner has at least 2 present hexes,
 * matching the module docstring's stated 2-3-length invariant. This
 * fixture includes that sea ring so the geometry is well-defined and so
 * ports render in their natural spot (on the coast, between land and
 * sea), the same way a real generated board would.
 */
import type {
  HexCoord,
  HexTile,
  Port,
  Terrain,
  WireBoardView,
  WireRoad,
  WireVertexBuilding,
} from "../types/protocol";
import { edgeIdForSide, hexKey, vertexIdForCorner } from "./hexMath";

/** All axial hex coords within `n` rings of the origin. */
function hexRingFill(n: number): HexCoord[] {
  const coords: HexCoord[] = [];
  for (let q = -n; q <= n; q++) {
    const rMin = Math.max(-n, -q - n);
    const rMax = Math.min(n, -q + n);
    for (let r = rMin; r <= rMax; r++) {
      coords.push([q, r]);
    }
  }
  return coords;
}

const LAND_HEX_COORDS = hexRingFill(2); // 19 entries -- the standard playable board
const ALL_HEX_COORDS = hexRingFill(3); // + a surrounding ring of 18 sea hexes = 37

const LAND_PRESENT = new Set(LAND_HEX_COORDS.map(hexKey));
const ALL_PRESENT = new Set(ALL_HEX_COORDS.map(hexKey));

// Standard resource distribution: 4 forest, 4 pasture, 4 fields, 3 hills,
// 3 mountains, 1 desert (18 producing + 1 desert = 19).
const TERRAIN_SEQUENCE: Terrain[] = [
  "forest", "forest", "forest", "forest",
  "pasture", "pasture", "pasture", "pasture",
  "fields", "fields", "fields", "fields",
  "hills", "hills", "hills",
  "mountains", "mountains", "mountains",
  "desert",
];

// Standard 18 number tokens (2-12, no 7s), arranged with desert
// interleaved so no two 6/8 end up adjacent in this particular
// coordinate walk order below.
const NUMBER_SEQUENCE = [5, 2, 6, 3, 8, 10, 9, 12, 11, 4, 8, 10, 9, 4, 5, 6, 3, 11];

function buildMockHexes(): HexTile[] {
  let numberIdx = 0;
  const land: HexTile[] = LAND_HEX_COORDS.map((coord, i) => {
    const terrain = TERRAIN_SEQUENCE[i];
    const number_token = terrain === "desert" ? null : NUMBER_SEQUENCE[numberIdx++];
    return { coord, terrain, number_token };
  });
  const sea: HexTile[] = ALL_HEX_COORDS.filter((c) => !LAND_PRESENT.has(hexKey(c))).map((coord) => ({
    coord,
    terrain: "sea" as Terrain,
    number_token: null,
  }));
  return [...land, ...sea];
}

const MOCK_HEXES = buildMockHexes();
const DESERT_HEX = MOCK_HEXES.find((h) => h.terrain === "desert")!.coord;

/**
 * Coastal edges: edges of a land hex whose far side is a (present) sea
 * hex -- i.e. the true coastline, where ports belong.
 */
function findCoastalEdges(): { hex: HexCoord; edgeIndex: number }[] {
  const result: { hex: HexCoord; edgeIndex: number }[] = [];
  for (const hex of LAND_HEX_COORDS) {
    for (let i = 0; i < 6; i++) {
      const edgeId = edgeIdForSide(hex, i);
      const other = edgeId.find((h) => !(h[0] === hex[0] && h[1] === hex[1]))!;
      if (!LAND_PRESENT.has(hexKey(other))) {
        result.push({ hex, edgeIndex: i });
      }
    }
  }
  return result;
}

const COASTAL_EDGES = findCoastalEdges();

/** Pick a handful of evenly-spaced coastal edges as port anchors. */
function buildMockPorts(): Port[] {
  const portTypes: Port["port_type"][] = [
    "generic", "brick", "generic", "wool", "ore", "generic", "grain", "lumber", "generic",
  ];
  const step = Math.floor(COASTAL_EDGES.length / portTypes.length);
  const ports: Port[] = [];
  for (let i = 0; i < portTypes.length; i++) {
    const { hex, edgeIndex } = COASTAL_EDGES[(i * step) % COASTAL_EDGES.length];
    const v0 = vertexIdForCorner(hex, edgeIndex, ALL_PRESENT);
    const v1 = vertexIdForCorner(hex, (edgeIndex + 1) % 6, ALL_PRESENT);
    ports.push({ port_type: portTypes[i], vertices: [v0, v1] });
  }
  return ports;
}

const MOCK_PORTS = buildMockPorts();

// Mock player ids -- just labeled strings, not real uuid4s (fine for a
// rendering fixture; ids only need to be stable strings here).
export const MOCK_PLAYER_IDS = [
  "player-red",
  "player-blue",
  "player-green",
  "player-orange",
] as const;

function buildMockBuildingsAndRoads(): { buildings: WireVertexBuilding[]; roads: WireRoad[] } {
  const [p1, p2, p3, p4] = MOCK_PLAYER_IDS;
  const present = ALL_PRESENT;

  // Pick a handful of land hexes to anchor sample pieces around, spread
  // across the board so gaps/overlaps in tiling are easy to spot visually.
  const centerHex: HexCoord = [0, 0];
  const nwHex: HexCoord = [-2, 0];
  const seHex: HexCoord = [2, 0];
  const nHex: HexCoord = [0, -2];
  const sHex: HexCoord = [0, 2];

  const buildings: WireVertexBuilding[] = [
    { vertex_id: vertexIdForCorner(centerHex, 0, present), player_id: p1, building_type: "settlement" },
    { vertex_id: vertexIdForCorner(centerHex, 3, present), player_id: p2, building_type: "city" },
    { vertex_id: vertexIdForCorner(nwHex, 4, present), player_id: p3, building_type: "settlement" },
    { vertex_id: vertexIdForCorner(seHex, 1, present), player_id: p4, building_type: "settlement" },
    { vertex_id: vertexIdForCorner(nHex, 5, present), player_id: p1, building_type: "city" },
    { vertex_id: vertexIdForCorner(sHex, 2, present), player_id: p2, building_type: "settlement" },
  ];

  const roads: WireRoad[] = [
    { edge_id: edgeIdForSide(centerHex, 0), player_id: p1 },
    { edge_id: edgeIdForSide(centerHex, 5), player_id: p1 },
    { edge_id: edgeIdForSide(centerHex, 3), player_id: p2 },
    { edge_id: edgeIdForSide(nwHex, 4), player_id: p3 },
    { edge_id: edgeIdForSide(nwHex, 3), player_id: p3 },
    { edge_id: edgeIdForSide(seHex, 1), player_id: p4 },
    { edge_id: edgeIdForSide(nHex, 5), player_id: p1 },
    { edge_id: edgeIdForSide(sHex, 2), player_id: p2 },
  ];

  return { buildings, roads };
}

const { buildings: MOCK_BUILDINGS, roads: MOCK_ROADS } = buildMockBuildingsAndRoads();

export const MOCK_BOARD: WireBoardView = {
  hexes: MOCK_HEXES,
  ports: MOCK_PORTS,
  buildings: MOCK_BUILDINGS,
  roads: MOCK_ROADS,
  robber_hex: DESERT_HEX,
};

/**
 * A handful of vertex/edge ids from MOCK_BOARD's own geometry, exported
 * for the preview's "buildable highlight" demo -- these are *not*
 * currently occupied, so they're valid stand-ins for a legal-build-set
 * the backend would normally compute.
 */
export function mockLegalVertexIds() {
  const present = ALL_PRESENT;
  return [
    vertexIdForCorner([0, 0], 1, present),
    vertexIdForCorner([0, 0], 2, present),
    vertexIdForCorner([1, -1], 0, present),
    vertexIdForCorner([-1, 1], 3, present),
    vertexIdForCorner([1, 1], 2, present),
  ];
}

export function mockLegalEdgeIds() {
  return [
    edgeIdForSide([0, 0], 1),
    edgeIdForSide([0, 0], 2),
    edgeIdForSide([1, -1], 5),
    edgeIdForSide([-1, 1], 0),
  ];
}
