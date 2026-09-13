/**
 * Thin REST client for room creation, per ARCHITECTURE.md's "Repo
 * Structure" (`POST /api/rooms -> create room, returns code + host
 * token`) and "Room, Lobby & Reconnect" sections.
 *
 * The exact response shape is a backend implementation detail not yet
 * pinned down by the frozen frontend/src/types/protocol.ts contract
 * (that file only covers the WS protocol). This client assumes the
 * response is directly usable as a StoredSession (room_code, player_id,
 * token) so CreateRoom.tsx can persist it via session.ts immediately.
 * The Wave 2 "Frontend-Backend Wiring" agent should reconcile this
 * against whatever backend/app/api/rooms.py actually returns.
 */

import type { GameSettings } from "../types/protocol";
import type { StoredSession } from "./session";

function defaultApiBaseUrl(): string {
  const env = (import.meta as ImportMeta & { env?: Record<string, string | undefined> }).env;
  // VITE_API_URL is the full REST base, already including the /api
  // path segment (e.g. "/api" in prod, "http://localhost:8000/api" in
  // dev) -- callers append only the endpoint-specific suffix on top of
  // this, never "/api" again.
  if (env?.VITE_API_URL) return env.VITE_API_URL;
  // Fall back to deriving an http(s) origin from VITE_WS_URL so a single
  // env var is enough for local dev (ws://host:port/ws -> http://host:port/api).
  const wsUrl = env?.VITE_WS_URL ?? "ws://localhost:8000/ws";
  return wsUrl.replace(/^ws/, "http").replace(/\/ws\/?$/, "/api");
}

export interface CreateRoomRequest {
  nickname: string;
  settings?: Partial<GameSettings>;
}

export type CreateRoomResponse = StoredSession;

export async function createRoom(
  nickname: string,
  settings?: Partial<GameSettings>
): Promise<CreateRoomResponse> {
  const base = defaultApiBaseUrl().replace(/\/+$/, "");
  const response = await fetch(`${base}/rooms`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ nickname, settings } satisfies CreateRoomRequest),
  });

  if (!response.ok) {
    let detail = response.statusText;
    try {
      const body = await response.json();
      if (body && typeof body.message === "string") detail = body.message;
    } catch {
      // Response body wasn't JSON; keep statusText.
    }
    throw new Error(`Failed to create room (${response.status}): ${detail}`);
  }

  return (await response.json()) as CreateRoomResponse;
}
