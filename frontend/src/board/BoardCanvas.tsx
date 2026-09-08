/**
 * React component that mounts a PixiJS Application and renders a full
 * Catan board from a WireBoardView-shaped prop. Vertex/edge clicks are
 * surfaced to the parent via onVertexClick/onEdgeClick so the parent can
 * dispatch BUILD_SETTLEMENT / BUILD_CITY / BUILD_ROAD actions -- this
 * component has no knowledge of the WS protocol or game rules.
 *
 * "Buildable" highlighting is entirely driven by the legalVertexIds /
 * legalEdgeIds props: the actual build-legality computation lives in the
 * backend rules_engine (see ARCHITECTURE.md); this component just
 * highlights whatever set it's told is legal (or nothing, if the props
 * are omitted -- e.g. while it isn't the viewer's turn).
 */
import { useEffect, useRef, useState } from "react";
import { Application, Container, Graphics, Text, TextStyle } from "pixi.js";
import type { EdgeId, PlayerId, PortType, VertexId, WireBoardView } from "../types/protocol";
import { computeBoardGeometry, HEX_SIZE, edgeIdKey, hexKey, vertexIdKey } from "./hexMath";
import { buildEdgeKeySet, buildVertexKeySet } from "./boardInteraction";
import { HexTileView } from "./HexTile";
import { VertexNodeView } from "./VertexNode";
import { EdgeSegmentView } from "./EdgeSegment";
import { assignPlayerColorIndices, PLAYER_COLOR_PALETTE, TERRAIN_LABELS } from "./theme";

export interface BoardCanvasProps {
  board: WireBoardView;
  /** Canvas pixel size. Defaults to 900x760. */
  width?: number;
  height?: number;
  /** Center-to-corner pixel size of one hex. Defaults to hexMath.HEX_SIZE. */
  hexSize?: number;
  /** Vertex ids currently legal to build on -- null/undefined means "none highlighted". */
  legalVertexIds?: readonly VertexId[] | null;
  /** Edge ids currently legal to build on -- null/undefined means "none highlighted". */
  legalEdgeIds?: readonly EdgeId[] | null;
  onVertexClick?: (vertexId: VertexId) => void;
  onEdgeClick?: (edgeId: EdgeId) => void;
  /**
   * Optional explicit player -> palette-index assignment (e.g. by seat
   * number, once real lobby/game state exists). If omitted, colors are
   * assigned deterministically in first-seen order across buildings/roads.
   */
  playerColorIndex?: Record<PlayerId, number>;
  backgroundColor?: number;
}

const DEFAULT_WIDTH = 900;
const DEFAULT_HEIGHT = 760;

export function BoardCanvas(props: BoardCanvasProps): JSX.Element {
  const {
    board,
    width = DEFAULT_WIDTH,
    height = DEFAULT_HEIGHT,
    hexSize = HEX_SIZE,
    legalVertexIds,
    legalEdgeIds,
    onVertexClick,
    onEdgeClick,
    playerColorIndex,
    backgroundColor = 0x0b3d59,
  } = props;

  const hostRef = useRef<HTMLDivElement | null>(null);
  const appRef = useRef<Application | null>(null);
  const [ready, setReady] = useState(false);

  // Mount/unmount the PixiJS Application exactly once.
  useEffect(() => {
    let cancelled = false;
    const app = new Application();

    (async () => {
      await app.init({
        width,
        height,
        backgroundColor,
        antialias: true,
        resolution: typeof window !== "undefined" ? window.devicePixelRatio || 1 : 1,
        autoDensity: true,
      });
      if (cancelled) {
        app.destroy(true, { children: true });
        return;
      }
      appRef.current = app;
      hostRef.current?.appendChild(app.canvas);
      setReady(true);
    })();

    return () => {
      cancelled = true;
      setReady(false);
      const current = appRef.current;
      appRef.current = null;
      if (current) {
        if (hostRef.current && current.canvas && current.canvas.parentNode === hostRef.current) {
          hostRef.current.removeChild(current.canvas);
        }
        current.destroy(true, { children: true });
      }
    };
    // Intentionally run only on mount/unmount; size changes are handled
    // by the resize effect below without recreating the Application.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // Keep renderer size in sync with width/height props.
  useEffect(() => {
    if (ready && appRef.current) {
      appRef.current.renderer.resize(width, height);
    }
  }, [ready, width, height]);

  // Rebuild the entire scene graph whenever the board data, highlight
  // sets, or interaction callbacks change.
  useEffect(() => {
    const app = appRef.current;
    if (!app || !ready) return;

    app.stage.removeChildren();
    const root = new Container();
    app.stage.addChild(root);

    const hexCoords = board.hexes.map((h) => h.coord);
    const geometry = computeBoardGeometry(hexCoords, hexSize);

    // Center the board in the canvas.
    const hexPixelValues = [...geometry.hexPixels.values()];
    if (hexPixelValues.length > 0) {
      const xs = hexPixelValues.map((p) => p.x);
      const ys = hexPixelValues.map((p) => p.y);
      const minX = Math.min(...xs) - hexSize;
      const maxX = Math.max(...xs) + hexSize;
      const minY = Math.min(...ys) - hexSize;
      const maxY = Math.max(...ys) + hexSize;
      root.x = width / 2 - (minX + maxX) / 2;
      root.y = height / 2 - (minY + maxY) / 2;
    }

    const robberKeyStr = hexKey(board.robber_hex);
    for (const tile of board.hexes) {
      const p = geometry.hexPixels.get(hexKey(tile.coord));
      if (!p) continue;
      root.addChild(
        new HexTileView({
          tile,
          x: p.x,
          y: p.y,
          size: hexSize,
          isRobber: hexKey(tile.coord) === robberKeyStr,
        })
      );
    }

    // Determine player -> palette color index.
    const seenPlayerIds: PlayerId[] = [];
    for (const b of board.buildings) seenPlayerIds.push(b.player_id);
    for (const r of board.roads) seenPlayerIds.push(r.player_id);
    const autoColorIndex = assignPlayerColorIndices(seenPlayerIds);
    const colorIndexFor = (playerId: PlayerId): number =>
      playerColorIndex?.[playerId] ?? autoColorIndex.get(playerId) ?? 0;

    const legalVKeys = buildVertexKeySet(legalVertexIds);
    const legalEKeys = buildEdgeKeySet(legalEdgeIds);
    const buildingByVertexKey = new Map(board.buildings.map((b) => [vertexIdKey(b.vertex_id), b]));
    const roadByEdgeKey = new Map(board.roads.map((r) => [edgeIdKey(r.edge_id), r]));

    // Ports: simple ratio label at the midpoint of the two anchor vertices.
    for (const port of board.ports) {
      const v0 = geometry.vertices.get(vertexIdKey(port.vertices[0]));
      const v1 = geometry.vertices.get(vertexIdKey(port.vertices[1]));
      if (!v0 || !v1) continue;
      const midX = (v0.x + v1.x) / 2;
      const midY = (v0.y + v1.y) / 2;
      root.addChild(makePortMarker(port.port_type, midX, midY, hexSize));
    }

    for (const v of geometry.vertices.values()) {
      const b = buildingByVertexKey.get(v.key);
      root.addChild(
        new VertexNodeView({
          vertexId: v.id,
          key: v.key,
          x: v.x,
          y: v.y,
          building: b
            ? { playerId: b.player_id, buildingType: b.building_type, colorIndex: colorIndexFor(b.player_id) }
            : null,
          buildable: legalVertexIds != null && legalVKeys.has(v.key),
          onClick: onVertexClick,
        })
      );
    }

    for (const e of geometry.edges.values()) {
      const r = roadByEdgeKey.get(e.key);
      root.addChild(
        new EdgeSegmentView({
          edgeId: e.id,
          key: e.key,
          x1: e.x1,
          y1: e.y1,
          x2: e.x2,
          y2: e.y2,
          road: r ? { playerId: r.player_id, colorIndex: colorIndexFor(r.player_id) } : null,
          buildable: legalEdgeIds != null && legalEKeys.has(e.key),
          onClick: onEdgeClick,
        })
      );
    }
  }, [ready, board, hexSize, width, height, legalVertexIds, legalEdgeIds, onVertexClick, onEdgeClick, playerColorIndex]);

  return <div ref={hostRef} style={{ width, height, lineHeight: 0 }} />;
}

const PORT_LABELS: Record<PortType, string> = {
  generic: "3:1",
  brick: "2:1 B",
  lumber: "2:1 L",
  ore: "2:1 O",
  grain: "2:1 G",
  wool: "2:1 W",
};

function makePortMarker(portType: PortType, x: number, y: number, hexSize: number) {
  const container = new Container();
  container.x = x;
  container.y = y;
  container.eventMode = "none";

  const radius = hexSize * 0.22;
  const bg = new Graphics();
  bg.circle(0, 0, radius).fill({ color: 0xf3ecd2, alpha: 0.95 }).stroke({ width: 1.5, color: 0x1a1a1a, alpha: 0.8 });
  container.addChild(bg);

  const label = new Text({
    text: PORT_LABELS[portType] ?? "?",
    style: new TextStyle({
      fontFamily: "Arial, sans-serif",
      fontSize: Math.max(9, Math.round(radius * 0.62)),
      fontWeight: "bold",
      fill: 0x1a1a1a,
    }),
  });
  label.anchor.set(0.5);
  container.addChild(label);

  return container;
}

export { PLAYER_COLOR_PALETTE, TERRAIN_LABELS };
