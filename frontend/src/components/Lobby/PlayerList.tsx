/**
 * Lobby roster: nickname, seat number, host badge, connection status,
 * and (host-only) a kick button per non-viewer row.
 *
 * Pure/presentational -- `players` is expected to be the
 * `RoomStatePayload.players: PlayerSummary[]` shape from the protocol;
 * no store/API imports, no derivation of "am I the host" from anything
 * beyond props.
 */
import type { PlayerId, PlayerSummary } from "../../types/protocol";
import styles from "./PlayerList.module.css";
import { KickButton } from "./KickButton";

export interface PlayerListProps {
  players: readonly PlayerSummary[];
  /** The player_id of whoever is viewing this list (for "(you)" + can't-kick-self). */
  viewerPlayerId: PlayerId;
  /** Whether the viewer is the host -- gates rendering of kick buttons. */
  isHost: boolean;
  /** Omit to render the roster read-only even for a host viewer. */
  onKick?: (playerId: PlayerId) => void;
}

export function PlayerList({ players, viewerPlayerId, isHost, onKick }: PlayerListProps) {
  const bySeat = [...players].sort((a, b) => a.seat - b.seat);

  return (
    <ul className={styles.list} aria-label="Players in room">
      {bySeat.map((player) => {
        const isViewer = player.player_id === viewerPlayerId;
        const canKick = isHost && !!onKick && !isViewer;
        return (
          <li key={player.player_id} className={styles.row}>
            <span className={styles.seat}>#{player.seat}</span>
            <span className={styles.nickname}>
              {player.nickname}
              {isViewer && <span className={styles.youTag}> (you)</span>}
            </span>
            {player.is_host && <span className={styles.hostBadge}>HOST</span>}
            <span
              className={player.is_connected ? styles.connected : styles.disconnected}
              title={player.is_connected ? "Connected" : "Disconnected"}
            >
              <span className={styles.statusDot} aria-hidden="true" />
              {player.is_connected ? "Connected" : "Disconnected"}
            </span>
            {canKick && (
              <KickButton playerId={player.player_id} nickname={player.nickname} onKick={onKick!} />
            )}
          </li>
        );
      })}
    </ul>
  );
}
