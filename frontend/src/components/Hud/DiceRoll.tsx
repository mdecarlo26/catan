/**
 * Two-die display with a brief tumble animation on a new roll, instead of
 * the final values just appearing instantly (Phase 3 "animation polish
 * (nuke, robber, dice)" per ARCHITECTURE.md). Driven by `rollSeq` -- a
 * monotonic id distinct per DICE_ROLLED event (see gameStore.ts's
 * `DiceRollEventRecord`) -- rather than by die1/die2 alone, since two
 * rolls can land on the same numbers but should still each animate.
 *
 * When `rollSeq` is null (no live roll event observed yet this session,
 * e.g. a fresh mount mid-game via reconnect where only a static
 * `last_dice_roll` is available), the values are shown immediately with
 * no animation.
 */
import { useEffect, useRef, useState } from "react";
import styles from "./DiceRoll.module.css";

export interface DiceRollProps {
  die1: number | null;
  die2: number | null;
  /** Distinct per roll event; null if no live event has been observed. */
  rollSeq: number | null;
}

const PIP_LAYOUT: Record<number, ReadonlyArray<readonly [number, number]>> = {
  1: [[50, 50]],
  2: [
    [25, 25],
    [75, 75],
  ],
  3: [
    [25, 25],
    [50, 50],
    [75, 75],
  ],
  4: [
    [25, 25],
    [75, 25],
    [25, 75],
    [75, 75],
  ],
  5: [
    [25, 25],
    [75, 25],
    [50, 50],
    [25, 75],
    [75, 75],
  ],
  6: [
    [25, 25],
    [75, 25],
    [25, 50],
    [75, 50],
    [25, 75],
    [75, 75],
  ],
};

function Die({ value, rolling }: { value: number; rolling: boolean }) {
  const pips = PIP_LAYOUT[value] ?? [];
  return (
    <div className={rolling ? `${styles.die} ${styles.rolling}` : styles.die} aria-hidden="true">
      {pips.map(([x, y], i) => (
        <span key={i} className={styles.pip} style={{ left: `${x}%`, top: `${y}%` }} />
      ))}
    </div>
  );
}

const TUMBLE_STEPS = 5;
const TUMBLE_STEP_MS = 60;

export function DiceRoll({ die1, die2, rollSeq }: DiceRollProps) {
  const [display1, setDisplay1] = useState(die1 ?? 1);
  const [display2, setDisplay2] = useState(die2 ?? 1);
  const [rolling, setRolling] = useState(false);
  const seenSeqRef = useRef<number | null>(null);
  const timersRef = useRef<number[]>([]);

  useEffect(() => {
    return () => {
      timersRef.current.forEach((t) => window.clearTimeout(t));
    };
  }, []);

  useEffect(() => {
    if (die1 == null || die2 == null) return;

    if (rollSeq == null) {
      // No live event to animate off of -- just reflect the current values.
      setDisplay1(die1);
      setDisplay2(die2);
      return;
    }
    if (seenSeqRef.current === rollSeq) return;
    seenSeqRef.current = rollSeq;

    timersRef.current.forEach((t) => window.clearTimeout(t));
    timersRef.current = [];

    setRolling(true);
    for (let i = 0; i < TUMBLE_STEPS; i++) {
      const t = window.setTimeout(() => {
        setDisplay1(1 + Math.floor(Math.random() * 6));
        setDisplay2(1 + Math.floor(Math.random() * 6));
      }, i * TUMBLE_STEP_MS);
      timersRef.current.push(t);
    }
    const finalTimer = window.setTimeout(
      () => {
        setDisplay1(die1);
        setDisplay2(die2);
        setRolling(false);
      },
      TUMBLE_STEPS * TUMBLE_STEP_MS
    );
    timersRef.current.push(finalTimer);
  }, [die1, die2, rollSeq]);

  const hasRoll = die1 != null && die2 != null;

  return (
    <div
      className={styles.tray}
      aria-label={hasRoll ? `Dice: ${display1} and ${display2}` : "No roll yet"}
    >
      <Die value={display1} rolling={rolling} />
      <Die value={display2} rolling={rolling} />
      {hasRoll && <span className={styles.total}>= {display1 + display2}</span>}
    </div>
  );
}
