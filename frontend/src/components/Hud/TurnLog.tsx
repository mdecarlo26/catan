/**
 * Scrollable turn/action log. `TurnLogEntry` is a presentational-only
 * type (the wire protocol has no single "log entry" event -- per
 * ARCHITECTURE.md, GameState carries "a bounded action log for the turn
 * log / resync"); Wave 2 derives entries client-side from the stream of
 * ServerEvents (DICE_ROLLED, RESOURCES_DISTRIBUTED, TRADE_RESOLVED, ...)
 * rather than this component depending on any one wire shape.
 */
import styles from "./TurnLog.module.css";

export interface TurnLogEntry {
  id: string;
  /** Epoch milliseconds. */
  timestamp: number;
  message: string;
}

export interface TurnLogProps {
  entries: readonly TurnLogEntry[];
  /** CSS max-height for the scroll region, e.g. "16rem" or 240. */
  maxHeight?: number | string;
}

export function TurnLog({ entries, maxHeight = "16rem" }: TurnLogProps) {
  const newestFirst = [...entries].sort((a, b) => b.timestamp - a.timestamp);

  return (
    <div
      className={styles.log}
      style={{ maxHeight: typeof maxHeight === "number" ? `${maxHeight}px` : maxHeight }}
      role="log"
      aria-label="Turn log"
    >
      {newestFirst.length === 0 && <p className={styles.empty}>No actions yet.</p>}
      <ul className={styles.list}>
        {newestFirst.map((entry) => (
          <li key={entry.id} className={styles.entry}>
            <time className={styles.time} dateTime={new Date(entry.timestamp).toISOString()}>
              {formatTime(entry.timestamp)}
            </time>
            <span className={styles.message}>{entry.message}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

function formatTime(timestamp: number): string {
  const date = new Date(timestamp);
  const hh = String(date.getHours()).padStart(2, "0");
  const mm = String(date.getMinutes()).padStart(2, "0");
  const ss = String(date.getSeconds()).padStart(2, "0");
  return `${hh}:${mm}:${ss}`;
}
