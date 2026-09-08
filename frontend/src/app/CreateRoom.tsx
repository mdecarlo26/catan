import { useState } from "react";
import type { FormEvent } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { createRoom } from "../api/roomsApi";
import { saveSession } from "../api/session";

interface HomeNavState {
  nickname?: string;
}

/**
 * Creates a room via REST (POST /api/rooms), persists the returned
 * session, and hands off to the Lobby route. See roomsApi.ts for the
 * caveat on the exact response shape (not yet pinned down by the frozen
 * WS-only protocol.ts contract).
 */
export default function CreateRoom() {
  const navigate = useNavigate();
  const location = useLocation();
  const initialNickname = (location.state as HomeNavState | null)?.nickname ?? "";

  const [nickname, setNickname] = useState(initialNickname);
  const [status, setStatus] = useState<"idle" | "creating" | "error">("idle");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent) {
    event.preventDefault();
    if (!nickname.trim()) {
      setErrorMessage("Enter a nickname first.");
      return;
    }
    setStatus("creating");
    setErrorMessage(null);
    try {
      const session = await createRoom(nickname.trim());
      saveSession(session);
      navigate(`/room/${session.room_code}/lobby`, {
        state: { nickname: nickname.trim() },
      });
    } catch (err) {
      setStatus("error");
      setErrorMessage(err instanceof Error ? err.message : "Failed to create room.");
    }
  }

  return (
    <div>
      <h1>Create a room</h1>

      <form onSubmit={handleSubmit}>
        <label htmlFor="host-nickname">Nickname</label>
        <input
          id="host-nickname"
          value={nickname}
          onChange={(event) => setNickname(event.target.value)}
          maxLength={24}
        />

        <button type="submit" disabled={status === "creating"}>
          {status === "creating" ? "Creating..." : "Create room"}
        </button>
      </form>

      {errorMessage ? <p role="alert">{errorMessage}</p> : null}
    </div>
  );
}
