/**
 * Host-only "Start Game" affordance. Renders nothing for non-host
 * viewers. Disabled (with a tooltip explaining why) whenever the
 * current seated player count is outside the supported 2-8 range --
 * see backend/app/game/settings_schema.py's `player_count` bounds and
 * ARCHITECTURE.md's "host starts (requires 2-8 players present)".
 */
import styles from "./StartGameButton.module.css";

export const MIN_PLAYERS = 2;
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
