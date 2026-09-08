import { useNavigate, useParams } from "react-router-dom";
import { clearSession } from "../api/session";
import { wsClient } from "../api/wsClient";
import { useGameStore } from "../state/gameStore";

/**
 * Post-game summary. Per ARCHITECTURE.md, the room lingers briefly after
 * GAME_OVER before the TTL sweep evicts it -- "Return home" clears the
 * local session and disconnects rather than trying to keep the room
 * alive client-side.
 */
export default function GameOver() {
  const { roomCode = "" } = useParams<{ roomCode: string }>();
  const navigate = useNavigate();
  const gameOver = useGameStore((state) => state.gameOver);
  const view = useGameStore((state) => state.view);

  function handleReturnHome() {
    wsClient.disconnect();
    clearSession();
    useGameStore.getState().reset();
    navigate("/");
  }

  const players = view?.players ?? {};
  const scores = gameOver?.final_scores ?? {};

  return (
    <div>
      <h1>Game over</h1>
      <p>Room code: {roomCode}</p>

      {gameOver ? (
        <>
          <p>Winner: {players[gameOver.winner]?.nickname ?? gameOver.winner}</p>
          <ul>
            {Object.entries(scores).map(([playerId, score]) => (
              <li key={playerId}>
                {players[playerId]?.nickname ?? playerId}: {score}
              </li>
            ))}
          </ul>
        </>
      ) : (
        <p>Waiting for final results...</p>
      )}

      <button type="button" onClick={handleReturnHome}>
        Return home
      </button>
    </div>
  );
}
