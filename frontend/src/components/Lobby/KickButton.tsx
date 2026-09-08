/**
 * Host-only affordance to remove a seated player from the lobby.
 * Pure/presentational -- calls `onKick(playerId)` and lets the caller
 * (Wave 2: gameStore dispatch of a KICK_PLAYER action) decide what
 * happens next.
 */
import type { PlayerId } from "../../types/protocol";
import styles from "./PlayerList.module.css";

export interface KickButtonProps {
  playerId: PlayerId;
  nickname: string;
  onKick: (playerId: PlayerId) => void;
}

export function KickButton({ playerId, nickname, onKick }: KickButtonProps) {
  return (
    <button
      type="button"
      className={styles.kickButton}
      aria-label={`Kick ${nickname}`}
      title={`Remove ${nickname} from the room`}
      onClick={() => onKick(playerId)}
    >
      Kick
    </button>
  );
}
