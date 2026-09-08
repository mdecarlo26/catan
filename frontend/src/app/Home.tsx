import { useState } from "react";
import type { FormEvent } from "react";
import { useNavigate } from "react-router-dom";

/**
 * Landing screen: pick a nickname, then either create a new room or join
 * an existing one by code. The nickname is carried forward via router
 * state to CreateRoom / Lobby, which are responsible for actually
 * joining (REST create, or WS JOIN_ROOM).
 */
export default function Home() {
  const navigate = useNavigate();
  const [nickname, setNickname] = useState("");
  const [joinCode, setJoinCode] = useState("");
  const [joinError, setJoinError] = useState<string | null>(null);

  function handleCreate(event: FormEvent) {
    event.preventDefault();
    if (!nickname.trim()) {
      setJoinError("Enter a nickname first.");
      return;
    }
    navigate("/create", { state: { nickname: nickname.trim() } });
  }

  function handleJoin(event: FormEvent) {
    event.preventDefault();
    if (!nickname.trim()) {
      setJoinError("Enter a nickname first.");
      return;
    }
    if (!joinCode.trim()) {
      setJoinError("Enter a room code.");
      return;
    }
    setJoinError(null);
    navigate(`/room/${joinCode.trim().toUpperCase()}/lobby`, {
      state: { nickname: nickname.trim() },
    });
  }

  return (
    <div>
      <h1>Catan</h1>

      <label htmlFor="nickname">Nickname</label>
      <input
        id="nickname"
        value={nickname}
        onChange={(event) => setNickname(event.target.value)}
        placeholder="Your name"
        maxLength={24}
      />

      {joinError ? <p role="alert">{joinError}</p> : null}

      <form onSubmit={handleCreate}>
        <button type="submit">Create room</button>
      </form>

      <form onSubmit={handleJoin}>
        <label htmlFor="join-code">Room code</label>
        <input
          id="join-code"
          value={joinCode}
          onChange={(event) => setJoinCode(event.target.value)}
          placeholder="ABCD"
          maxLength={8}
        />
        <button type="submit">Join room</button>
      </form>
    </div>
  );
}
