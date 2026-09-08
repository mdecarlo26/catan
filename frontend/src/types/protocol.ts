/**
 * Hand-mirrored TypeScript types for the Catan WebSocket protocol.
 *
 * This file MUST stay field-for-field in sync with the backend's pydantic
 * contract:
 *   - backend/app/game/board.py           (HexCoord, VertexId, EdgeId, Terrain, PortType, ...)
 *   - backend/app/game/players.py         (ResourceType, DevCardType)
 *   - backend/app/game/state.py           (Phase, PendingAction)
 *   - backend/app/game/actions.py         (ActionType, Client -> Server payloads)
 *   - backend/app/game/settings_schema.py (GameSettings, SETTINGS_REGISTRY)
 *   - backend/app/protocol/events.py      (EventType, Server -> Client payloads)
 *
 * Field names intentionally use snake_case throughout (matching the raw
 * JSON on the wire -- the backend pydantic models have no camelCase
 * alias config) rather than being renamed to camelCase, so the shapes
 * here can be compared directly against captured WS frames.
 *
 * Coordinate system (mirrors backend/app/game/board.py's module
 * docstring -- read that for the full explanation): hexes use axial
 * coordinates `[q, r]`. A vertex id is the ascending-sorted tuple of the
 * `HexCoord`s of the 2-3 hexes (that actually have tiles on this board)
 * meeting at that corner. An edge id is the ascending-sorted 2-tuple of
 * the `HexCoord`s of the two hex positions the edge separates. The
 * frontend board module (frontend/src/board/) derives these identically
 * to the backend so no float geometry ever needs to cross the wire or be
 * compared for equality.
 */

// ---------------------------------------------------------------------
// Board primitives (mirrors board.py)
// ---------------------------------------------------------------------

/** Axial hex coordinate, serialized as a 2-element JSON array `[q, r]`. */
export type HexCoord = readonly [q: number, r: number];

/**
 * Ascending-sorted tuple of the HexCoords of the hexes meeting at a
 * vertex. Length is always 2 or 3 -- see the module doc comment above.
 */
export type VertexId = readonly HexCoord[];

/** Ascending-sorted 2-tuple of the HexCoords an edge separates. */
export type EdgeId = readonly [HexCoord, HexCoord];

/** uuid4 string, issued by the server on JOIN_ROOM; stable across reconnects. */
export type PlayerId = string;

export type Terrain =
  | "forest"
  | "hills"
  | "mountains"
  | "fields"
  | "pasture"
  | "desert"
  | "sea";

export type PortType = "generic" | "brick" | "lumber" | "ore" | "grain" | "wool";

export type BuildingType = "settlement" | "city";

export interface HexTile {
  coord: HexCoord;
  terrain: Terrain;
  /** 2-12 (never 7); null for desert/sea tiles. */
  number_token: number | null;
}

export interface Port {
  port_type: PortType;
  vertices: readonly [VertexId, VertexId];
}

// ---------------------------------------------------------------------
// Player primitives (mirrors players.py)
// ---------------------------------------------------------------------

export type ResourceType = "brick" | "lumber" | "ore" | "grain" | "wool";

export type DevCardType =
  | "knight"
  | "road_building"
  | "year_of_plenty"
  | "monopoly"
  | "victory_point";

/** Resource type -> count held. */
export type ResourceHand = Partial<Record<ResourceType, number>>;

/** Dev card type -> count held. */
export type DevCardHand = Partial<Record<DevCardType, number>>;

// ---------------------------------------------------------------------
// Game state (mirrors state.py) -- these are the SERVER-INTERNAL shapes;
// the client only ever receives the masked ClientGameStateView below,
// reproduced here 1:1 with events.py for reference/typing convenience.
// ---------------------------------------------------------------------

export type Phase =
  | "lobby"
  | "setup"
  | "roll"
  | "robber_discard"
  | "robber_move"
  | "main"
  | "game_over";

export interface AwaitingDiscard {
  kind: "awaiting_discard";
  /** player_id -> number of cards that player still owes. */
  required_counts: Record<PlayerId, number>;
}

export interface AwaitingRobberPlacement {
  kind: "awaiting_robber_placement";
  actor: PlayerId;
  reason: "dice_roll" | "knight_card";
}

export interface AwaitingSteal {
  kind: "awaiting_steal";
  actor: PlayerId;
  candidate_targets: PlayerId[];
}

export interface AwaitingTradeResponse {
  kind: "awaiting_trade_response";
  trade_id: string;
  proposer: PlayerId;
  offered: ResourceHand;
  requested: ResourceHand;
  responses_pending: PlayerId[];
}

export type PendingAction =
  | AwaitingDiscard
  | AwaitingRobberPlacement
  | AwaitingSteal
  | AwaitingTradeResponse;

// ---------------------------------------------------------------------
// Settings (mirrors settings_schema.py)
// ---------------------------------------------------------------------

export interface GameSettings {
  /** 2-8. */
  player_count: number;
  /** Default 10. */
  victory_points_target: number;
  /** Named key into the board layout registry (e.g. "standard", "random", "fixed"). */
  board_layout: string;
  rush_mode: boolean;
  nuke_mode: boolean;
  /** null = "unset, derive from player_count >= 5"; a concrete host override otherwise. */
  special_build_phase: boolean | null;
  /** Default 7. */
  discard_limit: number;
}

export type SettingFieldType = "int" | "bool" | "string";

/** One entry of the generic settings-metadata registry the lobby form renders from. */
export interface SettingFieldMeta {
  /** Must exactly match a GameSettings key. */
  key: keyof GameSettings;
  type: SettingFieldType;
  default: unknown;
  description: string;
  min_value?: number | null;
  max_value?: number | null;
}

// ---------------------------------------------------------------------
// Client -> Server actions (mirrors actions.py)
// ---------------------------------------------------------------------

export type ActionType =
  | "JOIN_ROOM"
  | "LEAVE_ROOM"
  | "KICK_PLAYER"
  | "UPDATE_SETTINGS"
  | "START_GAME"
  | "ROLL_DICE"
  | "BUILD_SETTLEMENT"
  | "BUILD_ROAD"
  | "BUILD_CITY"
  | "BUY_DEV_CARD"
  | "PLAY_DEV_CARD"
  | "BANK_TRADE"
  | "PORT_TRADE"
  | "PROPOSE_TRADE"
  | "RESPOND_TRADE"
  | "MOVE_ROBBER"
  | "STEAL_RESOURCE"
  | "DISCARD_CARDS"
  | "PLAY_NUKE"
  | "END_TURN"
  | "CHAT_MESSAGE";

export interface EmptyPayload {}

export interface JoinRoomPayload {
  nickname: string;
}

export interface KickPlayerPayload {
  target_player_id: PlayerId;
}

export interface UpdateSettingsPayload {
  settings: GameSettings;
}

export interface BuildSettlementPayload {
  vertex_id: VertexId;
}

export interface BuildRoadPayload {
  edge_id: EdgeId;
}

export interface BuildCityPayload {
  vertex_id: VertexId;
}

export interface PlayDevCardPayload {
  card_type: DevCardType;
  /** Required (and only meaningful) when card_type === "monopoly". */
  monopoly_resource?: ResourceType | null;
  /** Required, exactly 2 entries, when card_type === "year_of_plenty". */
  year_of_plenty_resources?: ResourceType[] | null;
  /** Required, 1-2 entries, when card_type === "road_building". */
  road_building_edges?: EdgeId[] | null;
}

export interface BankTradePayload {
  offered: ResourceHand;
  requested: ResourceHand;
}

export interface PortTradePayload {
  offered: ResourceHand;
  requested: ResourceHand;
}

export interface ProposeTradePayload {
  offered: ResourceHand;
  requested: ResourceHand;
  /** null/omitted = open to all other players. */
  target_player_ids?: PlayerId[] | null;
}

export interface RespondTradePayload {
  trade_id: string;
  accept: boolean;
}

export interface MoveRobberPayload {
  hex: HexCoord;
}

export interface StealResourcePayload {
  target_player_id: PlayerId;
}

export interface DiscardCardsPayload {
  resources: ResourceHand;
}

export interface PlayNukePayload {
  target_player_id: PlayerId;
  target_vertex_id: VertexId;
  target_edge_id: EdgeId;
}

export interface ChatMessagePayload {
  text: string;
}

/** {type, payload} tagged union of every Client -> Server message. */
export type ClientAction =
  | { type: "JOIN_ROOM"; payload: JoinRoomPayload }
  | { type: "LEAVE_ROOM"; payload: EmptyPayload }
  | { type: "KICK_PLAYER"; payload: KickPlayerPayload }
  | { type: "UPDATE_SETTINGS"; payload: UpdateSettingsPayload }
  | { type: "START_GAME"; payload: EmptyPayload }
  | { type: "ROLL_DICE"; payload: EmptyPayload }
  | { type: "BUILD_SETTLEMENT"; payload: BuildSettlementPayload }
  | { type: "BUILD_ROAD"; payload: BuildRoadPayload }
  | { type: "BUILD_CITY"; payload: BuildCityPayload }
  | { type: "BUY_DEV_CARD"; payload: EmptyPayload }
  | { type: "PLAY_DEV_CARD"; payload: PlayDevCardPayload }
  | { type: "BANK_TRADE"; payload: BankTradePayload }
  | { type: "PORT_TRADE"; payload: PortTradePayload }
  | { type: "PROPOSE_TRADE"; payload: ProposeTradePayload }
  | { type: "RESPOND_TRADE"; payload: RespondTradePayload }
  | { type: "MOVE_ROBBER"; payload: MoveRobberPayload }
  | { type: "STEAL_RESOURCE"; payload: StealResourcePayload }
  | { type: "DISCARD_CARDS"; payload: DiscardCardsPayload }
  | { type: "PLAY_NUKE"; payload: PlayNukePayload }
  | { type: "END_TURN"; payload: EmptyPayload }
  | { type: "CHAT_MESSAGE"; payload: ChatMessagePayload };

// ---------------------------------------------------------------------
// Server -> Client events (mirrors protocol/events.py)
// ---------------------------------------------------------------------

export type EventType =
  | "ROOM_STATE"
  | "PLAYER_JOINED"
  | "PLAYER_LEFT"
  | "PLAYER_KICKED"
  | "PLAYER_DISCONNECTED"
  | "PLAYER_RECONNECTED"
  | "SETTINGS_UPDATED"
  | "GAME_STARTED"
  | "STATE_SNAPSHOT"
  | "DICE_ROLLED"
  | "RESOURCES_DISTRIBUTED"
  | "ROBBER_MOVED"
  | "RESOURCE_STOLEN"
  | "DISCARD_REQUIRED"
  | "TRADE_OFFERED"
  | "TRADE_RESOLVED"
  | "DEV_CARD_COUNT_CHANGED"
  | "NUKE_DROPPED"
  | "LONGEST_ROAD_CHANGED"
  | "LARGEST_ARMY_CHANGED"
  | "TURN_TIMER_EXPIRED"
  | "GAME_OVER"
  | "ERROR";

export interface PlayerSummary {
  player_id: PlayerId;
  nickname: string;
  seat: number;
  is_connected: boolean;
  is_host: boolean;
}

/**
 * Per-player info as seen by one specific recipient inside a
 * ClientGameStateView. `hand`/`dev_cards` are populated only in the
 * entry belonging to the viewer (see ClientGameStateView.viewer_player_id);
 * every other entry has them as `null` and relies on the always-accurate
 * `resource_card_count` / `dev_card_count` totals instead.
 */
export interface MaskedPlayerView {
  player_id: PlayerId;
  nickname: string;
  seat: number;
  is_connected: boolean;
  victory_points: number;
  knights_played: number;
  has_longest_road: boolean;
  has_largest_army: boolean;
  resource_card_count: number;
  dev_card_count: number;
  hand: ResourceHand | null;
  dev_cards: DevCardHand | null;
}

export interface WireVertexBuilding {
  vertex_id: VertexId;
  player_id: PlayerId;
  building_type: BuildingType;
}

export interface WireRoad {
  edge_id: EdgeId;
  player_id: PlayerId;
}

/**
 * JSON-safe flattening of the backend's internal Board model: hexes,
 * buildings and roads are lists of tagged records (each carrying its own
 * id field) rather than objects keyed by composite tuple ids, since
 * HexCoord/VertexId/EdgeId are not valid JSON object keys.
 */
export interface WireBoardView {
  hexes: HexTile[];
  ports: Port[];
  buildings: WireVertexBuilding[];
  roads: WireRoad[];
  robber_hex: HexCoord;
}

/**
 * The masked, per-recipient view of the server's GameState -- this is
 * the payload of STATE_SNAPSHOT and is what frontend/src/state/gameStore.ts
 * mirrors client-side.
 */
export interface ClientGameStateView {
  room_code: string;
  phase: Phase;
  settings: GameSettings;
  turn_order: PlayerId[];
  current_player_index: number;
  last_dice_roll: readonly [number, number] | null;
  board: WireBoardView;
  bank_resource_counts: ResourceHand;
  bank_dev_card_count: number;
  players: Record<PlayerId, MaskedPlayerView>;
  longest_road_holder: PlayerId | null;
  largest_army_holder: PlayerId | null;
  pending: PendingAction | null;
  viewer_player_id: PlayerId;
}

export interface RoomStatePayload {
  room_code: string;
  host_player_id: PlayerId;
  players: PlayerSummary[];
  settings: GameSettings;
  phase: Phase;
}

export interface PlayerJoinedPayload {
  player: PlayerSummary;
}

export interface PlayerLeftPayload {
  player_id: PlayerId;
}

export interface PlayerKickedPayload {
  player_id: PlayerId;
  reason: string | null;
}

export interface PlayerDisconnectedPayload {
  player_id: PlayerId;
}

export interface PlayerReconnectedPayload {
  player_id: PlayerId;
}

export interface SettingsUpdatedPayload {
  settings: GameSettings;
}

export interface GameStartedPayload {
  turn_order: PlayerId[];
}

export interface StateSnapshotPayload {
  state: ClientGameStateView;
}

export interface DiceRolledPayload {
  player_id: PlayerId;
  die1: number;
  die2: number;
  total: number;
}

export interface ResourcesDistributedPayload {
  distribution: Record<PlayerId, ResourceHand>;
}

export interface RobberMovedPayload {
  actor: PlayerId;
  hex: HexCoord;
}

export interface ResourceStolenPayload {
  actor: PlayerId;
  victim: PlayerId;
  /** Populated for actor/victim; null in the broadcast copy others receive. */
  resource: ResourceType | null;
}

export interface DiscardRequiredPayload {
  required_counts: Record<PlayerId, number>;
}

export interface TradeOfferedPayload {
  trade_id: string;
  proposer: PlayerId;
  offered: ResourceHand;
  requested: ResourceHand;
  target_player_ids: PlayerId[] | null;
}

export interface TradeResolvedPayload {
  trade_id: string;
  status: "accepted" | "declined" | "cancelled";
  accepted_by: PlayerId | null;
}

export interface DevCardCountChangedPayload {
  player_id: PlayerId;
  player_dev_card_count: number;
  bank_dev_card_count: number;
}

/** Exact shape per the plan: NUKE_DROPPED{actor, target, destroyed_vertex, destroyed_edge}. */
export interface NukeDroppedPayload {
  actor: PlayerId;
  target: PlayerId;
  destroyed_vertex: VertexId;
  destroyed_edge: EdgeId;
}

export interface LongestRoadChangedPayload {
  new_holder: PlayerId | null;
  previous_holder: PlayerId | null;
}

export interface LargestArmyChangedPayload {
  new_holder: PlayerId | null;
  previous_holder: PlayerId | null;
}

export interface TurnTimerExpiredPayload {
  player_id: PlayerId;
}

export interface GameOverPayload {
  winner: PlayerId;
  final_scores: Record<PlayerId, number>;
}

/** Exact shape per the plan: ERROR{code, message}. */
export interface ErrorPayload {
  code: string;
  message: string;
}

interface EventEnvelopeBase {
  seq: number;
  ts: number;
}

/** {type, payload, seq, ts} tagged union of every Server -> Client message. */
export type ServerEvent =
  | (EventEnvelopeBase & { type: "ROOM_STATE"; payload: RoomStatePayload })
  | (EventEnvelopeBase & { type: "PLAYER_JOINED"; payload: PlayerJoinedPayload })
  | (EventEnvelopeBase & { type: "PLAYER_LEFT"; payload: PlayerLeftPayload })
  | (EventEnvelopeBase & { type: "PLAYER_KICKED"; payload: PlayerKickedPayload })
  | (EventEnvelopeBase & {
      type: "PLAYER_DISCONNECTED";
      payload: PlayerDisconnectedPayload;
    })
  | (EventEnvelopeBase & {
      type: "PLAYER_RECONNECTED";
      payload: PlayerReconnectedPayload;
    })
  | (EventEnvelopeBase & { type: "SETTINGS_UPDATED"; payload: SettingsUpdatedPayload })
  | (EventEnvelopeBase & { type: "GAME_STARTED"; payload: GameStartedPayload })
  | (EventEnvelopeBase & { type: "STATE_SNAPSHOT"; payload: StateSnapshotPayload })
  | (EventEnvelopeBase & { type: "DICE_ROLLED"; payload: DiceRolledPayload })
  | (EventEnvelopeBase & {
      type: "RESOURCES_DISTRIBUTED";
      payload: ResourcesDistributedPayload;
    })
  | (EventEnvelopeBase & { type: "ROBBER_MOVED"; payload: RobberMovedPayload })
  | (EventEnvelopeBase & { type: "RESOURCE_STOLEN"; payload: ResourceStolenPayload })
  | (EventEnvelopeBase & { type: "DISCARD_REQUIRED"; payload: DiscardRequiredPayload })
  | (EventEnvelopeBase & { type: "TRADE_OFFERED"; payload: TradeOfferedPayload })
  | (EventEnvelopeBase & { type: "TRADE_RESOLVED"; payload: TradeResolvedPayload })
  | (EventEnvelopeBase & {
      type: "DEV_CARD_COUNT_CHANGED";
      payload: DevCardCountChangedPayload;
    })
  | (EventEnvelopeBase & { type: "NUKE_DROPPED"; payload: NukeDroppedPayload })
  | (EventEnvelopeBase & {
      type: "LONGEST_ROAD_CHANGED";
      payload: LongestRoadChangedPayload;
    })
  | (EventEnvelopeBase & {
      type: "LARGEST_ARMY_CHANGED";
      payload: LargestArmyChangedPayload;
    })
  | (EventEnvelopeBase & {
      type: "TURN_TIMER_EXPIRED";
      payload: TurnTimerExpiredPayload;
    })
  | (EventEnvelopeBase & { type: "GAME_OVER"; payload: GameOverPayload })
  | (EventEnvelopeBase & { type: "ERROR"; payload: ErrorPayload });
