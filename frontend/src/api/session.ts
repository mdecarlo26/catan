/**
 * localStorage-backed session persistence, per ARCHITECTURE.md's
 * "Room, Lobby & Reconnect" section: the client stores
 * `{room_code, player_id, token}` after JOIN_ROOM so a dropped/refreshed
 * tab can reconnect to `/ws/{room_code}?token=...` and resume its seat.
 *
 * `token` here is the plan's random `session_token` (not `player_id`) --
 * the server's `token -> player_id` map is what makes reconnect work.
 */

export interface StoredSession {
  room_code: string;
  player_id: string;
  token: string;
}

const STORAGE_KEY = "catan.session.v1";

function getStorage(): Storage | null {
  try {
    return window.localStorage;
  } catch {
    // localStorage can throw (privacy mode, disabled storage, SSR, etc).
    return null;
  }
}

/** Persists the active session so a refresh/reconnect can resume the seat. */
export function saveSession(session: StoredSession): void {
  const storage = getStorage();
  if (!storage) return;
  try {
    storage.setItem(STORAGE_KEY, JSON.stringify(session));
  } catch {
    // Ignore quota / serialization failures -- session persistence is
    // best-effort, not required for the current tab to keep working.
  }
}

/** Returns the stored session, or null if absent/corrupt/unavailable. */
export function loadSession(): StoredSession | null {
  const storage = getStorage();
  if (!storage) return null;
  const raw = storage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const parsed = JSON.parse(raw);
    if (
      parsed &&
      typeof parsed === "object" &&
      typeof parsed.room_code === "string" &&
      typeof parsed.player_id === "string" &&
      typeof parsed.token === "string"
    ) {
      return parsed as StoredSession;
    }
    return null;
  } catch {
    return null;
  }
}

/** Returns the stored session only if it matches the given room code. */
export function loadSessionForRoom(roomCode: string): StoredSession | null {
  const session = loadSession();
  if (!session) return null;
  return session.room_code.toUpperCase() === roomCode.toUpperCase() ? session : null;
}

/** Clears any stored session (e.g. on LEAVE_ROOM, kick, or game-over exit). */
export function clearSession(): void {
  const storage = getStorage();
  if (!storage) return;
  try {
    storage.removeItem(STORAGE_KEY);
  } catch {
    // Ignore.
  }
}
