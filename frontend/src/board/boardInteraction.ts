/**
 * Hit-testing / picking logic for the board: given a pointer position (in
 * the same board-local pixel space as hexMath.computeBoardGeometry),
 * determine the nearest buildable vertex or edge.
 *
 * "Buildable only" highlighting is driven purely by a set of legal
 * vertex/edge ids passed in by the caller -- this module has no opinion
 * on Catan build-legality rules; that lives in the backend rules_engine
 * (see ARCHITECTURE.md). It only answers "given these ids are legal,
 * which one is the pointer closest to?".
 */
import type { EdgeId, VertexId } from "../types/protocol";
import type { EdgeGeometry, VertexGeometry } from "./hexMath";
import { distancePointToSegment, edgeIdKey, vertexIdKey } from "./hexMath";

export interface PickedVertex {
  id: VertexId;
  key: string;
  distance: number;
}

export interface PickedEdge {
  id: EdgeId;
  key: string;
  distance: number;
}

/** Build a Set of vertexIdKey() strings from a list of legal VertexIds (or null/undefined for "no restriction"). */
export function buildVertexKeySet(ids: readonly VertexId[] | null | undefined): Set<string> {
  return new Set((ids ?? []).map(vertexIdKey));
}

/** Build a Set of edgeIdKey() strings from a list of legal EdgeIds (or null/undefined for "no restriction"). */
export function buildEdgeKeySet(ids: readonly EdgeId[] | null | undefined): Set<string> {
  return new Set((ids ?? []).map(edgeIdKey));
}

export interface PickOptions {
  /** Reject candidates farther than this from the pointer. Default: Infinity (no cap). */
  maxDistance?: number;
  /** If provided, only vertices/edges whose key is in this set are considered. */
  onlyKeys?: ReadonlySet<string> | null;
}

/** Find the buildable vertex nearest to pointer position (px, py), or null if none qualify. */
export function pickNearestVertex(
  px: number,
  py: number,
  vertices: Iterable<VertexGeometry>,
  opts: PickOptions = {}
): PickedVertex | null {
  const maxDistance = opts.maxDistance ?? Infinity;
  let best: PickedVertex | null = null;
  for (const v of vertices) {
    if (opts.onlyKeys && !opts.onlyKeys.has(v.key)) continue;
    const d = Math.hypot(px - v.x, py - v.y);
    if (d <= maxDistance && (!best || d < best.distance)) {
      best = { id: v.id, key: v.key, distance: d };
    }
  }
  return best;
}

/** Find the buildable edge nearest to pointer position (px, py), or null if none qualify. */
export function pickNearestEdge(
  px: number,
  py: number,
  edges: Iterable<EdgeGeometry>,
  opts: PickOptions = {}
): PickedEdge | null {
  const maxDistance = opts.maxDistance ?? Infinity;
  let best: PickedEdge | null = null;
  for (const e of edges) {
    if (opts.onlyKeys && !opts.onlyKeys.has(e.key)) continue;
    const d = distancePointToSegment(px, py, e.x1, e.y1, e.x2, e.y2);
    if (d <= maxDistance && (!best || d < best.distance)) {
      best = { id: e.id, key: e.key, distance: d };
    }
  }
  return best;
}

/**
 * Pick whichever of the nearest buildable vertex or nearest buildable
 * edge is closer to the pointer -- useful when a single click should
 * resolve to "the nearest legal build target of either kind" (e.g. a
 * generic "build" cursor before the player has chosen settlement vs
 * road). Distances are compared directly since both are in the same
 * pixel space.
 */
export function pickNearestBuildable(
  px: number,
  py: number,
  vertices: Iterable<VertexGeometry>,
  edges: Iterable<EdgeGeometry>,
  opts: { maxDistance?: number; onlyVertexKeys?: ReadonlySet<string> | null; onlyEdgeKeys?: ReadonlySet<string> | null } = {}
): { kind: "vertex"; pick: PickedVertex } | { kind: "edge"; pick: PickedEdge } | null {
  const v = pickNearestVertex(px, py, vertices, { maxDistance: opts.maxDistance, onlyKeys: opts.onlyVertexKeys });
  const e = pickNearestEdge(px, py, edges, { maxDistance: opts.maxDistance, onlyKeys: opts.onlyEdgeKeys });
  if (!v && !e) return null;
  if (v && (!e || v.distance <= e.distance)) return { kind: "vertex", pick: v };
  return { kind: "edge", pick: e! };
}
