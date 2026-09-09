/**
 * Client-side board connectivity graph, derived from a live WireBoardView
 * (real backend data, not mockBoard). This is NOT game-rules logic -- it
 * only answers "which vertex/edge ids exist, and which vertices/edges are
 * adjacent to which" so `legality.ts` can mirror the backend
 * rules_engine's connectivity checks (`_player_has_connection_to_vertex` /
 * `_player_has_connection_to_edge` / `vertex_neighbors`) closely enough to
 * drive "buildable" highlighting client-side. The backend remains the
 * authority: this is a best-effort UI helper, not a duplicate rules
 * engine.
 *
 * Vertex/edge ids for the same physical corner/side are guaranteed
 * identical to hexMath.computeBoardGeometry's ids (same derivation), so
 * edge endpoints are recovered by matching each edge's endpoint pixel
 * coordinates back to a vertex sharing that exact (x, y) -- both are
 * computed from the same `hexCorners()` call per hex in
 * computeBoardGeometry, so the floating-point values are bit-identical,
 * not just numerically close.
 */
import type { EdgeId, VertexId, WireBoardView } from "../types/protocol";
import { HEX_SIZE, computeBoardGeometry } from "./hexMath";

export interface BoardGraph {
  /** vertexIdKey() -> the real VertexId (for building action payloads). */
  vertexIds: Map<string, VertexId>;
  /** edgeIdKey() -> the real EdgeId (for building action payloads). */
  edgeIds: Map<string, EdgeId>;
  /** edgeIdKey() -> the two vertexIdKey()s it connects. */
  edgeEndpoints: Map<string, [string, string]>;
  /** vertexIdKey() -> the edgeIdKey()s incident to it. */
  vertexIncidentEdges: Map<string, string[]>;
  /** vertexIdKey() -> the vertexIdKey()s directly connected by one edge. */
  vertexNeighbors: Map<string, string[]>;
}

export function buildBoardGraph(board: WireBoardView, hexSize: number = HEX_SIZE): BoardGraph {
  const hexCoords = board.hexes.map((h) => h.coord);
  const geometry = computeBoardGeometry(hexCoords, hexSize);

  const vertexIds = new Map<string, VertexId>();
  const pointToVertexKey = new Map<string, string>();
  for (const v of geometry.vertices.values()) {
    vertexIds.set(v.key, v.id);
    pointToVertexKey.set(`${v.x},${v.y}`, v.key);
  }

  const edgeIds = new Map<string, EdgeId>();
  const edgeEndpoints = new Map<string, [string, string]>();
  const vertexIncidentEdges = new Map<string, string[]>();
  for (const e of geometry.edges.values()) {
    edgeIds.set(e.key, e.id);
    const v1 = pointToVertexKey.get(`${e.x1},${e.y1}`);
    const v2 = pointToVertexKey.get(`${e.x2},${e.y2}`);
    if (!v1 || !v2) continue; // shouldn't happen given the shared-corners guarantee above
    edgeEndpoints.set(e.key, [v1, v2]);
    for (const vk of [v1, v2]) {
      const list = vertexIncidentEdges.get(vk);
      if (list) list.push(e.key);
      else vertexIncidentEdges.set(vk, [e.key]);
    }
  }

  const vertexNeighbors = new Map<string, string[]>();
  for (const [vk, edges] of vertexIncidentEdges) {
    const neighbors: string[] = [];
    for (const ek of edges) {
      const ends = edgeEndpoints.get(ek);
      if (!ends) continue;
      const [a, b] = ends;
      neighbors.push(a === vk ? b : a);
    }
    vertexNeighbors.set(vk, neighbors);
  }

  return { vertexIds, edgeIds, edgeEndpoints, vertexIncidentEdges, vertexNeighbors };
}
