/**
 * Layout for all of the viewer's opponents (1-7 players), scaling
 * gracefully across the full 2-8 player-count range.
 *
 * Two responsive modes:
 *  - 4 or fewer opponents: split into left/right vertical column stacks
 *    (Math.ceil(n/2) left, Math.floor(n/2) right) -- the closest fit to
 *    a fixed-seat spatial layout beside the board.
 *  - More than 4 (5-7): a single horizontal row that wraps
 *    (`flex-wrap: wrap; justify-content: center`) with flexible
 *    per-panel sizing, so a full 8-player table degrades gracefully
 *    instead of squeezing panels unreadably thin.
 *
 * This component only renders the panels in the right layout mode --
 * where it gets mounted relative to the board is the caller's call.
 * Pure/presentational: build a fresh `Object.values(view.players)`
 * (minus the viewer) array for `opponents` rather than reusing any
 * narrower derivation another component depends on.
 */
import type { PlayerId } from "../../types/protocol";
import { OpponentPanel } from "./OpponentPanel";
import type { OpponentInfo } from "./OpponentPanel";
import styles from "./OpponentRail.module.css";

export type { OpponentInfo };

export interface OpponentRailProps {
  opponents: readonly OpponentInfo[];
  /** player_id of whoever's turn it currently is, if any -- drives the gold ring. */
  currentTurnPlayerId?: PlayerId | null;
}

/** At or below this many opponents, use the left/right column layout. */
const COLUMN_LAYOUT_MAX_OPPONENTS = 4;

export function OpponentRail({ opponents, currentTurnPlayerId = null }: OpponentRailProps) {
  if (opponents.length === 0) return null;

  if (opponents.length <= COLUMN_LAYOUT_MAX_OPPONENTS) {
    const splitAt = Math.ceil(opponents.length / 2);
    const left = opponents.slice(0, splitAt);
    const right = opponents.slice(splitAt);
    return (
      <div className={styles.columns} aria-label="Opponents">
        <div className={styles.column}>
          {left.map((opponent) => (
            <OpponentPanel
              key={opponent.player_id}
              {...opponent}
              isCurrentTurn={opponent.player_id === currentTurnPlayerId}
            />
          ))}
        </div>
        <div className={styles.column}>
          {right.map((opponent) => (
            <OpponentPanel
              key={opponent.player_id}
              {...opponent}
              isCurrentTurn={opponent.player_id === currentTurnPlayerId}
            />
          ))}
        </div>
      </div>
    );
  }

  return (
    <div className={styles.row} aria-label="Opponents">
      {opponents.map((opponent) => (
        <div key={opponent.player_id} className={styles.rowItem}>
          <OpponentPanel {...opponent} isCurrentTurn={opponent.player_id === currentTurnPlayerId} />
        </div>
      ))}
    </div>
  );
}
