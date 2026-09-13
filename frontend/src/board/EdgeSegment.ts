/**
 * PixiJS display object for a single buildable edge (road slot): a
 * hoverable, clickable line hit target that renders a road segment
 * (colored by owning player) when occupied, and a highlight when it's a
 * legal build target.
 */
import { Container, FederatedPointerEvent, Graphics } from "pixi.js";
import type { EdgeId, PlayerId } from "../types/protocol";
import { distancePointToSegment } from "./hexMath";
import { playerColor } from "./theme";

export interface EdgeRoadInfo {
  playerId: PlayerId;
  colorIndex: number;
}

export interface EdgeSegmentOptions {
  edgeId: EdgeId;
  /** hexMath.edgeIdKey(edgeId) -- passed in rather than recomputed. */
  key: string;
  x1: number;
  y1: number;
  x2: number;
  y2: number;
  road?: EdgeRoadInfo | null;
  /** Whether this edge is currently a legal build target. */
  buildable: boolean;
  onClick?: (edgeId: EdgeId) => void;
}

const HIT_TOLERANCE = 10;
const HIGHLIGHT_COLOR = 0xffd84d;
const HOVER_COLOR = 0xffffff;

export class EdgeSegmentView extends Container {
  readonly edgeId: EdgeId;
  readonly edgeKey: string;

  private p1: { x: number; y: number };
  private p2: { x: number; y: number };
  private buildable: boolean;
  private hovered = false;
  private onClickCb?: (edgeId: EdgeId) => void;

  private highlightGfx: Graphics;
  private roadGfx: Graphics;

  constructor(opts: EdgeSegmentOptions) {
    super();
    this.edgeId = opts.edgeId;
    this.edgeKey = opts.key;
    this.p1 = { x: opts.x1, y: opts.y1 };
    this.p2 = { x: opts.x2, y: opts.y2 };
    this.buildable = opts.buildable;
    this.onClickCb = opts.onClick;

    this.highlightGfx = new Graphics();
    this.roadGfx = new Graphics();
    this.addChild(this.highlightGfx);
    this.addChild(this.roadGfx);

    this.redrawHighlight();
    this.renderRoad(opts.road ?? null);
    this.applyInteractivity();
  }

  private applyInteractivity(): void {
    this.eventMode = this.buildable ? "static" : "none";
    this.cursor = this.buildable ? "pointer" : "default";
    this.hitArea = {
      contains: (px: number, py: number) =>
        distancePointToSegment(px, py, this.p1.x, this.p1.y, this.p2.x, this.p2.y) <= HIT_TOLERANCE,
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
      this.onClickCb(this.edgeId);
    }
  };

  private redrawHighlight(): void {
    this.highlightGfx.clear();
    if (this.buildable) {
      const color = this.hovered ? HOVER_COLOR : HIGHLIGHT_COLOR;
      this.highlightGfx
        .moveTo(this.p1.x, this.p1.y)
        .lineTo(this.p2.x, this.p2.y)
        .stroke({ width: 8, color, alpha: this.hovered ? 0.9 : 0.5, cap: "round" });
    } else if (this.hovered) {
      this.highlightGfx
        .moveTo(this.p1.x, this.p1.y)
        .lineTo(this.p2.x, this.p2.y)
        .stroke({ width: 6, color: 0xffffff, alpha: 0.2, cap: "round" });
    }
  }

  /** (Re)draw the road piece, or clear it if `road` is null. */
  renderRoad(road: EdgeRoadInfo | null): void {
    this.roadGfx.clear();
    if (!road) return;
    const color = playerColor(road.colorIndex);
    // Wider dark backing stroke first, for a crisper outline / subtle
    // depth against the board, then the colored road on top -- same
    // segment, same width as before, just an added pass underneath.
    this.roadGfx
      .moveTo(this.p1.x, this.p1.y)
      .lineTo(this.p2.x, this.p2.y)
      .stroke({ width: 9.5, color: 0x1a1a1a, alpha: 0.55, cap: "round" });
    this.roadGfx
      .moveTo(this.p1.x, this.p1.y)
      .lineTo(this.p2.x, this.p2.y)
      .stroke({ width: 7, color, cap: "round" });
  }

  setBuildable(buildable: boolean): void {
    if (this.buildable === buildable) return;
    this.buildable = buildable;
    this.applyInteractivity();
    this.redrawHighlight();
  }
}
