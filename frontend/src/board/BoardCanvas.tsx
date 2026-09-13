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
import { Application, Container, Graphics, Ticker } from "pixi.js";
import type { BuildingType, EdgeId, PlayerId, VertexId, WireBoardView } from "../types/protocol";
import {
  computeBoardGeometry,
  HEX_SIZE,
  edgeIdKey,
  hexEquals,
  hexKey,
  vertexIdKey,
  type EdgeGeometry,
  type Point,
} from "./hexMath";
import { buildEdgeKeySet, buildVertexKeySet } from "./boardInteraction";
import { HexTileView } from "./HexTile";
import { VertexNodeView } from "./VertexNode";
import { EdgeSegmentView } from "./EdgeSegment";
import { assignPlayerColorIndices, playerColor, PLAYER_COLOR_PALETTE, TERRAIN_LABELS } from "./theme";
import type { NukeEventRecord } from "../state/gameStore";
import { makePortMarker } from "./PortMarker";

/**
 * Resolve a player -> palette-color-index function for `board`, matching
 * the same "explicit override, else first-seen order across
 * buildings/roads" convention used by the static scene rebuild below. Used
 * both there and by the nuke-drop animation (which needs the victim's
 * color independently of the vertex/edge rebuild).
 */
function resolveColorIndexFor(
  board: WireBoardView,
  playerColorIndex: Record<PlayerId, number> | undefined
): (playerId: PlayerId) => number {
  const seenPlayerIds: PlayerId[] = [];
  for (const b of board.buildings) seenPlayerIds.push(b.player_id);
  for (const r of board.roads) seenPlayerIds.push(r.player_id);
  const autoColorIndex = assignPlayerColorIndices(seenPlayerIds);
  return (playerId: PlayerId): number => playerColorIndex?.[playerId] ?? autoColorIndex.get(playerId) ?? 0;
}

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
  /**
   * Most recent NUKE_DROPPED event (see gameStore.ts's NukeEventRecord),
   * or null/undefined if none has happened yet this session. Threaded in
   * directly so the falling-bomb animation can start the instant the
   * event arrives, ahead of the STATE_SNAPSHOT that actually removes the
   * destroyed vertex/edge from `board` -- see the nukeEvent-keyed effect
   * below for the full explanation.
   */
  nukeEvent?: NukeEventRecord | null;
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
    nukeEvent,
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
  /**
   * Vertex/edge keys ("already animated via nukeEvent") currently mid-way
   * through the event-driven `animateNukeDrop` sequence. The board-diff
   * pass below (which detects a nuke the *old* way, by noticing a
   * building/road vanished between renders) checks this set and skips
   * firing its own plain flash/line for anything in it, so a nuke plays
   * exactly one coherent animation instead of two overlapping ones. Each
   * key is removed automatically (see `nukeDedupTimeoutsRef`) once the
   * event-driven animation has had time to fully finish.
   */
  const nukeDedupKeysRef = useRef<Set<string>>(new Set());
  /** Pending `window.setTimeout` ids that clear `nukeDedupKeysRef` entries, tracked for unmount cleanup. */
  const nukeDedupTimeoutsRef = useRef<Set<ReturnType<typeof window.setTimeout>>>(new Set());
  /** Last `nukeEvent.seq` already handled by the nuke-drop effect, guarding against React StrictMode's double-invoke and re-renders that don't carry a new event. */
  const processedNukeSeqRef = useRef<number | null>(null);
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
      for (const timeoutId of nukeDedupTimeoutsRef.current) window.clearTimeout(timeoutId);
      nukeDedupTimeoutsRef.current.clear();
      nukeDedupKeysRef.current.clear();
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
    const colorIndexFor = resolveColorIndexFor(board, playerColorIndex);

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
        const key = vertexIdKey(b.vertex_id);
        if (currentVertexKeys.has(key)) continue;
        // Already shown a full fall+explosion for this vertex via the
        // nukeEvent-driven effect below -- skip the plain flash so this
        // destruction doesn't animate twice.
        if (nukeDedupKeysRef.current.has(key)) continue;
        const v = geometry.vertices.get(key);
        if (v) animateDestroyFlash(effectsLayer, app.ticker, v.x, v.y, hexSize, activeTickersRef.current);
      }

      const currentEdgeKeys = new Set(board.roads.map((r) => edgeIdKey(r.edge_id)));
      for (const r of prevBoard.roads) {
        const key = edgeIdKey(r.edge_id);
        if (currentEdgeKeys.has(key)) continue;
        // Already handled in sync with the nuke impact phase below.
        if (nukeDedupKeysRef.current.has(key)) continue;
        const e = geometry.edges.get(key);
        if (e) animateDestroyLine(effectsLayer, app.ticker, e.x1, e.y1, e.x2, e.y2, activeTickersRef.current);
      }
    }
    prevBoardRef.current = board;
  }, [ready, board, hexSize, width, height, legalVertexIds, legalEdgeIds, onVertexClick, onEdgeClick, playerColorIndex]);

  // Event-driven nuke-drop animation: fires the instant a NUKE_DROPPED
  // event lands (via the `nukeEvent` prop), well ahead of the
  // STATE_SNAPSHOT that actually removes the destroyed vertex/edge from
  // `board` -- the diff-based effect above can only react *after* that
  // snapshot arrives, which is too late to show anything "landing" on the
  // piece before it disappears. Deliberately keyed on `nukeEvent?.seq`
  // alone (not `board`) so this captures the board exactly as it stood
  // the moment the event was processed (see NukeEventRecord's doc comment
  // in gameStore.ts) rather than re-running on every later snapshot.
  useEffect(() => {
    const app = appRef.current;
    const effectsLayer = effectsLayerRef.current;
    if (!app || !ready || !effectsLayer) return;
    if (!nukeEvent) return;
    // Guards against React StrictMode's dev-mode double-invoke and against
    // this effect re-running (e.g. on `ready` flipping) without a genuinely
    // new event.
    if (processedNukeSeqRef.current === nukeEvent.seq) return;
    processedNukeSeqRef.current = nukeEvent.seq;

    const hexCoords = board.hexes.map((h) => h.coord);
    const geometry = computeBoardGeometry(hexCoords, hexSize);
    const vertexGeom = geometry.vertices.get(vertexIdKey(nukeEvent.destroyed_vertex));
    // If the vertex can't be resolved (e.g. a resync raced this event
    // away, or the board geometry doesn't match yet) there's nothing
    // sensible to animate -- let the diff-based fallback handle it.
    if (!vertexGeom) return;
    const edgeGeom = geometry.edges.get(edgeIdKey(nukeEvent.destroyed_edge)) ?? null;

    const colorIndexFor = resolveColorIndexFor(board, playerColorIndex);
    const victimColorIndex = colorIndexFor(nukeEvent.target);

    // Mark these keys as "already animated via nukeEvent" so the
    // diff-based pass above skips them once the corresponding
    // STATE_SNAPSHOT lands and actually removes the building/road.
    const vertexKey = vertexIdKey(nukeEvent.destroyed_vertex);
    const edgeKey = edgeIdKey(nukeEvent.destroyed_edge);
    nukeDedupKeysRef.current.add(vertexKey);
    nukeDedupKeysRef.current.add(edgeKey);
    const timeoutId = window.setTimeout(() => {
      nukeDedupKeysRef.current.delete(vertexKey);
      nukeDedupKeysRef.current.delete(edgeKey);
      nukeDedupTimeoutsRef.current.delete(timeoutId);
    }, NUKE_DEDUP_MS);
    nukeDedupTimeoutsRef.current.add(timeoutId);

    animateNukeFlyoverThenDrop(
      effectsLayer,
      app.ticker,
      vertexGeom,
      edgeGeom,
      hexSize,
      activeTickersRef.current,
      victimColorIndex,
      nukeEvent.destroyedBuildingType,
      width
    );
    // Intentionally keyed on nukeEvent?.seq alone -- see comment above.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ready, nukeEvent?.seq]);

  return <div ref={hostRef} style={{ width, height, lineHeight: 0 }} />;
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

// ---------------------------------------------------------------------
// Nuke drop: a procedural bomb falls onto the destroyed vertex, then
// explodes (shockwave ring + radiating debris) in sync with the
// destroyed road's existing animateDestroyLine treatment. Event-driven
// (see the nukeEvent-keyed effect above) rather than diffed from board
// data, so it can start the instant NUKE_DROPPED arrives instead of only
// after the piece has already vanished from the next snapshot.
// ---------------------------------------------------------------------

const NUKE_FALL_MS = 400;
const NUKE_IMPACT_MS = 400;
/** Duration of the plane-flyover lead-in (see `animateNukeFlyoverThenDrop`
 * below) that plays before the bomb fall+impact sequence starts. */
const NUKE_FLYOVER_MS = 850;
/** How long a vertex/edge key stays in `nukeDedupKeysRef` -- must outlast
 * the flyover lead-in *plus* both the impact phase and the
 * (independently-timed) road-line flash it kicks off, so the diff-based
 * pass never fires a duplicate for either while any part of the combined
 * flyover -> fall -> impact sequence is still in flight. */
const NUKE_DEDUP_MS = NUKE_FLYOVER_MS + NUKE_FALL_MS + Math.max(NUKE_IMPACT_MS, DESTROY_FLASH_MS) + 100;
const EXPLOSION_ORANGE = 0xff5a3c;

function easeInQuad(t: number): number {
  return t * t;
}

/** Blend two 0xRRGGBB colors; t=0 is pure `a`, t=1 is pure `b`. */
function blendColors(a: number, b: number, t: number): number {
  const ar = (a >> 16) & 0xff;
  const ag = (a >> 8) & 0xff;
  const ab = a & 0xff;
  const br = (b >> 16) & 0xff;
  const bg = (b >> 8) & 0xff;
  const bb = b & 0xff;
  const r = Math.round(ar + (br - ar) * t);
  const g = Math.round(ag + (bg - ag) * t);
  const bl = Math.round(ab + (bb - ab) * t);
  return (r << 16) | (g << 8) | bl;
}

/** Draws a small procedural bomb: a dark rounded body plus a simple
 * fin + lit-fuse-spark accent -- same level of effort as drawRobberShape,
 * no image/sprite assets (this codebase draws every piece with Graphics). */
function drawBombShape(g: Graphics, size: number): void {
  g.clear();
  g.circle(0, size * 0.05, size * 0.22)
    .fill({ color: 0x1c1c1c, alpha: 0.95 })
    .stroke({ width: 1.5, color: 0x000000 });
  g.poly([-size * 0.09, -size * 0.18, size * 0.09, -size * 0.18, 0, -size * 0.32], true)
    .fill({ color: 0x1c1c1c, alpha: 0.95 })
    .stroke({ width: 1, color: 0x000000 });
  g.moveTo(0, -size * 0.32)
    .lineTo(size * 0.06, -size * 0.44)
    .stroke({ width: 1.5, color: 0x8a5a2b });
  g.circle(size * 0.07, -size * 0.47, size * 0.05).fill({ color: 0xffd23f, alpha: 0.95 });
}

/**
 * Multi-phase nuke-drop animation on `effectsLayer`:
 *   1. Fall (~NUKE_FALL_MS, ease-in): a bomb shape drops from well above
 *      `vertexPixel` down onto it.
 *   2. Impact (~NUKE_IMPACT_MS): an expanding shockwave ring plus a
 *      handful of radiating debris lines, tinted with a blend of the
 *      victim's player color and explosion orange, scaled slightly larger
 *      for a destroyed city than a settlement. The destroyed road's
 *      existing shrink+fade line effect is kicked off at the same moment
 *      impact begins, so the two read as one simultaneous explosion
 *      rather than the road getting its own separate bomb-drop.
 */
function animateNukeDrop(
  effectsLayer: Container,
  ticker: Ticker,
  vertexPixel: Point,
  edgeGeom: EdgeGeometry | null,
  size: number,
  activeTickers: Set<(t: Ticker) => void>,
  victimColorIndex: number,
  buildingType: BuildingType | null
): void {
  const bomb = new Graphics();
  drawBombShape(bomb, size);
  const startY = vertexPixel.y - size * 3;
  bomb.x = vertexPixel.x;
  bomb.y = startY;
  effectsLayer.addChild(bomb);

  const impact = new Graphics();
  impact.x = vertexPixel.x;
  impact.y = vertexPixel.y;
  effectsLayer.addChild(impact);

  const buildingScale = buildingType === "city" ? 1.3 : 1.0;
  const ringColor = blendColors(playerColor(victimColorIndex), EXPLOSION_ORANGE, 0.5);
  const DEBRIS_COUNT = 8;
  const debrisAngles = Array.from(
    { length: DEBRIS_COUNT },
    (_, i) => (Math.PI * 2 * i) / DEBRIS_COUNT + 0.3
  );

  let impactStarted = false;
  const start = performance.now();
  const tick = () => {
    const elapsed = performance.now() - start;

    if (elapsed < NUKE_FALL_MS) {
      const eased = easeInQuad(elapsed / NUKE_FALL_MS);
      bomb.y = startY + (vertexPixel.y - startY) * eased;
      bomb.rotation = eased * 0.6;
      return;
    }

    if (!impactStarted) {
      impactStarted = true;
      bomb.destroy();
      if (edgeGeom) {
        animateDestroyLine(effectsLayer, ticker, edgeGeom.x1, edgeGeom.y1, edgeGeom.x2, edgeGeom.y2, activeTickers);
      }
    }

    const it = Math.min(1, (elapsed - NUKE_FALL_MS) / NUKE_IMPACT_MS);
    const radius = size * buildingScale * (0.3 + 0.65 * it);
    const fade = 1 - it;
    impact.clear();
    impact
      .circle(0, 0, radius)
      .fill({ color: ringColor, alpha: 0.85 * fade })
      .stroke({ width: 2.5, color: 0xffffff, alpha: 0.9 * fade });
    const debrisLen = size * buildingScale * (0.25 + 0.5 * it);
    const debrisInner = radius * 0.5;
    for (const angle of debrisAngles) {
      const dx = Math.cos(angle);
      const dy = Math.sin(angle);
      impact
        .moveTo(dx * debrisInner, dy * debrisInner)
        .lineTo(dx * (debrisInner + debrisLen), dy * (debrisInner + debrisLen))
        .stroke({ width: 2, color: ringColor, alpha: 0.8 * fade });
    }

    if (it >= 1) {
      ticker.remove(tick);
      activeTickers.delete(tick);
      impact.destroy();
    }
  };
  activeTickers.add(tick);
  ticker.add(tick);
}

// ---------------------------------------------------------------------
// Plane flyover: a lead-in that plays *before* the bomb fall+impact
// above. Composes strictly in front of `animateNukeDrop` -- this section
// draws its own procedural plane, flies it across the board at constant
// altitude/velocity, and on reaching the target's x position hands off to
// the existing, untouched `animateNukeDrop` to run the fall+impact on its
// own independent timeline, exactly as it does when called directly.
// ---------------------------------------------------------------------

/** Draws a small procedural plane silhouette -- fuselage + nose, two
 * wing triangles, and a small tail fin -- same modest effort level as
 * `drawBombShape`, pure Graphics, no image/sprite assets. Drawn nose-first
 * along +x; callers flip it horizontally (`scale.x = -1`) to face left. */
function drawPlaneShape(g: Graphics, size: number): void {
  g.clear();
  // Fuselage.
  g.roundRect(-size * 0.32, -size * 0.05, size * 0.5, size * 0.1, size * 0.04)
    .fill({ color: 0x8a97a6, alpha: 0.95 })
    .stroke({ width: 1.2, color: 0x2b2b2b });
  // Nose cone.
  g.poly([size * 0.18, -size * 0.05, size * 0.18, size * 0.05, size * 0.34, 0], true)
    .fill({ color: 0x8a97a6, alpha: 0.95 })
    .stroke({ width: 1, color: 0x2b2b2b });
  // Wings (one on each side of the fuselage).
  g.poly([-size * 0.04, -size * 0.03, size * 0.06, -size * 0.03, -size * 0.02, -size * 0.34], true)
    .fill({ color: 0x5c6773, alpha: 0.95 })
    .stroke({ width: 1, color: 0x2b2b2b });
  g.poly([-size * 0.04, size * 0.03, size * 0.06, size * 0.03, -size * 0.02, size * 0.34], true)
    .fill({ color: 0x5c6773, alpha: 0.95 })
    .stroke({ width: 1, color: 0x2b2b2b });
  // Tail fin.
  g.poly([-size * 0.32, -size * 0.02, -size * 0.32, size * 0.02, -size * 0.42, -size * 0.14], true)
    .fill({ color: 0x5c6773, alpha: 0.95 })
    .stroke({ width: 1, color: 0x2b2b2b });
}

/** How long the plane keeps flying in a straight line after releasing its
 * payload before it's torn down -- just enough to visibly clear the board
 * rather than vanishing the instant the bomb is released. */
const NUKE_FLYOVER_EXIT_MS = 300;

/**
 * Plane-flyover lead-in for the nuke-drop sequence:
 *   1. Flyover (~NUKE_FLYOVER_MS, constant velocity): a plane enters from
 *      off-canvas on whichever horizontal side is farther from the
 *      target's x position, at a fixed altitude above the board, and
 *      flies straight toward the target's x.
 *   2. Release: the instant the plane's x reaches `vertexPixel.x`, this
 *      removes its own ticker callback's per-frame flight-update branch
 *      and calls the existing, unmodified `animateNukeDrop(...)` with the
 *      exact same arguments it would otherwise have been called with --
 *      the fall+impact plays out on its own independent timeline from
 *      that moment, identically to calling `animateNukeDrop` directly.
 *   3. Exit: the plane itself keeps flying in a straight line (fading
 *      out) for `NUKE_FLYOVER_EXIT_MS` more, then removes its ticker
 *      callback and destroys itself -- same self-removing pattern as
 *      every other animation in this file.
 *
 * Takes one parameter beyond `animateNukeDrop`'s list -- `canvasWidth`
 * (the `width` prop from `BoardCanvas`) -- because picking the entry side
 * and the off-canvas start/end x positions requires knowing the canvas's
 * horizontal extent, which nothing else passed in captures. `effectsLayer`
 * is a child of `root` (see the mount effect above), whose `x` is the
 * translation that centers the board in the canvas, so `effectsLayer`'s
 * *local* left/right canvas edges are derived from `canvasWidth` and
 * `effectsLayer.parent.x` (i.e. `root.x`) rather than needing `root.x`
 * threaded in as its own argument too.
 */
function animateNukeFlyoverThenDrop(
  effectsLayer: Container,
  ticker: Ticker,
  vertexPixel: Point,
  edgeGeom: EdgeGeometry | null,
  size: number,
  activeTickers: Set<(t: Ticker) => void>,
  victimColorIndex: number,
  buildingType: BuildingType | null,
  canvasWidth: number
): void {
  const rootX = (effectsLayer.parent as Container | null)?.x ?? 0;
  const localLeftEdge = -rootX;
  const localRightEdge = canvasWidth - rootX;

  // Entry side = whichever horizontal canvas edge is farther from the
  // target, so the flight path always crosses over/near it rather than
  // starting right next to it.
  const distToLeft = vertexPixel.x - localLeftEdge;
  const distToRight = localRightEdge - vertexPixel.x;
  const enterFromLeft = distToLeft >= distToRight;

  const margin = size * 2;
  const startX = enterFromLeft ? localLeftEdge - margin : localRightEdge + margin;
  const flyY = vertexPixel.y - size * 4.2;

  const plane = new Graphics();
  drawPlaneShape(plane, size);
  plane.scale.x = enterFromLeft ? 1 : -1;
  plane.x = startX;
  plane.y = flyY;
  effectsLayer.addChild(plane);

  const totalDeltaX = vertexPixel.x - startX;
  const velocityXPerMs = totalDeltaX / NUKE_FLYOVER_MS;

  let released = false;
  const start = performance.now();
  const tick = () => {
    const elapsed = performance.now() - start;

    if (!released) {
      const t = Math.min(1, elapsed / NUKE_FLYOVER_MS);
      plane.x = startX + totalDeltaX * t;
      plane.y = flyY;
      if (t >= 1) {
        released = true;
        // Hand off to the existing, unmodified fall+impact sequence --
        // same arguments it would receive if called directly, running on
        // its own independent timeline from this moment.
        animateNukeDrop(effectsLayer, ticker, vertexPixel, edgeGeom, size, activeTickers, victimColorIndex, buildingType);
      }
      return;
    }

    // Post-release: keep flying straight off-screen, fading out, then
    // self-remove -- same lifecycle every other animation in this file
    // follows.
    const exitElapsed = elapsed - NUKE_FLYOVER_MS;
    const exitT = Math.min(1, exitElapsed / NUKE_FLYOVER_EXIT_MS);
    plane.x = vertexPixel.x + velocityXPerMs * exitElapsed;
    plane.alpha = 1 - exitT;
    if (exitT >= 1) {
      ticker.remove(tick);
      activeTickers.delete(tick);
      plane.destroy();
    }
  };
  activeTickers.add(tick);
  ticker.add(tick);
}

export { PLAYER_COLOR_PALETTE, TERRAIN_LABELS };
