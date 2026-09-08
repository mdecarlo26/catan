/**
 * Victory point counter: current VP vs. the room's
 * `victory_points_target`, plus badges for the longest-road / largest-
 * army bonuses (mirrors MaskedPlayerView's has_longest_road /
 * has_largest_army fields in protocol.ts). Pure/presentational.
 */
import styles from "./VpCounter.module.css";

export interface VpCounterProps {
  victoryPoints: number;
  targetVictoryPoints: number;
  hasLongestRoad?: boolean;
  hasLargestArmy?: boolean;
}

export function VpCounter({
  victoryPoints,
  targetVictoryPoints,
  hasLongestRoad = false,
  hasLargestArmy = false,
}: VpCounterProps) {
  return (
    <div className={styles.counter} aria-label="Victory points">
      <span className={styles.score}>
        {victoryPoints}
        <span className={styles.target}> / {targetVictoryPoints}</span>
      </span>
      <div className={styles.badges}>
        {hasLongestRoad && (
          <span className={styles.badge} title="Longest Road (+2 VP)">
            {"\u{1F6E4}\u{FE0F}"} Longest Road
          </span>
        )}
        {hasLargestArmy && (
          <span className={styles.badge} title="Largest Army (+2 VP)">
            {"\u{2694}\u{FE0F}"} Largest Army
          </span>
        )}
      </div>
    </div>
  );
}
