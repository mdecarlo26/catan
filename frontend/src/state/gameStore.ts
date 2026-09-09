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
  ClientGameStateView,
  DiscardRequiredPayload,
  ErrorPayload,
  GameOverPayload,
  RoomStatePayload,
  ServerEvent,
  TradeOfferedPayload,
} from "../types/protocol";
import type { ConnectionStatus, ResyncNeededDetail, WsClient } from "../api/wsClient";
import { saveSession } from "../api/session";

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
        set({ log: withLog("Nuke dropped!") });
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
