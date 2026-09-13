/**
 * Host-only "Start Game" affordance. Renders nothing for non-host
 * viewers. Disabled (with a tooltip explaining why) whenever the
 * current seated human count is outside the supported range.
 *
 * Minimum is 1 (not 2): the backend auto-fills any gap between seated
 * humans and `settings.player_count` with bots on START_GAME (see
 * backend/app/api/websocket.py's bot auto-fill and
 * backend/app/game/rules/bot_ai.py), so a lone host can start a game
 * entirely against bots. See backend/app/game/settings_schema.py's
 * `player_count` bounds (2-8) for the upper limit.
 */
import styles from "./StartGameButton.module.css";

export const MIN_PLAYERS = 1;
export const MAX_PLAYERS = 8;

export interface StartGameButtonProps {
  playerCount: number;
  isHost: boolean;
  onStart: () => void;
}

export function StartGameButton({ playerCount, isHost, onStart }: StartGameButtonProps) {
  if (!isHost) {
    return null;
  }

  const outOfRange = playerCount < MIN_PLAYERS || playerCount > MAX_PLAYERS;
  const reason = outOfRange
    ? playerCount < MIN_PLAYERS
      ? `Need at least ${MIN_PLAYERS} players to start (currently ${playerCount}).`
      : `At most ${MAX_PLAYERS} players are supported (currently ${playerCount}).`
    : undefined;

  return (
    <button
      type="button"
      className={styles.startButton}
      disabled={outOfRange}
      title={reason}
      aria-disabled={outOfRange}
      onClick={onStart}
    >
      Start Game
    </button>
  );
}
