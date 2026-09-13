/**
 * Extracted verbatim from BoardCanvas.tsx (mechanical relocation only, no
 * behavior change) so the board-reskin and nuke-flyover workstreams don't
 * touch the same section of BoardCanvas.tsx in parallel.
 */
import { Container, Graphics, Text, TextStyle } from "pixi.js";
import type { PortType } from "../types/protocol";

const PORT_LABELS: Record<PortType, string> = {
  generic: "3:1",
  brick: "2:1 B",
  lumber: "2:1 L",
  ore: "2:1 O",
  grain: "2:1 G",
  wool: "2:1 W",
};

export function makePortMarker(portType: PortType, x: number, y: number, hexSize: number) {
  const container = new Container();
  container.x = x;
  container.y = y;
  container.eventMode = "none";

  const radius = hexSize * 0.22;
  const bg = new Graphics();
  bg.circle(0, 0, radius).fill({ color: 0xf3ecd2, alpha: 0.95 }).stroke({ width: 1.5, color: 0x1a1a1a, alpha: 0.8 });
  container.addChild(bg);

  // Small nautical glyph (anchor) as a subtle watermark behind the ratio
  // text, for a bit more visual interest than a bare label on a badge.
  const glyph = new Graphics();
  drawAnchorGlyph(glyph, radius);
  container.addChild(glyph);

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

/** Tiny procedural anchor glyph (ring + shaft + crossbar + flukes), drawn
 * as a faint watermark behind a port badge's ratio text. */
function drawAnchorGlyph(g: Graphics, badgeRadius: number): void {
  const s = badgeRadius * 0.62;
  const color = 0x1a1a1a;
  const alpha = 0.28;
  g.circle(0, -s * 0.85, s * 0.22).stroke({ width: Math.max(1, s * 0.1), color, alpha });
  g.moveTo(0, -s * 0.6).lineTo(0, s * 0.75).stroke({ width: Math.max(1, s * 0.14), color, alpha });
  g.moveTo(-s * 0.4, -s * 0.15).lineTo(s * 0.4, -s * 0.15).stroke({ width: Math.max(1, s * 0.12), color, alpha });
  g.moveTo(0, s * 0.75).lineTo(-s * 0.5, s * 0.45).stroke({
    width: Math.max(1, s * 0.12),
    color,
    alpha,
    cap: "round",
  });
  g.moveTo(0, s * 0.75).lineTo(s * 0.5, s * 0.45).stroke({
    width: Math.max(1, s * 0.12),
    color,
    alpha,
    cap: "round",
  });
}
