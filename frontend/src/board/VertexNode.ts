/**
 * PixiJS display object for a single buildable vertex: a hoverable,
 * clickable hit target that renders a settlement/city icon (colored by
 * owning player) when occupied, and a highlight ring when it's a legal
 * build target.
 */
import { Container, FederatedPointerEvent, Graphics } from "pixi.js";
import type { BuildingType, PlayerId, VertexId } from "../types/protocol";
import { playerColor } from "./theme";

export interface VertexBuildingInfo {
  playerId: PlayerId;
  buildingType: BuildingType;
  colorIndex: number;
}

export interface VertexNodeOptions {
  vertexId: VertexId;
  /** hexMath.vertexIdKey(vertexId) -- passed in rather than recomputed. */
  key: string;
  x: number;
  y: number;
  radius?: number;
  building?: VertexBuildingInfo | null;
  /** Whether this vertex is currently a legal build target. */
  buildable: boolean;
  onClick?: (vertexId: VertexId) => void;
}

const HIGHLIGHT_COLOR = 0xffd84d;
const HOVER_COLOR = 0xffffff;

export class VertexNodeView extends Container {
  readonly vertexId: VertexId;
  readonly vertexKey: string;

  private radius: number;
  private buildable: boolean;
  private hovered = false;
  private onClickCb?: (vertexId: VertexId) => void;

  private highlightGfx: Graphics;
  private pieceLayer: Container;

  constructor(opts: VertexNodeOptions) {
    super();
    this.vertexId = opts.vertexId;
    this.vertexKey = opts.key;
    this.x = opts.x;
    this.y = opts.y;
    this.radius = opts.radius ?? 9;
    this.buildable = opts.buildable;
    this.onClickCb = opts.onClick;

    this.highlightGfx = new Graphics();
    this.pieceLayer = new Container();
    this.addChild(this.highlightGfx);
    this.addChild(this.pieceLayer);

    this.redrawHighlight();
    this.renderBuilding(opts.building ?? null);
    this.applyInteractivity();
  }

  private applyInteractivity(): void {
    this.eventMode = this.buildable ? "static" : "none";
    this.cursor = this.buildable ? "pointer" : "default";
    // Generous circular hit area, easier to click than the visible dot.
    const hitRadius = this.radius * 2.4;
    this.hitArea = {
      contains: (px: number, py: number) => px * px + py * py <= hitRadius * hitRadius,
    };

    this.removeAllListeners();
    this.on("pointerover", this.handlePointerOver);
    this.on("pointerout", this.handlePointerOut);
    this.on("pointerdown", this.handlePointerDown);
  }

  private handlePointerOver = (): void => {
    this.hovered = true;
    this.redrawHighlight();
  };

  private handlePointerOut = (): void => {
    this.hovered = false;
    this.redrawHighlight();
  };

  private handlePointerDown = (e: FederatedPointerEvent): void => {
    e.stopPropagation();
    if (this.buildable && this.onClickCb) {
      this.onClickCb(this.vertexId);
    }
  };

  private redrawHighlight(): void {
    this.highlightGfx.clear();
    if (this.buildable) {
      const color = this.hovered ? HOVER_COLOR : HIGHLIGHT_COLOR;
      this.highlightGfx
        .circle(0, 0, this.radius)
        .fill({ color, alpha: this.hovered ? 0.95 : 0.6 })
        .stroke({ width: 1.5, color: 0x2b2b2b, alpha: 0.8 });
    } else if (this.hovered) {
      this.highlightGfx.circle(0, 0, this.radius * 0.7).fill({ color: 0xffffff, alpha: 0.25 });
    }
  }

  /** (Re)draw the settlement/city piece, or clear it if `building` is null. */
  renderBuilding(building: VertexBuildingInfo | null): void {
    this.pieceLayer.removeChildren();
    if (!building) return;

    const color = playerColor(building.colorIndex);
    const g = new Graphics();
    const r = this.radius;
    // Subtle drop-shadow: the same silhouette(s), offset and dark, drawn
    // first so the colored piece reads with a bit of depth against the
    // board rather than sitting perfectly flat.
    const shadowOffset = r * 0.12;

    if (building.buildingType === "settlement") {
      drawHouse(g, shadowOffset, shadowOffset, r * 1.0, r * 0.95, r * 0.65, 0x0a0a0a, 0.35);
      drawHouse(g, 0, 0, r * 1.0, r * 0.95, r * 0.65, color);
    } else {
      // City: a bigger main house plus a smaller attached block, per the
      // classic Catan city silhouette (visibly larger than a settlement).
      drawHouse(g, -r * 0.35 + shadowOffset, shadowOffset, r * 1.15, r * 1.05, r * 0.75, 0x0a0a0a, 0.35);
      drawHouse(g, r * 0.85 + shadowOffset, r * 0.25 + shadowOffset, r * 0.6, r * 0.55, r * 0.4, 0x0a0a0a, 0.35);
      drawHouse(g, -r * 0.35, 0, r * 1.15, r * 1.05, r * 0.75, color);
      drawHouse(g, r * 0.85, r * 0.25, r * 0.6, r * 0.55, r * 0.4, color);
    }

    this.pieceLayer.addChild(g);
  }

  setBuildable(buildable: boolean): void {
    if (this.buildable === buildable) return;
    this.buildable = buildable;
    this.applyInteractivity();
    this.redrawHighlight();
  }
}

/** Draws a simple pentagon "house" (roof + walls) centered at (cx, cy).
 * `alpha` lets callers draw a translucent dark copy as a drop-shadow pass
 * before the opaque, colored piece -- same shape/anchor math either way. */
function drawHouse(
  g: Graphics,
  cx: number,
  cy: number,
  halfWidth: number,
  wallHeight: number,
  roofHeight: number,
  color: number,
  alpha = 1
): void {
  const baseY = cy + wallHeight * 0.5;
  const topY = cy - wallHeight * 0.5;
  const peakY = topY - roofHeight;
  g.poly(
    [
      cx - halfWidth, baseY,
      cx - halfWidth, topY,
      cx, peakY,
      cx + halfWidth, topY,
      cx + halfWidth, baseY,
    ],
    true
  )
    .fill({ color, alpha })
    .stroke({ width: 1.5, color: 0x1a1a1a, alpha: alpha * 0.9 });
}
