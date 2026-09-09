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
import { Application, Container, Graphics, Text, TextStyle, Ticker } from "pixi.js";
import type { EdgeId, PlayerId, PortType, VertexId, WireBoardView } from "../types/protocol";
import { computeBoardGeometry, HEX_SIZE, edgeIdKey, hexEquals, hexKey, vertexIdKey, type Point } from "./hexMath";
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
  // `root` persists across data-driven rebuilds (unlike the old
  // per-render `app.stage.removeChildren()` approach) so that
  // `effectsLayer` -- which hosts transient robber-slide / nuke-destroy
  // animations -- survives a rebuild triggered by the STATE_SNAPSHOT that
  // typically lands a moment after the event that kicked off an
  // animation. `staticLayer` is what gets torn down and rebuilt each time
  // board data actually changes.
  const rootRef = useRef<Container | null>(null);
  const staticLayerRef = useRef<Container | null>(null);
  const effectsLayerRef = useRef<Container | null>(null);
  /** The board from the previous rebuild, used to detect "the robber just
   * moved" / "a building or road just vanished" (nuke) so those changes
   * can be animated instead of just snapping to the new state. */
  const prevBoardRef = useRef<WireBoardView | null>(null);
  /** Ticker callbacks currently driving an in-flight animation, tracked so
   * they can be torn down on unmount (Application.destroy() stops the
   * ticker, but doesn't know about closures captured in `.add()`). */
  const activeTickersRef = useRef<Set<(ticker: Ticker) => void>>(new Set());
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
      const root = new Container();
      const staticLayer = new Container();
      const effectsLayer = new Container();
      root.addChild(staticLayer);
      root.addChild(effectsLayer);
      app.stage.addChild(root);

      appRef.current = app;
      rootRef.current = root;
      staticLayerRef.current = staticLayer;
      effectsLayerRef.current = effectsLayer;
      hostRef.current?.appendChild(app.canvas);
      setReady(true);
    })();

    return () => {
      cancelled = true;
      setReady(false);
      const current = appRef.current;
      appRef.current = null;
      rootRef.current = null;
      staticLayerRef.current = null;
      effectsLayerRef.current = null;
      prevBoardRef.current = null;
      if (current) {
        for (const tick of activeTickersRef.current) current.ticker.remove(tick);
        activeTickersRef.current.clear();
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

  // Rebuild the static scene graph whenever the board data, highlight
  // sets, or interaction callbacks change. `effectsLayer` is deliberately
  // left untouched here -- see the animation-trigger block at the end of
  // this effect.
  useEffect(() => {
    const app = appRef.current;
    const root = rootRef.current;
    const staticLayer = staticLayerRef.current;
    const effectsLayer = effectsLayerRef.current;
    if (!app || !ready || !root || !staticLayer || !effectsLayer) return;

    staticLayer.removeChildren();

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
      staticLayer.addChild(
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
      staticLayer.addChild(makePortMarker(port.port_type, midX, midY, hexSize));
    }

    for (const v of geometry.vertices.values()) {
      const b = buildingByVertexKey.get(v.key);
      staticLayer.addChild(
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
      staticLayer.addChild(
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

    // --- Animation polish: robber slide + nuke destroy flash ---------
    // Diffed purely from board data (previous vs. current), so this needs
    // no dedicated event props: a robber_hex change is always a
    // ROBBER_MOVED, and a building/road present a moment ago but gone now
    // can only be a nuke (normal play only ever adds buildings/roads,
    // never removes one -- a settlement upgrading to a city keeps its
    // vertex).
    const prevBoard = prevBoardRef.current;
    if (prevBoard) {
      if (!hexEquals(prevBoard.robber_hex, board.robber_hex)) {
        const fromPixel = geometry.hexPixels.get(hexKey(prevBoard.robber_hex));
        const toPixel = geometry.hexPixels.get(hexKey(board.robber_hex));
        if (fromPixel && toPixel) {
          animateRobberSlide(effectsLayer, app.ticker, fromPixel, toPixel, hexSize, activeTickersRef.current);
        }
      }

      const currentVertexKeys = new Set(board.buildings.map((b) => vertexIdKey(b.vertex_id)));
      for (const b of prevBoard.buildings) {
        if (currentVertexKeys.has(vertexIdKey(b.vertex_id))) continue;
        const v = geometry.vertices.get(vertexIdKey(b.vertex_id));
        if (v) animateDestroyFlash(effectsLayer, app.ticker, v.x, v.y, hexSize, activeTickersRef.current);
      }

      const currentEdgeKeys = new Set(board.roads.map((r) => edgeIdKey(r.edge_id)));
      for (const r of prevBoard.roads) {
        if (currentEdgeKeys.has(edgeIdKey(r.edge_id))) continue;
        const e = geometry.edges.get(edgeIdKey(r.edge_id));
        if (e) animateDestroyLine(effectsLayer, app.ticker, e.x1, e.y1, e.x2, e.y2, activeTickersRef.current);
      }
    }
    prevBoardRef.current = board;
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

// ---------------------------------------------------------------------
// Animation polish: robber slide + nuke destroy flash. Both are driven
// entirely by a PIXI Ticker (elapsed wall-clock time, eased), and remove
// themselves (graphics + ticker callback) on completion -- see the
// `effectsLayer` docstring above for why these live outside the
// per-render `staticLayer` rebuild.
// ---------------------------------------------------------------------

function easeOutCubic(t: number): number {
  return 1 - Math.pow(1 - t, 3);
}

function drawRobberShape(g: Graphics, size: number): void {
  g.clear();
  g.roundRect(-size * 0.14, -size * 0.32, size * 0.28, size * 0.5, size * 0.1)
    .fill({ color: 0x2b2b2b, alpha: 0.92 })
    .stroke({ width: 1.5, color: 0x000000 });
  g.circle(0, -size * 0.34, size * 0.14)
    .fill({ color: 0x2b2b2b, alpha: 0.92 })
    .stroke({ width: 1.5, color: 0x000000 });
}

const ROBBER_SLIDE_MS = 380;

/** Slides a ghost robber token from `fromPixel` to `toPixel`, fading out
 * near the end (the static robber marker is already drawn at its new,
 * final hex by HexTileView -- this ghost just sells the motion of
 * getting there instead of the token teleporting). */
function animateRobberSlide(
  effectsLayer: Container,
  ticker: Ticker,
  fromPixel: Point,
  toPixel: Point,
  size: number,
  activeTickers: Set<(t: Ticker) => void>
): void {
  const ghost = new Graphics();
  drawRobberShape(ghost, size);
  ghost.x = fromPixel.x;
  ghost.y = fromPixel.y;
  effectsLayer.addChild(ghost);

  const start = performance.now();
  const tick = () => {
    const t = Math.min(1, (performance.now() - start) / ROBBER_SLIDE_MS);
    const eased = easeOutCubic(t);
    ghost.x = fromPixel.x + (toPixel.x - fromPixel.x) * eased;
    ghost.y = fromPixel.y + (toPixel.y - fromPixel.y) * eased;
    ghost.alpha = t < 0.7 ? 1 : Math.max(0, 1 - (t - 0.7) / 0.3);
    if (t >= 1) {
      ticker.remove(tick);
      activeTickers.delete(tick);
      ghost.destroy();
    }
  };
  activeTickers.add(tick);
  ticker.add(tick);
}

const DESTROY_FLASH_MS = 550;

/** A brief expanding, fading burst at a destroyed settlement/city's
 * vertex -- feedback for a nuked building disappearing instead of just
 * vanishing on the next snapshot. */
function animateDestroyFlash(
  effectsLayer: Container,
  ticker: Ticker,
  x: number,
  y: number,
  size: number,
  activeTickers: Set<(t: Ticker) => void>
): void {
  const gfx = new Graphics();
  gfx.x = x;
  gfx.y = y;
  effectsLayer.addChild(gfx);

  const start = performance.now();
  const tick = () => {
    const t = Math.min(1, (performance.now() - start) / DESTROY_FLASH_MS);
    const radius = size * (0.25 + 0.55 * t);
    gfx.clear();
    gfx
      .circle(0, 0, radius)
      .fill({ color: 0xff5a3c, alpha: 0.85 * (1 - t) })
      .stroke({ width: 2, color: 0xffffff, alpha: 0.9 * (1 - t) });
    if (t >= 1) {
      ticker.remove(tick);
      activeTickers.delete(tick);
      gfx.destroy();
    }
  };
  activeTickers.add(tick);
  ticker.add(tick);
}

/** A brief fading flash along a destroyed road's edge -- the road
 * equivalent of animateDestroyFlash. */
function animateDestroyLine(
  effectsLayer: Container,
  ticker: Ticker,
  x1: number,
  y1: number,
  x2: number,
  y2: number,
  activeTickers: Set<(t: Ticker) => void>
): void {
  const gfx = new Graphics();
  effectsLayer.addChild(gfx);

  const start = performance.now();
  const tick = () => {
    const t = Math.min(1, (performance.now() - start) / DESTROY_FLASH_MS);
    gfx.clear();
    gfx
      .moveTo(x1, y1)
      .lineTo(x2, y2)
      .stroke({ width: 10 * (1 - t * 0.4), color: 0xff5a3c, alpha: 0.85 * (1 - t), cap: "round" });
    if (t >= 1) {
      ticker.remove(tick);
      activeTickers.delete(tick);
      gfx.destroy();
    }
  };
  activeTickers.add(tick);
  ticker.add(tick);
}

export { PLAYER_COLOR_PALETTE, TERRAIN_LABELS };
