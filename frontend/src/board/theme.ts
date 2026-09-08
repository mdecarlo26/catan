/**
 * Rendering constants shared by HexTile / VertexNode / EdgeSegment /
 * BoardCanvas: terrain colors and the fixed player color palette.
 */
import type { Terrain, PlayerId } from "../types/protocol";

export const TERRAIN_COLORS: Record<Terrain, number> = {
  forest: 0x2f6b3a,
  hills: 0xb5651d,
  mountains: 0x8a8d90,
  fields: 0xe8c547,
  pasture: 0x8fc93a,
  desert: 0xd9c08c,
  sea: 0x2a6f97,
};

export const TERRAIN_LABELS: Record<Terrain, string> = {
  forest: "Forest",
  hills: "Hills",
  mountains: "Mountains",
  fields: "Fields",
  pasture: "Pasture",
  desert: "Desert",
  sea: "Sea",
};

/**
 * Fixed palette of 8 visually-distinct player colors, one slot per max
 * supported player count (ARCHITECTURE.md: 2-8 players).
 */
export const PLAYER_COLOR_PALETTE: readonly number[] = [
  0xe6194b, // red
  0x4363d8, // blue
  0x3cb44b, // green
  0xf58231, // orange
  0x911eb4, // purple
  0x42d4f4, // cyan
  0xf032e6, // magenta
  0x9a6324, // brown
];

export function playerColor(index: number): number {
  const palette = PLAYER_COLOR_PALETTE;
  const i = ((index % palette.length) + palette.length) % palette.length;
  return palette[i];
}

/**
 * Deterministically assign a stable palette index to each distinct
 * player id encountered, in first-seen order. Callers that already know
 * seat numbers (once the real lobby/game state exists) should prefer
 * assigning by seat instead -- this is a reasonable fallback for a board
 * view that only has a flat list of buildings/roads to go on.
 */
export function assignPlayerColorIndices(playerIds: readonly PlayerId[]): Map<PlayerId, number> {
  const map = new Map<PlayerId, number>();
  for (const id of playerIds) {
    if (!map.has(id)) {
      map.set(id, map.size % PLAYER_COLOR_PALETTE.length);
    }
  }
  return map;
}
