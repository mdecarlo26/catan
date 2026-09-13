/**
 * PixiJS display object for a single terrain hex: two-tone-shaded hexagon
 * colored per terrain type, a small terrain icon in its upper portion, its
 * number token (if any), and a robber marker when it's the hex currently
 * holding the robber.
 */
import { Container, Graphics, Text, TextStyle } from "pixi.js";
import type { HexTile as WireHexTile, Terrain } from "../types/protocol";
import { hexCornerOffsets } from "./hexMath";
import { TERRAIN_ACCENT_COLORS, TERRAIN_COLORS } from "./theme";

export interface HexTileOptions {
  tile: WireHexTile;
  /** Pixel position (board-local space) of this hex's center. */
  x: number;
  y: number;
  size: number;
  isRobber: boolean;
}

export class HexTileView extends Container {
  constructor(opts: HexTileOptions) {
    super();
    this.x = opts.x;
    this.y = opts.y;
    this.eventMode = "none";
    this.buildHex(opts);
  }

  private buildHex(opts: HexTileOptions): void {
    const { tile, size, isRobber } = opts;
    const corners = hexCornerOffsets(size);
    const points = corners.flatMap((c) => [c.x, c.y]);
    const baseColor = TERRAIN_COLORS[tile.terrain] ?? 0x999999;
    const accentColor = TERRAIN_ACCENT_COLORS[tile.terrain] ?? baseColor;

    const hexGfx = new Graphics();
    // Base fill for the full hex, then a brighter accent facet across the
    // upper portion (corners NW/N/NE down to center) for a richer
    // two-tone look than a single flat color, and finally the outline on
    // top of both fills.
    hexGfx.poly(points, true).fill({ color: baseColor });
    hexGfx
      .poly([corners[5].x, corners[5].y, corners[0].x, corners[0].y, corners[1].x, corners[1].y, 0, 0], true)
      .fill({ color: accentColor, alpha: 0.85 });
    hexGfx.poly(points, true).stroke({ width: 2, color: 0x18324a, alpha: 0.55 });
    this.addChild(hexGfx);

    if (tile.terrain !== "sea") {
      const iconGfx = new Graphics();
      drawTerrainIcon(iconGfx, tile.terrain, size);
      this.addChild(iconGfx);
    }

    if (tile.number_token != null) {
      const tokenRadius = size * 0.32;
      const isHot = tile.number_token === 6 || tile.number_token === 8;

      const tokenBg = new Graphics();
      tokenBg
        .circle(0, 0, tokenRadius)
        .fill({ color: 0xf3ecd2 })
        .stroke({ width: 1.5, color: 0x1a1a1a, alpha: 0.75 });
      this.addChild(tokenBg);

      const label = new Text({
        text: String(tile.number_token),
        style: new TextStyle({
          fontFamily: "Arial, sans-serif",
          fontSize: Math.round(tokenRadius * 1.15),
          fontWeight: "bold",
          fill: isHot ? 0xc21807 : 0x1a1a1a,
        }),
      });
      label.anchor.set(0.5);
      label.y = 1;
      this.addChild(label);

      // Probability pips (1-5 dots), classic Catan token convention.
      const pipCount = 6 - Math.abs(7 - tile.number_token);
      if (pipCount > 0) {
        const pips = new Graphics();
        const pipRadius = tokenRadius * 0.08;
        const spacing = pipRadius * 2.6;
        const startX = -((pipCount - 1) * spacing) / 2;
        for (let i = 0; i < pipCount; i++) {
          pips.circle(startX + i * spacing, tokenRadius * 0.62, pipRadius).fill({
            color: isHot ? 0xc21807 : 0x1a1a1a,
          });
        }
        this.addChild(pips);
      }
    }

    if (isRobber) {
      const robber = new Graphics();
      robber
        .roundRect(-size * 0.14, -size * 0.32, size * 0.28, size * 0.5, size * 0.1)
        .fill({ color: 0x2b2b2b, alpha: 0.92 })
        .stroke({ width: 1.5, color: 0x000000 });
      robber.circle(0, -size * 0.34, size * 0.14).fill({ color: 0x2b2b2b, alpha: 0.92 }).stroke({
        width: 1.5,
        color: 0x000000,
      });
      this.addChild(robber);
    }
  }
}

// ---------------------------------------------------------------------
// Terrain icons: small procedural glyphs drawn in the upper portion of
// each hex (above the number token), one per land terrain. Same "simple
// primitives via Graphics" effort level as BoardCanvas.tsx's
// drawRobberShape/drawBombShape -- no image/sprite assets.
// ---------------------------------------------------------------------

/** Vertical anchor (relative to hex center) shared by all terrain icons,
 * and the base unit they scale their shapes from. Chosen to sit clear
 * above the number token (token top edge is at roughly -0.32 * size). */
function drawTerrainIcon(g: Graphics, terrain: Terrain, size: number): void {
  const u = size * 0.08;
  const cy = -size * 0.6;
  switch (terrain) {
    case "forest":
      drawTreeIcon(g, cy, u);
      break;
    case "hills":
      drawBrickIcon(g, cy, u);
      break;
    case "mountains":
      drawMountainIcon(g, cy, u);
      break;
    case "fields":
      drawWheatIcon(g, cy, u);
      break;
    case "pasture":
      drawSheepIcon(g, cy, u);
      break;
    case "desert":
      drawCactusIcon(g, cy, u);
      break;
  }
}

/** Pine tree: two stacked canopy triangles over a small trunk. */
function drawTreeIcon(g: Graphics, cy: number, u: number): void {
  g.poly([0, cy - 3 * u, 2 * u, cy - 0.2 * u, -2 * u, cy - 0.2 * u], true)
    .fill({ color: 0x1f4d29 })
    .stroke({ width: 1, color: 0x123018, alpha: 0.7 });
  g.poly([0, cy - 1.6 * u, 2.6 * u, cy + u, -2.6 * u, cy + u], true)
    .fill({ color: 0x2c6a3a })
    .stroke({ width: 1, color: 0x123018, alpha: 0.7 });
  g.rect(-0.5 * u, cy + u, u, 1.2 * u).fill({ color: 0x6b4423 }).stroke({ width: 0.8, color: 0x3a2414, alpha: 0.6 });
}

/** Stacked-brick motif: two offset rows of small bricks over a mortar strip. */
function drawBrickIcon(g: Graphics, cy: number, u: number): void {
  const w = 1.8 * u;
  const h = 1.1 * u;
  const gap = 0.15 * u;
  const brickFill = 0x9c4a1f;
  const mortarStroke = 0x5a2a10;
  g.roundRect(-2.7 * u, cy - 1.3 * u, 5.4 * u, 2.6 * u, 0.3 * u).fill({ color: 0x8a4513, alpha: 0.35 });
  const topRowY = cy - 1.15 * u;
  const botRowY = cy + 0.15 * u;
  for (let i = 0; i < 3; i++) {
    g.rect(-2.7 * u + i * (w + gap), topRowY, w, h)
      .fill({ color: brickFill })
      .stroke({ width: 0.8, color: mortarStroke, alpha: 0.8 });
  }
  const botOffset = (w + gap) / 2;
  for (let i = 0; i < 2; i++) {
    g.rect(-2.7 * u + botOffset + i * (w + gap), botRowY, w, h)
      .fill({ color: 0xb5651d })
      .stroke({ width: 0.8, color: mortarStroke, alpha: 0.8 });
  }
}

/** Jagged double-peak mountain silhouette with a light snow-cap accent. */
function drawMountainIcon(g: Graphics, cy: number, u: number): void {
  g.poly([1.2 * u, cy - 2 * u, 3.2 * u, cy + 1.5 * u, -0.4 * u, cy + 1.5 * u], true)
    .fill({ color: 0x5c6266 })
    .stroke({ width: 0.8, color: 0x33383b, alpha: 0.7 });
  g.poly([-1.1 * u, cy - 3 * u, 2.2 * u, cy + 1.6 * u, -3.4 * u, cy + 1.6 * u], true)
    .fill({ color: 0x7d8288 })
    .stroke({ width: 0.9, color: 0x33383b, alpha: 0.8 });
  g.poly([-1.1 * u, cy - 3 * u, 0.1 * u, cy - 1.2 * u, -0.6 * u, cy - u, -1.7 * u, cy - 1.3 * u], true).fill({
    color: 0xf2f4f5,
    alpha: 0.95,
  });
}

/** A few curved wheat-stalk strokes, each topped with a small grain cluster. */
function drawWheatIcon(g: Graphics, cy: number, u: number): void {
  const stalkColor = 0x8a6a1f;
  const grainColor = 0xf0d878;
  for (const dx of [-1.6, 0, 1.6]) {
    const baseX = dx * u;
    const topX = dx * u * 0.35;
    const baseY = cy + 1.9 * u;
    const topY = cy - 2.1 * u;
    g.moveTo(baseX, baseY).lineTo(topX, topY).stroke({ width: 1, color: stalkColor, alpha: 0.85 });
    for (let i = 0; i < 3; i++) {
      const t = i * 0.3;
      const gx = topX + (baseX - topX) * t * 0.22;
      const gy = topY + (baseY - topY) * t * 0.22;
      g.ellipse(gx - 0.5 * u, gy, 0.45 * u, 0.25 * u).fill({ color: grainColor, alpha: 0.9 });
      g.ellipse(gx + 0.5 * u, gy, 0.45 * u, 0.25 * u).fill({ color: grainColor, alpha: 0.9 });
    }
  }
}

/** Simple rounded sheep silhouette: a fluffy scalloped body plus head and legs. */
function drawSheepIcon(g: Graphics, cy: number, u: number): void {
  const bodyColor = 0xf5f0e6;
  const outline = 0x3a3a3a;
  g.ellipse(0, cy + 0.3 * u, 2.6 * u, 1.6 * u).fill({ color: bodyColor }).stroke({
    width: 0.9,
    color: outline,
    alpha: 0.6,
  });
  for (const dx of [-1.6, -0.5, 0.6, 1.7]) {
    g.circle(dx * u, cy - 0.9 * u, 0.9 * u).fill({ color: bodyColor });
  }
  g.ellipse(2.6 * u, cy + 0.1 * u, 0.9 * u, 0.7 * u).fill({ color: 0x4a4a4a }).stroke({
    width: 0.8,
    color: outline,
    alpha: 0.7,
  });
  g.rect(-1.5 * u, cy + 1.6 * u, 0.35 * u, 0.9 * u).fill({ color: 0x2e2e2e });
  g.rect(1.2 * u, cy + 1.6 * u, 0.35 * u, 0.9 * u).fill({ color: 0x2e2e2e });
}

/** Simple saguaro-style cactus: a ribbed trunk with two curled arms. */
function drawCactusIcon(g: Graphics, cy: number, u: number): void {
  const cactusColor = 0x4c7a3f;
  const ribColor = 0x355c2a;
  g.roundRect(-0.7 * u, cy - 2.3 * u, 1.4 * u, 4 * u, 0.6 * u)
    .fill({ color: cactusColor })
    .stroke({ width: 0.9, color: ribColor, alpha: 0.8 });
  g.roundRect(-2.2 * u, cy - 1 * u, 1.3 * u, 0.9 * u, 0.4 * u)
    .fill({ color: cactusColor })
    .stroke({ width: 0.8, color: ribColor, alpha: 0.8 });
  g.roundRect(-2.2 * u, cy - 2.1 * u, 0.9 * u, 1.6 * u, 0.4 * u)
    .fill({ color: cactusColor })
    .stroke({ width: 0.8, color: ribColor, alpha: 0.8 });
  g.roundRect(0.9 * u, cy - 0.3 * u, 1.3 * u, 0.9 * u, 0.4 * u)
    .fill({ color: cactusColor })
    .stroke({ width: 0.8, color: ribColor, alpha: 0.8 });
  g.roundRect(1.3 * u, cy - 1.5 * u, 0.9 * u, 1.6 * u, 0.4 * u)
    .fill({ color: cactusColor })
    .stroke({ width: 0.8, color: ribColor, alpha: 0.8 });
  for (const dx of [-0.35, 0, 0.35]) {
    g.moveTo(dx * u, cy - 2 * u).lineTo(dx * u, cy + 1.6 * u).stroke({ width: 0.5, color: ribColor, alpha: 0.5 });
  }
}
