/**
 * Brief, auto-dismissing toast notifications for NUKE_DROPPED events --
 * shown to every player (not just the actor/victim), per the plan's
 * "dedicated frontend animation" note on that event. Pure/presentational:
 * the caller (Game.tsx) owns building the message text (which needs
 * player nicknames from gameStore's `view`, not available here) and the
 * timed removal of each entry; this just renders whatever list it's given.
 */
import styles from "./NukeToast.module.css";

export interface NukeToastItem {
  id: number;
  text: string;
}

export interface NukeToastProps {
  toasts: readonly NukeToastItem[];
}

export function NukeToast({ toasts }: NukeToastProps) {
  if (toasts.length === 0) return null;
  return (
    <div className={styles.stack} role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={styles.toast}>
          {"\u{1F4A5}"} {toast.text}
        </div>
      ))}
    </div>
  );
}
