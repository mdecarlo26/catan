/**
 * Bold centered "MINIGAME" title card shown by SceneManager during its
 * entering_minigame/exiting_minigame beats. Purely decorative/presentational
 * -- no game state, no props required. SceneManager mounts/unmounts this
 * component (rather than toggling its visibility like the always-mounted
 * board/casino layers) specifically so its scale/fade-in CSS animation
 * (see .module.css's `cardIn` keyframes) replays fresh every time the beat
 * fires, both on the way in and on the way out.
 */
import styles from "./MinigameTitleCard.module.css";

export interface MinigameTitleCardProps {
  /** Defaults to "MINIGAME". Overridable in case a later minigame reuses this card. */
  label?: string;
}

export function MinigameTitleCard({ label = "MINIGAME" }: MinigameTitleCardProps) {
  return (
    <div className={styles.overlay} aria-hidden="true">
      <span className={styles.card}>{label}</span>
    </div>
  );
}
