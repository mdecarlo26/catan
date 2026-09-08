import { useEffect } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { wsClient } from "../api/wsClient";
import { loadSessionForRoom } from "../api/session";
import { bindGameStoreToWsClient, useGameStore } from "../state/gameStore";

interface LobbyNavState {
  nickname?: string;
}

/**
 * Lobby route: establishes the WS connection for this room (reusing a
 * stored reconnect token if present, otherwise joining fresh with the
 * nickname carried over from Home/CreateRoom), and shows the
 * host/players/settings summary from ROOM_STATE.
 *
 * The real settings form / player list / kick / start UI lives in
 * components/Lobby/ (a separate agent's scope) -- this route just wires
 * the connection and phase transition, with an inline placeholder for
 * where that UI mounts.
 */
export default function Lobby() {
  const { roomCode = "" } = useParams<{ roomCode: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const nickname = (location.state as LobbyNavState | null)?.nickname;

  const connectionStatus = useGameStore((state) => state.connectionStatus);
  const roomState = useGameStore((state) => state.roomState);
  const phase = useGameStore((state) => state.view?.phase);

  useEffect(() => {
    const unbind = bindGameStoreToWsClient(wsClient);

    const existingSession = loadSessionForRoom(roomCode);
    wsClient.connect(roomCode, existingSession?.token);
    if (!existingSession && nickname) {
      wsClient.send({ type: "JOIN_ROOM", payload: { nickname } });
    }

    return () => {
      unbind();
      wsClient.disconnect();
    };
    // Reconnect only when the room actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomCode]);

  useEffect(() => {
    if (phase && phase !== "lobby") {
      navigate(`/room/${roomCode}/game`);
    }
  }, [phase, roomCode, navigate]);

  return (
    <div>
      <h1>Lobby</h1>
      <p>Room code: {roomCode}</p>
      <p>Connection: {connectionStatus}</p>

      {roomState ? (
        <ul>
          {roomState.players.map((player) => (
            <li key={player.player_id}>
              {player.nickname}
              {player.is_host ? " (host)" : ""}
              {player.is_connected ? "" : " (disconnected)"}
            </li>
          ))}
        </ul>
      ) : (
        <p>Waiting for room state...</p>
      )}

      <div>Settings form / player list / start button goes here.</div>
    </div>
  );
}
