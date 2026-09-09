/**
 * "Drop Nuke" trigger: a clearly-visible, dedicated affordance for
 * starting the PLAY_NUKE target-selection flow (see Game.tsx, which owns
 * the actual multi-step player -> vertex -> edge -> confirm flow and
 * dispatches the action). Pure/presentational -- eligibility (nuke_mode
 * on + the viewer's hand qualifying, per gameStore.ts's
 * `isNukeEligibleHand`) and whether it's currently actionable (the
 * viewer's MAIN-phase turn, no other pending action) are both decided by
 * the caller; this component just renders the button, disabled with an
 * explanatory title when it isn't currently usable.
 */
import styles from "./NukeButton.module.css";

export interface NukeButtonProps {
  /** False disables the button (e.g. not the viewer's turn right now). */
  disabled?: boolean;
  /** Shown as the button's title/tooltip, e.g. explaining why it's disabled. */
  title?: string;
  onClick: () => void;
}

export function NukeButton({ disabled = false, title, onClick }: NukeButtonProps) {
  return (
    <button
      type="button"
      className={styles.button}
      disabled={disabled}
      title={title}
      onClick={onClick}
      aria-label="Drop Nuke"
    >
      <span aria-hidden="true">{"\u{1F4A3}"}</span> Drop Nuke
    </button>
  );
}
