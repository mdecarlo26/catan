// Barrel for the PixiJS board renderer: BoardCanvas.tsx, HexTile.ts,
// VertexNode.ts, EdgeSegment.ts, boardInteraction.ts. This is also where
// the TypeScript mirror of backend/app/game/board.py's vertex/edge id
// derivation functions belongs -- see that module's docstring for the
// coordinate-system convention both sides must agree on independently.
export { BoardCanvas } from "./BoardCanvas";
export type { BoardCanvasProps } from "./BoardCanvas";
export { HexTileView } from "./HexTile";
export type { HexTileOptions } from "./HexTile";
export { VertexNodeView } from "./VertexNode";
export type { VertexNodeOptions, VertexBuildingInfo } from "./VertexNode";
export { EdgeSegmentView } from "./EdgeSegment";
export type { EdgeSegmentOptions, EdgeRoadInfo } from "./EdgeSegment";
export * from "./boardInteraction";
export * from "./hexMath";
export * from "./theme";
