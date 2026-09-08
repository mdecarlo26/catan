/**
 * WebSocket wrapper around the Catan realtime protocol
 * (see ARCHITECTURE.md's "WebSocket Protocol" section and
 * frontend/src/types/protocol.ts, which this is built strictly against).
 *
 * Responsibilities:
 *  - connect(roomCode, token) to `/ws/{room_code}?token=...`.
 *  - automatic reconnect with exponential backoff on any *unexpected*
 *    socket close (i.e. not one requested via disconnect()).
 *  - a `seq` gap detector: each incoming envelope carries a monotonic
 *    `seq`; if the next envelope's seq skips ahead of what we last saw,
 *    a synthetic "resync-needed" event is emitted so gameStore.ts can
 *    request a fresh STATE_SNAPSHOT (per the plan: "on a detected gap
 *    the client requests a full STATE_SNAPSHOT rather than replaying
 *    deltas").
 *  - a typed `send(action: ClientAction)` / `on(eventType, handler)` API.
 *
 * NOTE: the frozen protocol.ts ActionType enum has no dedicated
 * "request a resync" action yet -- that wiring detail is left for the
 * Wave 2 "Frontend-Backend Wiring" agent to reconcile against whatever
 * the real backend integration settles on (e.g. a REQUEST_SNAPSHOT
 * action, or simply relying on the reconnect handshake to push a fresh
 * STATE_SNAPSHOT). In the meantime, `forceResync()` re-opens the socket
 * immediately (bypassing backoff), which is expected to trigger the
 * server's normal reconnect flow and a fresh state push.
 */

import type { ClientAction, ServerEvent } from "../types/protocol";

export type ConnectionStatus =
  | "disconnected"
  | "connecting"
  | "connected"
  | "reconnecting";

export interface ResyncNeededDetail {
  /** The seq we expected next (last seen + 1). */
  expectedSeq: number;
  /** The seq that actually arrived, ahead of expectedSeq. */
  receivedSeq: number;
}

export interface StatusChangeDetail {
  status: ConnectionStatus;
}

export interface CloseDetail {
  code: number;
  reason: string;
  wasClean: boolean;
}

/** Maps every subscribable event name to its handler's parameter list. */
type WsClientEventMap = {
  [K in ServerEvent["type"]]: (
    payload: Extract<ServerEvent, { type: K }>["payload"],
    envelope: Extract<ServerEvent, { type: K }>
  ) => void;
} & {
  /** Emitted when a seq gap is detected; store should request a resync. */
  "resync-needed": (detail: ResyncNeededDetail) => void;
  /** Emitted whenever the connection status changes. */
  "status-change": (detail: StatusChangeDetail) => void;
  /** Raw socket lifecycle events, mostly useful for debugging/logging. */
  open: () => void;
  close: (detail: CloseDetail) => void;
  error: (err: unknown) => void;
};

export type WsClientEventType = keyof WsClientEventMap;

const BASE_RECONNECT_DELAY_MS = 500;
const MAX_RECONNECT_DELAY_MS = 15_000;
const RECONNECT_JITTER_MS = 250;

function defaultWsBaseUrl(): string {
  const fromEnv = (import.meta as ImportMeta & { env?: Record<string, string | undefined> })
    .env?.VITE_WS_URL;
  return fromEnv ?? "ws://localhost:8000";
}

export class WsClient {
  private readonly wsBaseUrl: string;
  private socket: WebSocket | null = null;
  private status: ConnectionStatus = "disconnected";
  private roomCode: string | null = null;
  private token: string | null = null;
  private lastSeq: number | null = null;
  private reconnectAttempts = 0;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  /** True once disconnect() has been called; suppresses auto-reconnect. */
  private manualClose = false;

  // Stored loosely (rather than as the precise mapped-type shape) because
  // TypeScript can't verify a generic `K`-indexed read/write against a
  // mapped type at the storage site; the public on()/off()/emit() API
  // surface is what actually guarantees per-event handler typing.
  private listeners: Partial<Record<WsClientEventType, Set<(...args: never[]) => void>>> = {};

  constructor(wsBaseUrl: string = defaultWsBaseUrl()) {
    this.wsBaseUrl = wsBaseUrl;
  }

  /** Opens (or re-opens) a connection to a room, optionally with a reconnect token. */
  connect(roomCode: string, token?: string | null): void {
    this.roomCode = roomCode;
    this.token = token ?? null;
    this.manualClose = false;
    this.lastSeq = null;
    this.reconnectAttempts = 0;
    this.clearReconnectTimer();
    this.openSocket();
  }

  /** Updates the reconnect token in place (e.g. once JOIN_ROOM assigns one), without reconnecting. */
  setToken(token: string): void {
    this.token = token;
  }

  /** Closes the socket and stops any pending auto-reconnect. */
  disconnect(): void {
    this.manualClose = true;
    this.clearReconnectTimer();
    if (this.socket) {
      try {
        this.socket.close(1000, "client disconnect");
      } catch {
        // Socket may already be closed/closing; ignore.
      }
      this.socket = null;
    }
    this.setStatus("disconnected");
  }

  /**
   * Forces an immediate reconnect (bypassing backoff), e.g. in response
   * to "resync-needed", so the server's reconnect handshake can push a
   * fresh STATE_SNAPSHOT.
   */
  forceResync(): void {
    if (!this.roomCode) return;
    this.clearReconnectTimer();
    if (this.socket) {
      try {
        this.socket.close();
      } catch {
        // Ignore.
      }
      this.socket = null;
    }
    this.openSocket();
  }

  getStatus(): ConnectionStatus {
    return this.status;
  }

  /** Sends a typed client action. No-ops (with a console warning) if not connected. */
  send(action: ClientAction): void {
    if (!this.socket || this.socket.readyState !== WebSocket.OPEN) {
      // eslint-disable-next-line no-console
      console.warn(`[wsClient] dropped ${action.type}: socket not open`);
      return;
    }
    this.socket.send(JSON.stringify(action));
  }

  /** Subscribes to a protocol EventType or a wsClient lifecycle event. Returns an unsubscribe fn. */
  on<K extends WsClientEventType>(type: K, handler: WsClientEventMap[K]): () => void {
    let set = this.listeners[type];
    if (!set) {
      set = new Set();
      this.listeners[type] = set;
    }
    set.add(handler as (...args: never[]) => void);
    return () => this.off(type, handler);
  }

  off<K extends WsClientEventType>(type: K, handler: WsClientEventMap[K]): void {
    this.listeners[type]?.delete(handler as (...args: never[]) => void);
  }

  // -------------------------------------------------------------------
  // Internals
  // -------------------------------------------------------------------

  private openSocket(): void {
    if (!this.roomCode) return;
    this.setStatus(this.reconnectAttempts > 0 ? "reconnecting" : "connecting");

    const trimmedBase = this.wsBaseUrl.replace(/\/+$/, "");
    const path = `${trimmedBase}/ws/${encodeURIComponent(this.roomCode)}`;
    const url = this.token ? `${path}?token=${encodeURIComponent(this.token)}` : path;

    let socket: WebSocket;
    try {
      socket = new WebSocket(url);
    } catch (err) {
      this.emit("error", err);
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;

    socket.addEventListener("open", () => {
      this.reconnectAttempts = 0;
      this.setStatus("connected");
      this.emit("open");
    });
    socket.addEventListener("message", (event: MessageEvent) => this.handleMessage(event));
    socket.addEventListener("close", (event: CloseEvent) => this.handleClose(event));
    socket.addEventListener("error", (event: Event) => this.emit("error", event));
  }

  private handleMessage(event: MessageEvent): void {
    let envelope: ServerEvent;
    try {
      envelope = JSON.parse(event.data as string) as ServerEvent;
    } catch (err) {
      this.emit("error", err);
      return;
    }

    if (typeof envelope.seq === "number") {
      if (this.lastSeq !== null && envelope.seq > this.lastSeq + 1) {
        this.emit("resync-needed", {
          expectedSeq: this.lastSeq + 1,
          receivedSeq: envelope.seq,
        });
      }
      this.lastSeq = envelope.seq;
    }

    // Dynamic dispatch across the discriminated union: each branch's
    // payload type lines up with its `type` at runtime; the public `on()`
    // API keeps that guarantee for callers, so a loose cast here is safe.
    this.emit(
      envelope.type as WsClientEventType,
      envelope.payload as never,
      envelope as never
    );
  }

  private handleClose(event: CloseEvent): void {
    this.socket = null;
    this.emit("close", {
      code: event.code,
      reason: event.reason,
      wasClean: event.wasClean,
    });

    if (this.manualClose) {
      this.setStatus("disconnected");
      return;
    }
    this.setStatus("reconnecting");
    this.scheduleReconnect();
  }

  private scheduleReconnect(): void {
    if (this.reconnectTimer || this.manualClose || !this.roomCode) return;
    const attempt = this.reconnectAttempts;
    const delay =
      Math.min(BASE_RECONNECT_DELAY_MS * 2 ** attempt, MAX_RECONNECT_DELAY_MS) +
      Math.random() * RECONNECT_JITTER_MS;
    this.reconnectAttempts += 1;
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.manualClose || !this.roomCode) return;
      this.openSocket();
    }, delay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
    }
  }

  private setStatus(status: ConnectionStatus): void {
    if (this.status === status) return;
    this.status = status;
    this.emit("status-change", { status });
  }

  private emit<K extends WsClientEventType>(
    type: K,
    ...args: Parameters<WsClientEventMap[K]>
  ): void {
    const handlers = this.listeners[type];
    if (!handlers) return;
    for (const handler of handlers) {
      (handler as (...a: unknown[]) => void)(...args);
    }
  }
}

/** Shared singleton client used by the app; construct your own for tests. */
export const wsClient = new WsClient();
