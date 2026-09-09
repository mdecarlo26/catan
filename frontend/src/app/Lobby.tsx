import { useEffect } from "react";
import { useLocation, useNavigate, useParams } from "react-router-dom";
import { wsClient } from "../api/wsClient";
import { loadSessionForRoom } from "../api/session";
import { bindGameStoreToWsClient, useGameStore } from "../state/gameStore";
import { PlayerList, SettingsForm, StartGameButton } from "../components/Lobby";
import { SETTINGS_REGISTRY_MOCK as SETTINGS_REGISTRY } from "../components/Lobby/Lobby.mock";
import type { GameSettings } from "../types/protocol";

interface LobbyNavState {
  nickname?: string;
}

/**
 * Lobby route: establishes the WS connection for this room (reusing a
 * stored reconnect token if present, otherwise joining fresh with the
 * nickname carried over from Home/CreateRoom), and renders the real
 * PlayerList / SettingsForm / StartGameButton wired to gameStore's live
 * ROOM_STATE + real UPDATE_SETTINGS / KICK_PLAYER / START_GAME
 * dispatches.
 *
 * The settings-metadata registry ({key,type,default,description,min/max})
 * is never sent over the wire (only current values are, via
 * RoomStatePayload.settings / SettingsUpdatedPayload.settings -- see
 * backend/app/game/settings_schema.py's SETTINGS_REGISTRY and
 * app.api.rooms/websocket, neither of which exposes the metadata list)
 * so SETTINGS_REGISTRY_MOCK (kept 1:1 in sync with the backend registry,
 * see Lobby.mock.ts's docstring) doubles as the real static registry
 * here; live *values* always come from roomState.settings.
 */
export default function Lobby() {
  const { roomCode = "" } = useParams<{ roomCode: string }>();
  const location = useLocation();
  const navigate = useNavigate();
  const nickname = (location.state as LobbyNavState | null)?.nickname;

  const connectionStatus = useGameStore((state) => state.connectionStatus);
  const roomState = useGameStore((state) => state.roomState);
  const phase = useGameStore((state) => state.view?.phase);
  const myPlayerId = useGameStore((state) => state.myPlayerId);
  const lastError = useGameStore((state) => state.lastError);

  useEffect(() => {
    const unbind = bindGameStoreToWsClient(wsClient);

    const existingSession = loadSessionForRoom(roomCode);
    if (existingSession) {
      useGameStore.getState().setMyPlayerId(existingSession.player_id);
    }
    if (wsClient.getStatus() === "disconnected") {
      wsClient.connect(roomCode, existingSession?.token);
      if (!existingSession && nickname) {
        wsClient.send({ type: "JOIN_ROOM", payload: { nickname } });
      }
    }

    // Deliberately does NOT disconnect the socket on unmount: navigating
    // from Lobby -> Game (on the LOBBY -> SETUP phase transition below)
    // must keep the same connection alive. The socket is only ever
    // explicitly torn down by GameOver's "Return home".
    return () => unbind();
    // Reconnect only when the room actually changes.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomCode]);

  useEffect(() => {
    if (phase && phase !== "lobby") {
      navigate(`/room/${roomCode}/game`);
    }
  }, [phase, roomCode, navigate]);

  const isHost = !!myPlayerId && roomState?.host_player_id === myPlayerId;
  const settingsValues: GameSettings | null = roomState?.settings ?? null;

  function handleSettingsChange(next: GameSettings) {
    if (!isHost) return;
    wsClient.send({ type: "UPDATE_SETTINGS", payload: { settings: next } });
  }

  function handleKick(playerId: string) {
    wsClient.send({ type: "KICK_PLAYER", payload: { target_player_id: playerId } });
  }

  function handleStart() {
    wsClient.send({ type: "START_GAME", payload: {} });
  }

  return (
    <div>
      <h1>Lobby</h1>
      <p>Room code: {roomCode}</p>
      <p>Connection: {connectionStatus}</p>
      {lastError ? (
        <p role="alert">
          Error: {lastError.message} ({lastError.code})
        </p>
      ) : null}

      {roomState ? (
        <>
          <PlayerList
            players={roomState.players}
            viewerPlayerId={myPlayerId ?? ""}
            isHost={isHost}
            onKick={isHost ? handleKick : undefined}
          />

          {settingsValues ? (
            <SettingsForm
              registry={SETTINGS_REGISTRY}
              values={settingsValues}
              onChange={handleSettingsChange}
              disabled={!isHost}
            />
          ) : null}

          <StartGameButton
            playerCount={roomState.players.length}
            isHost={isHost}
            onStart={handleStart}
          />
        </>
      ) : (
        <p>Waiting for room state...</p>
      )}
    </div>
  );
}
