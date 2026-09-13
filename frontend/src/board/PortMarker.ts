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
