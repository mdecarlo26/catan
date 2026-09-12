/**
 * Brief, auto-dismissing toast notifications for BLACKJACK_ROUND_RESOLVED
 * events -- shown to every player, mirroring NukeToast's pattern exactly.
 * Pure/presentational: Game.tsx owns building the message text (needs
 * player nicknames from gameStore's `view`) and the timed removal of each
 * entry; this just renders whatever list it's given.
 */
import styles from "./BlackjackToast.module.css";

export interface BlackjackToastItem {
  id: number;
  text: string;
}

export interface BlackjackToastProps {
  toasts: readonly BlackjackToastItem[];
}

export function BlackjackToast({ toasts }: BlackjackToastProps) {
  if (toasts.length === 0) return null;
  return (
    <div className={styles.stack} role="status" aria-live="polite">
      {toasts.map((toast) => (
        <div key={toast.id} className={styles.toast}>
          {"\u{2660}\u{FE0F}"} {toast.text}
        </div>
      ))}
    </div>
  );
}
