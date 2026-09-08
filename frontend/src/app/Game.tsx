import { useEffect } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useGameStore } from "../state/gameStore";

/**
 * Main game route. The board canvas (board/) and HUD (components/Hud/)
 * are separate agents' scope -- this route just reads the mirrored
 * ClientGameStateView from gameStore and provides the layout slots they
 * mount into, plus the phase-driven redirect to GameOver.
 */
export default function Game() {
  const { roomCode = "" } = useParams<{ roomCode: string }>();
  const navigate = useNavigate();

  const view = useGameStore((state) => state.view);
  const connectionStatus = useGameStore((state) => state.connectionStatus);
  const resyncPending = useGameStore((state) => state.resyncPending);

  useEffect(() => {
    if (view?.phase === "game_over") {
      navigate(`/room/${roomCode}/game-over`);
    }
  }, [view?.phase, roomCode, navigate]);

  return (
    <div>
      <h1>Game</h1>
      <p>Room code: {roomCode}</p>
      <p>Connection: {connectionStatus}</p>
      {resyncPending ? <p role="status">Resyncing...</p> : null}
      {view ? <p>Phase: {view.phase}</p> : <p>Waiting for game state...</p>}

      <div>Board goes here</div>
      <div>HUD goes here</div>
    </div>
  );
}
