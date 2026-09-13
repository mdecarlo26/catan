/**
 * Green-felt casino-table frame hosting the blackjack-round HUD during
 * SceneManager's `minigame` scene. Purely a visual composition shell --
 * it renders whatever it's given via `children`, and does not know about
 * (or alter) BlackjackHands/BlackjackBetPanel/BlackjackToast internals.
 * Those components stay exactly as built elsewhere; this just re-parents
 * them into a table-shaped frame that reads as "casino table" instead of
 * a bare panel, distinct in palette from the board's ocean-blue felt.
 */
import type { ReactNode } from "react";
import styles from "./CasinoTable.module.css";

export interface CasinoTableProps {
  /** Typically <BlackjackHands />, <BlackjackBetPanel />, <BlackjackToast /> in some order. */
  children?: ReactNode;
  /** Table-top label. Defaults to a generic blackjack heading; pass "" to omit. */
  heading?: string;
}

export function CasinoTable({ children, heading = "Blackjack Table" }: CasinoTableProps) {
  return (
    <div className={styles.table}>
      {heading && <h2 className={styles.heading}>{heading}</h2>}
      <div className={styles.content}>{children}</div>
    </div>
  );
}
