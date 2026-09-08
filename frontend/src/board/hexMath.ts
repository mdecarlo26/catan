/**
 * Pure hex-grid geometry for the Catan board.
 *
 * Mirrors the coordinate-system convention documented in
 * backend/app/game/board.py's module docstring:
 *   - Hexes use axial coordinates (q, r).
 *   - A vertex id is the ascending-sorted tuple of the HexCoords of the
 *     2-3 hexes (that actually have tiles on this board) meeting at that
 *     corner.
 *   - An edge id is the ascending-sorted 2-tuple of the HexCoords of the
 *     two hex positions the edge separates (both slots exist in
 *     coordinate space regardless of whether either has a tile).
 *
 * This module derives ids purely from hex-coordinate adjacency (no
 * floating point comparisons), exactly as the backend is required to.
 * The backend's board.py geometry functions (get_adjacent_vertices,
 * get_adjacent_edges, ...) are still NotImplementedError stubs as of
 * this writing (Wave 1 backend work) -- this file does not call into or
 * depend on that implementation. It independently derives ids from the
 * same documented convention, using the standard pointy-top axial layout
 * described at https://www.redblobgames.com/grids/hexagons/ (referenced
 * by board.py's docstring). As long as both sides use ascending-sorted
 * HexCoord tuples built from mutually-adjacent hexes, the two
 * implementations necessarily agree without ever comparing geometry.
 */
import type { HexCoord, VertexId, EdgeId } from "../types/protocol";

/** Default pixel distance from a hex's center to any of its corners. */
export const HEX_SIZE = 48;

const SQRT3 = Math.sqrt(3);

// ---------------------------------------------------------------------
// HexCoord helpers
// ---------------------------------------------------------------------

export function hexKey(h: HexCoord): string {
  return `${h[0]},${h[1]}`;
}

export function hexEquals(a: HexCoord, b: HexCoord): boolean {
  return a[0] === b[0] && a[1] === b[1];
}

function compareHex(a: HexCoord, b: HexCoord): number {
  return a[0] - b[0] || a[1] - b[1];
}

function sortHexCoords(hs: HexCoord[]): HexCoord[] {
  return [...hs].sort(compareHex);
}

function sortPair(a: HexCoord, b: HexCoord): EdgeId {
  return compareHex(a, b) <= 0 ? [a, b] : [b, a];
}

export function vertexIdKey(v: VertexId): string {
  return v.map(hexKey).join("|");
}

export function edgeIdKey(e: EdgeId): string {
  return e.map(hexKey).join("|");
}

// ---------------------------------------------------------------------
// Axial <-> pixel conversion (pointy-top hexes)
// ---------------------------------------------------------------------

export interface Point {
  x: number;
  y: number;
}

/** Center pixel position of hex `coord`, in board-local pixel space. */
export function axialToPixel(coord: HexCoord, size: number = HEX_SIZE): Point {
  const [q, r] = coord;
  return {
    x: size * SQRT3 * (q + r / 2),
    y: size * 1.5 * r,
  };
}

/** Fractional (unrounded) axial coordinates for a pixel position -- used by pixelToAxial. */
function pixelToAxialFractional(x: number, y: number, size: number): { q: number; r: number } {
  const q = ((SQRT3 / 3) * x - (1 / 3) * y) / size;
  const r = ((2 / 3) * y) / size;
  return { q, r };
}

/** Round fractional axial coordinates to the nearest integer hex (standard cube rounding). */
function axialRound(qf: number, rf: number): HexCoord {
  const xf = qf;
  const zf = rf;
  const yf = -xf - zf;
  let rx = Math.round(xf);
  let ry = Math.round(yf);
  let rz = Math.round(zf);
  const xDiff = Math.abs(rx - xf);
  const yDiff = Math.abs(ry - yf);
  const zDiff = Math.abs(rz - zf);
  if (xDiff > yDiff && xDiff > zDiff) {
    rx = -ry - rz;
  } else if (yDiff > zDiff) {
    ry = -rx - rz;
  } else {
    rz = -rx - ry;
  }
  return [rx, rz];
}

/** Inverse of axialToPixel: which hex (rounded to the nearest) contains pixel (x, y). */
export function pixelToAxial(x: number, y: number, size: number = HEX_SIZE): HexCoord {
  const { q, r } = pixelToAxialFractional(x, y, size);
  return axialRound(q, r);
}

// ---------------------------------------------------------------------
// Hex corners
// ---------------------------------------------------------------------

/**
 * Pixel offsets (relative to a hex's own center) of its 6 corners.
 * Pointy-top orientation: corner 0 is due north, then proceeding
 * clockwise in 60-degree steps (corner 1 = NE, 2 = SE, 3 = S, 4 = SW,
 * 5 = NW).
 */
export function hexCornerOffsets(size: number = HEX_SIZE): Point[] {
  const corners: Point[] = [];
  for (let i = 0; i < 6; i++) {
    const angleDeg = -90 + 60 * i;
    const angleRad = (Math.PI / 180) * angleDeg;
    corners.push({ x: size * Math.cos(angleRad), y: size * Math.sin(angleRad) });
  }
  return corners;
}

/** Absolute pixel positions of hex `coord`'s 6 corners. */
export function hexCorners(coord: HexCoord, size: number = HEX_SIZE): Point[] {
  const center = axialToPixel(coord, size);
  return hexCornerOffsets(size).map((o) => ({ x: center.x + o.x, y: center.y + o.y }));
}

// ---------------------------------------------------------------------
// Adjacency tables
// ---------------------------------------------------------------------

/** The 6 axial neighbor direction vectors, indexed 0-5 by ascending pixel angle. */
const NEIGHBOR_DIRS: readonly HexCoord[] = [
  [1, 0],
  [1, -1],
  [0, -1],
  [-1, 0],
  [-1, 1],
  [0, 1],
];

/**
 * For corner `i` of a hex (see hexCornerOffsets for the indexing), the
 * two neighbor-direction vectors whose hexes -- together with the hex
 * itself -- are the (up to) three mutually-adjacent hexes that meet at
 * that physical corner point.
 *
 * Derivation: corner i's pixel offset is exactly the centroid of the hex
 * center and its two neighbor centers at NEIGHBOR_DIRS[(1-i)%6] and
 * NEIGHBOR_DIRS[(2-i)%6] (verified analytically from the corner-offset
 * and neighbor-delta formulas above), so those three hex coordinates are
 * the ones that physically share that corner.
 */
const CORNER_NEIGHBOR_DIRS: readonly [HexCoord, HexCoord][] = [0, 1, 2, 3, 4, 5].map((i) => {
  const a = NEIGHBOR_DIRS[(((1 - i) % 6) + 6) % 6];
  const b = NEIGHBOR_DIRS[(((2 - i) % 6) + 6) % 6];
  return [a, b];
}) as [HexCoord, HexCoord][];

/**
 * Direction of the single neighbor hex bordering the edge that runs from
 * corner `i` to corner `(i + 1) % 6`.
 */
const EDGE_NEIGHBOR_DIR: readonly HexCoord[] = [0, 1, 2, 3, 4, 5].map((i) => {
  const [a1, a2] = CORNER_NEIGHBOR_DIRS[i];
  const [b1, b2] = CORNER_NEIGHBOR_DIRS[(i + 1) % 6];
  // The direction shared between consecutive corners' neighbor-pairs is
  // the one hex bordering the edge between them.
  const shared = [a1, a2].find((d) => hexEquals(d, b1) || hexEquals(d, b2));
  if (!shared) {
    throw new Error(`internal error: no shared neighbor direction for edge ${i}`);
  }
  return shared;
});

// ---------------------------------------------------------------------
// Vertex / edge id derivation
// ---------------------------------------------------------------------

/**
 * Vertex id for corner `cornerIndex` (0-5) of `hex`, restricted to the
 * hexes actually present on the board (`presentHexes`, a set of
 * `hexKey()` strings). Mirrors board.py's VertexId convention exactly:
 * the ascending-sorted tuple of the 2-3 present hexes meeting at that
 * corner.
 */
export function vertexIdForCorner(
  hex: HexCoord,
  cornerIndex: number,
  presentHexes: ReadonlySet<string>
): VertexId {
  const [d1, d2] = CORNER_NEIGHBOR_DIRS[cornerIndex];
  const candidates: HexCoord[] = [
    hex,
    [hex[0] + d1[0], hex[1] + d1[1]],
    [hex[0] + d2[0], hex[1] + d2[1]],
  ];
  const present = candidates.filter((c) => presentHexes.has(hexKey(c)));
  return sortHexCoords(present);
}

/**
 * Edge id for the edge between corner `edgeIndex` and corner
 * `(edgeIndex + 1) % 6` of `hex`. Unlike vertices, both hex slots are
 * included regardless of whether the neighbor is actually present on the
 * board (mirrors board.py: "the two hex coordinate slots on either side
 * of an edge exist in coordinate space regardless of whether a tile has
 * been placed there").
 */
export function edgeIdForSide(hex: HexCoord, edgeIndex: number): EdgeId {
  const d = EDGE_NEIGHBOR_DIR[edgeIndex];
  const other: HexCoord = [hex[0] + d[0], hex[1] + d[1]];
  return sortPair(hex, other);
}

// ---------------------------------------------------------------------
// Full-board geometry
// ---------------------------------------------------------------------

export interface VertexGeometry {
  id: VertexId;
  key: string;
  x: number;
  y: number;
}

export interface EdgeGeometry {
  id: EdgeId;
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  midX: number;
  midY: number;
  /** Angle (radians) of the edge, useful for orienting a road sprite. */
  angle: number;
}

export interface BoardGeometry {
  hexPixels: Map<string, Point>;
  vertices: Map<string, VertexGeometry>;
  edges: Map<string, EdgeGeometry>;
}

/**
 * Compute pixel-space geometry (hex centers, deduplicated vertices and
 * edges with their ids) for a full board given only its list of present
 * hex coordinates. This is the single source of truth both BoardCanvas
 * and boardInteraction build on.
 */
export function computeBoardGeometry(hexCoords: readonly HexCoord[], size: number = HEX_SIZE): BoardGeometry {
  const present = new Set(hexCoords.map(hexKey));
  const hexPixels = new Map<string, Point>();
  const vertices = new Map<string, VertexGeometry>();
  const edges = new Map<string, EdgeGeometry>();

  for (const hex of hexCoords) {
    hexPixels.set(hexKey(hex), axialToPixel(hex, size));
    const corners = hexCorners(hex, size);

    for (let i = 0; i < 6; i++) {
      const id = vertexIdForCorner(hex, i, present);
      const key = vertexIdKey(id);
      if (!vertices.has(key)) {
        vertices.set(key, { id, key, x: corners[i].x, y: corners[i].y });
      }
    }

    for (let i = 0; i < 6; i++) {
      const id = edgeIdForSide(hex, i);
      const key = edgeIdKey(id);
      if (!edges.has(key)) {
        const c1 = corners[i];
        const c2 = corners[(i + 1) % 6];
        edges.set(key, {
          id,
          key,
          x1: c1.x,
          y1: c1.y,
          x2: c2.x,
          y2: c2.y,
          midX: (c1.x + c2.x) / 2,
          midY: (c1.y + c2.y) / 2,
          angle: Math.atan2(c2.y - c1.y, c2.x - c1.x),
        });
      }
    }
  }

  return { hexPixels, vertices, edges };
}

// ---------------------------------------------------------------------
// Misc geometry helpers
// ---------------------------------------------------------------------

/** Shortest distance from point (px, py) to the line segment (x1,y1)-(x2,y2). */
export function distancePointToSegment(
  px: number,
  py: number,
  x1: number,
  y1: number,
  x2: number,
  y2: number
): number {
  const dx = x2 - x1;
  const dy = y2 - y1;
  const lenSq = dx * dx + dy * dy;
  let t = lenSq === 0 ? 0 : ((px - x1) * dx + (py - y1) * dy) / lenSq;
  t = Math.max(0, Math.min(1, t));
  const cx = x1 + t * dx;
  const cy = y1 + t * dy;
  return Math.hypot(px - cx, py - cy);
}
