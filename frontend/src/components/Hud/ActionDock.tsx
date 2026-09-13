/**
 * Dice + turn-timer + primary action-button column. Purely presentational
 * -- every gate (`canRoll`, `canAct`, which buttons are enabled) and every
 * dispatch is decided by Game.tsx exactly as before; this component just
 * gives that existing inline markup (previously scattered directly in
 * Game.tsx's return JSX) a single styled home. Reuses `DiceRoll` for the
 * pip-grid dice faces rather than re-implementing dice rendering.
 */
import { DiceRoll } from "./DiceRoll";
import styles from "./ActionDock.module.css";

/** Mirrors Game.tsx's local `BuildMode` union. */
type BuildMode = "settlement" | "road" | "city" | "road_building" | null;
type BuildModeOption = "settlement" | "road" | "city";

export interface ActionDockProps {
  /** Dice face values; null renders no pips (mirrors DiceRoll's own contract). */
  die1: number | null;
  die2: number | null;
  /** Distinct per roll event; null if no live event has been observed. */
  rollSeq: number | null;
  /** Whether the dice tray should render at all (a roll has happened this game). */
  showDice: boolean;
  /** Rush-mode auto-roll countdown, in seconds; null outside rush mode or
   * before the server has primed a last-roll timestamp. */
  timerSeconds: number | null;

  /** Whether the Roll Dice button should render. */
  canRoll: boolean;
  onRoll: () => void;

  /** Whether the post-roll Build/Buy/End-Turn action row should render. */
  canAct: boolean;
  buildMode: BuildMode;
  onSelectBuildMode: (mode: BuildModeOption) => void;
  onBuyDevCard: () => void;
  onCancelBuildMode: () => void;
  /** True while a lone road-building road has been picked, awaiting the
   * "build just this 1 road" shortcut. */
  showConfirmSingleRoad: boolean;
  onConfirmSingleRoad: () => void;
  /** False in rush mode, which has no turns to end. */
  showEndTurn: boolean;
  onEndTurn: () => void;
}

function formatTimer(seconds: number): string {
  const clamped = Math.max(0, Math.floor(seconds));
  const minutes = Math.floor(clamped / 60);
  const secs = clamped % 60;
  return `${minutes}:${String(secs).padStart(2, "0")}`;
}

export function ActionDock({
  die1,
  die2,
  rollSeq,
  showDice,
  timerSeconds,
  canRoll,
  onRoll,
  canAct,
  buildMode,
  onSelectBuildMode,
  onBuyDevCard,
  onCancelBuildMode,
  showConfirmSingleRoad,
  onConfirmSingleRoad,
  showEndTurn,
  onEndTurn,
}: ActionDockProps) {
  return (
    <div className={styles.dock}>
      {showDice && (
        <div className={styles.diceRow}>
          <DiceRoll die1={die1} die2={die2} rollSeq={rollSeq} />
        </div>
      )}

      {timerSeconds != null && (
        <div className={styles.timer} aria-label="Next auto-roll countdown">
          {formatTimer(timerSeconds)}
        </div>
      )}

      {canRoll && (
        <button type="button" className={styles.rollButton} onClick={onRoll}>
          Roll Dice
        </button>
      )}

      {canAct && (
        <div className={styles.actions}>
          <button
            type="button"
            className={styles.actionButton}
            disabled={buildMode === "settlement"}
            onClick={() => onSelectBuildMode("settlement")}
          >
            Build Settlement
          </button>
          <button
            type="button"
            className={styles.actionButton}
            disabled={buildMode === "road"}
            onClick={() => onSelectBuildMode("road")}
          >
            Build Road
          </button>
          <button
            type="button"
            className={styles.actionButton}
            disabled={buildMode === "city"}
            onClick={() => onSelectBuildMode("city")}
          >
            Build City
          </button>
          <button type="button" className={styles.actionButton} onClick={onBuyDevCard}>
            Buy Dev Card
          </button>
          {buildMode && (
            <button type="button" className={styles.cancelButton} onClick={onCancelBuildMode}>
              Cancel
            </button>
          )}
          {showConfirmSingleRoad && (
            <button type="button" className={styles.actionButton} onClick={onConfirmSingleRoad}>
              Build just this 1 road
            </button>
          )}
          {showEndTurn && (
            <button type="button" className={styles.endTurnButton} onClick={onEndTurn}>
              End Turn
            </button>
          )}
        </div>
      )}
    </div>
  );
}
