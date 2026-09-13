/**
 * Cross-fades between the main board+HUD view and the blackjack-on-7
 * "minigame" casino-table view, driven purely by `phase` (pass
 * `view.phase` from the caller). Follows the same `setTimeout`-chain
 * animation-sequencing idiom `DiceRoll.tsx` already uses elsewhere in
 * this codebase -- no new animation library.
 *
 * CRITICAL constraint (see the Phase 1 plan's Workstream A): `boardAndHud`
 * contains `BoardCanvas`, whose mount effect does a real PixiJS
 * `Application.init()`/`app.destroy()` cycle. Unmounting and remounting
 * it on every minigame round would drop in-flight ticker/animation state
 * and pay Pixi init cost repeatedly. So `boardAndHud` and
 * `minigameContent` are BOTH ALWAYS MOUNTED in the tree below, stacked
 * absolutely on top of one another, and only ever distinguished by CSS
 * opacity/pointer-events -- never by conditional rendering. Only the
 * purely-decorative `MinigameTitleCard` (no external state, cheap to
 * remount) is mounted/unmounted, so its own fade/scale-in animation
 * replays fresh on both the way in and the way out.
 *
 * State machine:
 *
 *   board --(phase becomes "blackjack_round")--> entering_minigame
 *     entering_minigame: title card mounts, fades in, holds briefly.
 *   entering_minigame --(hold elapses)--> minigame
 *     minigame: title card unmounts; board fades out, casino table fades in.
 *   minigame --(phase leaves "blackjack_round")--> exiting_minigame
 *     exiting_minigame: title card remounts, fades in, holds briefly
 *     (casino table stays visible underneath -- the mirror image of
 *     entering_minigame).
 *   exiting_minigame --(hold elapses)--> board
 *     board: title card unmounts; casino table fades out, board fades in.
 */
import { useEffect, useRef, useState, type ReactNode } from "react";
import type { Phase } from "../../types/protocol";
import { MinigameTitleCard } from "./MinigameTitleCard";
import styles from "./SceneManager.module.css";

export type SceneState = "board" | "entering_minigame" | "minigame" | "exiting_minigame";

/** How long the title card holds, centered, before the scene underneath it swaps. Spec range: 600-900ms. */
const TITLE_HOLD_MS = 750;

/** Per-state target opacity for the two always-mounted layers, plus whether the title card should be mounted. */
const LAYER_TARGETS: Record<SceneState, { board: number; minigame: number; titleCard: boolean }> = {
  board: { board: 1, minigame: 0, titleCard: false },
  entering_minigame: { board: 1, minigame: 0, titleCard: true },
  minigame: { board: 0, minigame: 1, titleCard: false },
  exiting_minigame: { board: 0, minigame: 1, titleCard: true },
};

export interface SceneManagerProps {
  /** Current game phase; a transition into/out of "blackjack_round" drives the state machine. */
  phase: Phase;
  /** The normal board + surrounding HUD. Always mounted; visibility is CSS-only. */
  boardAndHud: ReactNode;
  /** The casino-table blackjack view. Always mounted; visibility is CSS-only. */
  minigameContent: ReactNode;
}

export function SceneManager({ phase, boardAndHud, minigameContent }: SceneManagerProps) {
  // Start directly in "minigame" (no entrance beat) if we mount mid-round
  // (e.g. a reconnect), rather than playing the entrance animation for a
  // transition that didn't actually just happen.
  const [scene, setScene] = useState<SceneState>(phase === "blackjack_round" ? "minigame" : "board");
  const prevPhaseRef = useRef<Phase>(phase);
  const timersRef = useRef<number[]>([]);

  const clearTimers = () => {
    timersRef.current.forEach((id) => window.clearTimeout(id));
    timersRef.current = [];
  };

  // Clear any in-flight timers on unmount, mirroring DiceRoll's cleanup effect.
  useEffect(() => clearTimers, []);

  useEffect(() => {
    const prevPhase = prevPhaseRef.current;
    prevPhaseRef.current = phase;
    if (prevPhase === phase) return;

    const enteringMinigame = phase === "blackjack_round" && prevPhase !== "blackjack_round";
    const exitingMinigame = prevPhase === "blackjack_round" && phase !== "blackjack_round";

    if (enteringMinigame) {
      clearTimers();
      setScene("entering_minigame");
      const id = window.setTimeout(() => setScene("minigame"), TITLE_HOLD_MS);
      timersRef.current.push(id);
    } else if (exitingMinigame) {
      clearTimers();
      setScene("exiting_minigame");
      const id = window.setTimeout(() => setScene("board"), TITLE_HOLD_MS);
      timersRef.current.push(id);
    }
    // Any other phase change (e.g. main -> roll) is irrelevant to this
    // machine; it only cares about the blackjack_round boundary.
  }, [phase]);

  const targets = LAYER_TARGETS[scene];
  const boardLayerClass = `${styles.layer} ${targets.board === 1 ? styles.layerActive : styles.layerInactive}`;
  const minigameLayerClass = `${styles.layer} ${targets.minigame === 1 ? styles.layerActive : styles.layerInactive}`;

  return (
    <div className={styles.root} data-scene-state={scene}>
      <div className={boardLayerClass} aria-hidden={targets.board !== 1}>
        {boardAndHud}
      </div>
      <div className={minigameLayerClass} aria-hidden={targets.minigame !== 1}>
        {minigameContent}
      </div>
      {targets.titleCard && (
        <div className={styles.titleLayer}>
          <MinigameTitleCard />
        </div>
      )}
    </div>
  );
}
