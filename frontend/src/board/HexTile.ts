/**
 * PixiJS display object for a single terrain hex: filled hexagon colored
 * per terrain type, its number token (if any), and a robber marker when
 * it's the hex currently holding the robber.
 */
import { Container, Graphics, Text, TextStyle } from "pixi.js";
import type { HexTile as WireHexTile } from "../types/protocol";
import { hexCornerOffsets } from "./hexMath";
import { TERRAIN_COLORS } from "./theme";

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
    const fillColor = TERRAIN_COLORS[tile.terrain] ?? 0x999999;

    const hexGfx = new Graphics();
    hexGfx
      .poly(points, true)
      .fill({ color: fillColor })
      .stroke({ width: 2, color: 0x18324a, alpha: 0.55 });
    this.addChild(hexGfx);

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
