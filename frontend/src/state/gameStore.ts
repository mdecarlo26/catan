/**
 * Client-mirrored game state, per ARCHITECTURE.md's "Repo Structure"
 * (`state/gameStore.ts -- client-mirrored GameState (zustand)`).
 *
 * `view` mirrors ClientGameStateView (the masked per-recipient snapshot
 * from protocol.ts) and is wholesale-replaced on every STATE_SNAPSHOT --
 * per the plan, the server sends full masked-state snapshots per action
 * rather than diffs, so STATE_SNAPSHOT is the single source of truth for
 * `view`. The other ServerEvent types are mostly transient
 * notifications (animation triggers, toasts, turn-log lines) that arrive
 * around the same time as their corresponding snapshot; a few also patch
 * small, obviously-safe fields on `view` incrementally (e.g. flipping
 * `is_connected`) for snappier UI, but nothing here should be treated as
 * an alternative to the next STATE_SNAPSHOT.
 *
 * `roomState` mirrors RoomStatePayload, the lobby-only summary sent
 * before a game exists (host/players/settings/phase pre-SETUP).
 */

import { create } from "zustand";
import type {
  BlackjackRoundResolvedPayload,
  BuildingType,
  ClientGameStateView,
  DiscardRequiredPayload,
  EdgeId,
  ErrorPayload,
  GameOverPayload,
  PlayerId,
  ResourceHand,
  ResourceType,
  RoomStatePayload,
  ServerEvent,
  TradeOfferedPayload,
  VertexId,
} from "../types/protocol";
import type { ConnectionStatus, ResyncNeededDetail, WsClient } from "../api/wsClient";
import { saveSession } from "../api/session";
import { vertexIdKey } from "../board/hexMath";

/** The 5 resource types nuke mode's precondition checks, mirroring
 * backend/app/game/rules/nuke_mode.py's `ResourceType` iteration. */
const NUKE_RESOURCE_TYPES: readonly ResourceType[] = ["brick", "lumber", "ore", "grain", "wool"];
/** Mirrors nuke_mode.py's `RESOURCE_COST_PER_TYPE`. */
const NUKE_RESOURCE_COST_PER_TYPE = 2;

/**
 * True if `hand` alone satisfies nuke mode's precondition (>=2 of each of
 * the 5 resource types, 10 cards total) -- mirrors
 * backend/app/game/rules/nuke_mode.py's `player_has_nuke_hand` exactly, as
 * a UI-only convenience for gating the "Drop Nuke" trigger. The backend
 * (`rules_engine._validate_play_nuke`) remains the sole authority; a
 * mismatch here just means the button is mis-enabled for a moment, never
 * a state corruption.
 */
export function isNukeEligibleHand(hand: ResourceHand | null | undefined): boolean {
  if (!hand) return false;
  return NUKE_RESOURCE_TYPES.every((type) => (hand[type] ?? 0) >= NUKE_RESOURCE_COST_PER_TYPE);
}

/** A NUKE_DROPPED event, retained for one-shot toast/animation triggers
 * (see Game.tsx). `seq` is the envelope's monotonic sequence number, used
 * by consumers to detect "this is a new event" even if actor/target repeat. */
export interface NukeEventRecord {
  seq: number;
  actor: PlayerId;
  target: PlayerId;
  destroyed_vertex: VertexId;
  destroyed_edge: EdgeId;
  /**
   * Settlement vs. city, looked up from the pre-nuke `view` at the moment
   * this event was processed -- NUKE_DROPPED is always broadcast before
   * the resulting STATE_SNAPSHOT (see backend/app/api/websocket.py's
   * module docstring), so `get().view` here still reflects the board as
   * it stood immediately before the piece was destroyed. `null` if
   * unavailable (e.g. a resync raced this event away).
   */
  destroyedBuildingType: BuildingType | null;
}

/** A DICE_ROLLED event, retained purely to give the HUD's dice display a
 * distinct trigger to animate on (die values alone can repeat between
 * rolls, so the `seq` is what actually signals "a new roll happened"). */
export interface DiceRollEventRecord {
  seq: number;
  die1: number;
  die2: number;
}

/** A BLACKJACK_ROUND_RESOLVED event, retained for a one-shot
 * round-resolution toast trigger (see Game.tsx / BlackjackToast), mirroring
 * NukeEventRecord's shape. */
export interface BlackjackResolvedEventRecord {
  seq: number;
  payload: BlackjackRoundResolvedPayload;
}

const MAX_LOG_ENTRIES = 100;

export interface LogEntry {
  id: number;
  ts: number;
  text: string;
}

export interface GameStoreState {
  connectionStatus: ConnectionStatus;
  /** Set whenever wsClient detects a seq gap; cleared once a fresh snapshot lands. */
  resyncPending: ResyncNeededDetail | null;

  /**
   * This connection's own player_id, known as soon as either a stored
   * session is loaded (host / reconnect) or SESSION_ESTABLISHED arrives
   * (a freshly-joined non-host player). Prefer `view.viewer_player_id`
   * once a game exists -- this exists for the lobby-phase window before
   * any ClientGameStateView is available.
   */
  myPlayerId: string | null;

  /** Lobby-phase room summary (players/settings/host), from ROOM_STATE. */
  roomState: RoomStatePayload | null;
  /** Authoritative masked game state, from STATE_SNAPSHOT. */
  view: ClientGameStateView | null;

  /** Outstanding discard requirement, from DISCARD_REQUIRED (cleared by the next snapshot). */
  discardRequired: DiscardRequiredPayload | null;
  /** Open trade offers this client has seen, keyed by trade_id. */
  tradeOffers: Record<string, TradeOfferedPayload>;
  /** Final result, from GAME_OVER. */
  gameOver: GameOverPayload | null;
  /** Most recent ERROR event from the server, if any. */
  lastError: ErrorPayload | null;

  /** Most recent NUKE_DROPPED event, for one-shot toast/animation triggers. */
  nukeEvent: NukeEventRecord | null;
  /** Most recent DICE_ROLLED event, for one-shot dice-tumble animation triggers. */
  diceRollEvent: DiceRollEventRecord | null;
  /** Most recent BLACKJACK_ROUND_RESOLVED event, for a one-shot resolution toast. */
  blackjackResolvedEvent: BlackjackResolvedEventRecord | null;

  /** Rolling human-readable turn/event log, newest last. */
  log: LogEntry[];

  setConnectionStatus: (status: ConnectionStatus) => void;
  setResyncPending: (detail: ResyncNeededDetail | null) => void;
  setMyPlayerId: (playerId: string | null) => void;
  applyServerEvent: (event: ServerEvent) => void;
  reset: () => void;
}

let logIdCounter = 0;

function initialState(): Pick<
  GameStoreState,
  | "connectionStatus"
  | "resyncPending"
  | "myPlayerId"
  | "roomState"
  | "view"
  | "discardRequired"
  | "tradeOffers"
  | "gameOver"
  | "lastError"
  | "nukeEvent"
  | "diceRollEvent"
  | "blackjackResolvedEvent"
  | "log"
> {
  return {
    connectionStatus: "disconnected",
    resyncPending: null,
    myPlayerId: null,
    roomState: null,
    view: null,
    discardRequired: null,
    tradeOffers: {},
    gameOver: null,
    lastError: null,
    nukeEvent: null,
    diceRollEvent: null,
    blackjackResolvedEvent: null,
    log: [],
  };
}

function appendLog(log: LogEntry[], text: string, ts: number): LogEntry[] {
  const next = [...log, { id: logIdCounter++, ts, text }];
  return next.length > MAX_LOG_ENTRIES ? next.slice(next.length - MAX_LOG_ENTRIES) : next;
}

export const useGameStore = create<GameStoreState>((set, get) => ({
  ...initialState(),

  setConnectionStatus: (status) => set({ connectionStatus: status }),

  setResyncPending: (detail) => set({ resyncPending: detail }),

  setMyPlayerId: (playerId) => set({ myPlayerId: playerId }),

  reset: () => set(initialState()),

  applyServerEvent: (event) => {
    const { log } = get();
    const withLog = (text: string) => appendLog(log, text, event.ts);

    switch (event.type) {
      case "SESSION_ESTABLISHED": {
        saveSession({
          room_code: event.payload.room_code,
          player_id: event.payload.player_id,
          token: event.payload.token,
        });
        set({ myPlayerId: event.payload.player_id, log: withLog("Session established.") });
        break;
      }

      case "ROOM_STATE": {
        set({ roomState: event.payload, log: withLog("Room state updated.") });
        break;
      }

      case "PLAYER_JOINED": {
        set({ log: withLog(`${event.payload.player.nickname} joined the room.`) });
        break;
      }

      case "PLAYER_LEFT": {
        set({ log: withLog("A player left the room.") });
        break;
      }

      case "PLAYER_KICKED": {
        set({
          log: withLog(
            event.payload.reason
              ? `A player was kicked (${event.payload.reason}).`
              : "A player was kicked."
          ),
        });
        break;
      }

      case "PLAYER_DISCONNECTED": {
        const { view } = get();
        const patchedView = patchPlayerConnection(view, event.payload.player_id, false);
        set({ view: patchedView, log: withLog("A player disconnected.") });
        break;
      }

      case "PLAYER_RECONNECTED": {
        const { view } = get();
        const patchedView = patchPlayerConnection(view, event.payload.player_id, true);
        set({ view: patchedView, log: withLog("A player reconnected.") });
        break;
      }

      case "SETTINGS_UPDATED": {
        const { roomState, view } = get();
        set({
          roomState: roomState ? { ...roomState, settings: event.payload.settings } : roomState,
          view: view ? { ...view, settings: event.payload.settings } : view,
          log: withLog("Settings updated."),
        });
        break;
      }

      case "GAME_STARTED": {
        set({ log: withLog("The game has started.") });
        break;
      }

      case "STATE_SNAPSHOT": {
        set({
          view: event.payload.state,
          resyncPending: null,
          discardRequired: null,
          log: withLog("State synced."),
        });
        break;
      }

      case "DICE_ROLLED": {
        const { view } = get();
        set({
          view: view
            ? { ...view, last_dice_roll: [event.payload.die1, event.payload.die2] }
            : view,
          diceRollEvent: { seq: event.seq, die1: event.payload.die1, die2: event.payload.die2 },
          log: withLog(`Dice rolled: ${event.payload.total} (${event.payload.die1}+${event.payload.die2}).`),
        });
        break;
      }

      case "RESOURCES_DISTRIBUTED": {
        set({ log: withLog("Resources distributed.") });
        break;
      }

      case "ROBBER_MOVED": {
        const { view } = get();
        set({
          view: view
            ? { ...view, board: { ...view.board, robber_hex: event.payload.hex } }
            : view,
          log: withLog("The robber was moved."),
        });
        break;
      }

      case "RESOURCE_STOLEN": {
        set({ log: withLog("A resource was stolen.") });
        break;
      }

      case "DISCARD_REQUIRED": {
        set({ discardRequired: event.payload, log: withLog("Players must discard.") });
        break;
      }

      case "TRADE_OFFERED": {
        const { tradeOffers } = get();
        set({
          tradeOffers: { ...tradeOffers, [event.payload.trade_id]: event.payload },
          log: withLog("A trade was offered."),
        });
        break;
      }

      case "TRADE_RESOLVED": {
        const { tradeOffers } = get();
        const nextOffers = { ...tradeOffers };
        delete nextOffers[event.payload.trade_id];
        set({ tradeOffers: nextOffers, log: withLog(`Trade ${event.payload.status}.`) });
        break;
      }

      case "DEV_CARD_COUNT_CHANGED": {
        set({ log: withLog("Dev card counts changed.") });
        break;
      }

      case "NUKE_DROPPED": {
        // NUKE_DROPPED is always broadcast before the resulting
        // STATE_SNAPSHOT (see backend/app/api/websocket.py's module
        // docstring), so `view` here still reflects the board as it stood
        // immediately before the piece was destroyed -- this is the only
        // moment we can still look up whether it was a settlement or city.
        const { view } = get();
        const building = view?.board.buildings.find(
          (b) => vertexIdKey(b.vertex_id) === vertexIdKey(event.payload.destroyed_vertex)
        );
        set({
          nukeEvent: {
            seq: event.seq,
            actor: event.payload.actor,
            target: event.payload.target,
            destroyed_vertex: event.payload.destroyed_vertex,
            destroyed_edge: event.payload.destroyed_edge,
            destroyedBuildingType: building?.building_type ?? null,
          },
          log: withLog("Nuke dropped!"),
        });
        break;
      }

      case "BLACKJACK_ROUND_STARTED": {
        set({ log: withLog("A blackjack round has opened for betting.") });
        break;
      }

      case "BLACKJACK_BET_PLACED": {
        set({ log: withLog("A blackjack bet was placed.") });
        break;
      }

      case "BLACKJACK_BET_DECLINED": {
        set({ log: withLog("A player declined the blackjack round.") });
        break;
      }

      case "BLACKJACK_HAND_UPDATED": {
        set({
          log: withLog(
            event.payload.status === "busted"
              ? "A blackjack hand busted."
              : event.payload.status === "stood"
                ? "A blackjack hand stood."
                : "A blackjack hand hit."
          ),
        });
        break;
      }

      case "BLACKJACK_DEALER_REVEALED": {
        set({
          log: withLog(
            event.payload.dealer_busted
              ? `Dealer busted with ${event.payload.dealer_total}.`
              : `Dealer revealed ${event.payload.dealer_total}.`
          ),
        });
        break;
      }

      case "BLACKJACK_ROUND_RESOLVED": {
        set({
          blackjackResolvedEvent: { seq: event.seq, payload: event.payload },
          log: withLog("Blackjack round resolved."),
        });
        break;
      }

      case "LONGEST_ROAD_CHANGED": {
        set({ log: withLog("Longest road holder changed.") });
        break;
      }

      case "LARGEST_ARMY_CHANGED": {
        set({ log: withLog("Largest army holder changed.") });
        break;
      }

      case "TURN_TIMER_EXPIRED": {
        set({ log: withLog("A turn timer expired.") });
        break;
      }

      case "GAME_OVER": {
        set({ gameOver: event.payload, log: withLog("Game over.") });
        break;
      }

      case "ERROR": {
        set({
          lastError: event.payload,
          log: withLog(`Error: ${event.payload.message}`),
        });
        break;
      }

      default: {
        // Exhaustiveness guard: if protocol.ts grows a new EventType
        // without a case above, this line will fail to compile.
        const _exhaustive: never = event;
        void _exhaustive;
      }
    }
  },
}));

const SERVER_EVENT_TYPES: ServerEvent["type"][] = [
  "SESSION_ESTABLISHED",
  "ROOM_STATE",
  "PLAYER_JOINED",
  "PLAYER_LEFT",
  "PLAYER_KICKED",
  "PLAYER_DISCONNECTED",
  "PLAYER_RECONNECTED",
  "SETTINGS_UPDATED",
  "GAME_STARTED",
  "STATE_SNAPSHOT",
  "DICE_ROLLED",
  "RESOURCES_DISTRIBUTED",
  "ROBBER_MOVED",
  "RESOURCE_STOLEN",
  "DISCARD_REQUIRED",
  "TRADE_OFFERED",
  "TRADE_RESOLVED",
  "DEV_CARD_COUNT_CHANGED",
  "NUKE_DROPPED",
  "BLACKJACK_ROUND_STARTED",
  "BLACKJACK_BET_PLACED",
  "BLACKJACK_BET_DECLINED",
  "BLACKJACK_HAND_UPDATED",
  "BLACKJACK_DEALER_REVEALED",
  "BLACKJACK_ROUND_RESOLVED",
  "LONGEST_ROAD_CHANGED",
  "LARGEST_ARMY_CHANGED",
  "TURN_TIMER_EXPIRED",
  "GAME_OVER",
  "ERROR",
];

/**
 * Wires a WsClient's events into this store: every ServerEvent goes
 * through applyServerEvent, connection status changes are mirrored, and
 * a detected seq gap both records `resyncPending` and asks the client to
 * force a resync (see wsClient.ts's forceResync doc comment for the
 * caveat about the still-TBD dedicated resync action).
 *
 * Call once per WsClient instance (e.g. when a route establishing the
 * connection mounts) and call the returned cleanup function on unmount.
 */
export function bindGameStoreToWsClient(client: WsClient): () => void {
  // Cast to a single non-generic overload for this bulk subscription: each
  // ServerEvent's payload/envelope pairing is only actually used via the
  // fully-typed `envelope`, so the loosened `payload: unknown` here is safe.
  const genericOn = client.on.bind(client) as (
    type: ServerEvent["type"],
    handler: (payload: unknown, envelope: ServerEvent) => void
  ) => () => void;

  const unsubscribers = SERVER_EVENT_TYPES.map((type) =>
    genericOn(type, (_payload, envelope) => {
      useGameStore.getState().applyServerEvent(envelope);
    })
  );

  unsubscribers.push(
    client.on("status-change", ({ status }) => {
      useGameStore.getState().setConnectionStatus(status);
    })
  );

  unsubscribers.push(
    client.on("resync-needed", (detail) => {
      useGameStore.getState().setResyncPending(detail);
      client.forceResync();
    })
  );

  return () => unsubscribers.forEach((unsubscribe) => unsubscribe());
}

function patchPlayerConnection(
  view: ClientGameStateView | null,
  playerId: string,
  isConnected: boolean
): ClientGameStateView | null {
  if (!view || !view.players[playerId]) return view;
  return {
    ...view,
    players: {
      ...view.players,
      [playerId]: { ...view.players[playerId], is_connected: isConnected },
    },
  };
}
