/**
 * Client-side placement-legality helper, mirroring the *shape* of
 * backend/app/game/rules_engine.py's connectivity/distance checks
 * (`_vertex_free_and_far_enough`, `_player_has_connection_to_vertex`,
 * `_player_has_connection_to_edge`) closely enough to drive "buildable"
 * highlighting in the real UI. This is a UI convenience only -- the
 * backend re-validates every BUILD_* action and is the sole authority;
 * a client/server disagreement here just means a highlight is
 * missing/extra, never a state corruption (the server would reject an
 * illegal click with an ERROR event).
 */
import type { PlayerId, VertexId, EdgeId, WireBoardView } from "../types/protocol";
import type { BoardGraph } from "./boardGraph";
import { edgeIdKey, vertexIdKey } from "./hexMath";

interface OwnedThing {
  player_id: PlayerId;
}

function buildingMap(board: WireBoardView): Map<string, OwnedThing & { building_type: string }> {
  return new Map(board.buildings.map((b) => [vertexIdKey(b.vertex_id), b]));
}

function roadMap(board: WireBoardView): Map<string, OwnedThing> {
  return new Map(board.roads.map((r) => [edgeIdKey(r.edge_id), r]));
}

export function playerHasConnectionToVertex(
  graph: BoardGraph,
  roads: Map<string, OwnedThing>,
  playerId: PlayerId,
  vertexKey: string
): boolean {
  const incident = graph.vertexIncidentEdges.get(vertexKey) ?? [];
  return incident.some((ek) => roads.get(ek)?.player_id === playerId);
}

export function playerHasConnectionToEdge(
  graph: BoardGraph,
  buildings: Map<string, OwnedThing>,
  roads: Map<string, OwnedThing>,
  playerId: PlayerId,
  edgeKey: string
): boolean {
  const ends = graph.edgeEndpoints.get(edgeKey);
  if (!ends) return false;
  for (const vk of ends) {
    const building = buildings.get(vk);
    if (building && building.player_id === playerId) return true;
    if (playerHasConnectionToVertex(graph, roads, playerId, vk)) return true;
  }
  return false;
}

/**
 * Legal settlement vertex ids: unoccupied, no occupied neighbor
 * (distance rule). `requireConnection` should be true for MAIN-phase
 * builds (must connect to the player's own road network) and false
 * during SETUP (no connection requirement for the free initial
 * placements).
 */
export function legalSettlementVertexIds(
  graph: BoardGraph,
  board: WireBoardView,
  playerId: PlayerId,
  requireConnection: boolean
): VertexId[] {
  const buildings = buildingMap(board);
  const roads = roadMap(board);
  const legal: VertexId[] = [];
  for (const vk of graph.vertexIds.keys()) {
    if (buildings.has(vk)) continue;
    const neighbors = graph.vertexNeighbors.get(vk) ?? [];
    if (neighbors.some((n) => buildings.has(n))) continue;
    if (requireConnection && !playerHasConnectionToVertex(graph, roads, playerId, vk)) continue;
    legal.push(graph.vertexIds.get(vk)!);
  }
  return legal;
}

/** Legal road edge ids: unoccupied and connected to the player's own network. */
export function legalRoadEdgeIds(graph: BoardGraph, board: WireBoardView, playerId: PlayerId): EdgeId[] {
  const buildings = buildingMap(board);
  const roads = roadMap(board);
  const legal: EdgeId[] = [];
  for (const ek of graph.edgeIds.keys()) {
    if (roads.has(ek)) continue;
    if (playerHasConnectionToEdge(graph, buildings, roads, playerId, ek)) {
      legal.push(graph.edgeIds.get(ek)!);
    }
  }
  return legal;
}

/** Vertex ids the player owns a SETTLEMENT on (legal BUILD_CITY targets). */
export function legalCityVertexIds(board: WireBoardView, playerId: PlayerId): VertexId[] {
  return board.buildings
    .filter((b) => b.player_id === playerId && b.building_type === "settlement")
    .map((b) => b.vertex_id);
}
