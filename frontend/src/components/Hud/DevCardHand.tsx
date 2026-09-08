/**
 * Dev-card hand: one row per card type showing the count held, with a
 * "Play" affordance for types the caller marks playable this turn.
 * Pure/presentational -- takes a `DevCardHand` (Partial<Record
 * <DevCardType, number>>, per protocol.ts) plus an optional
 * `onPlay(cardType)` callback.
 */
import type { DevCardHand, DevCardType } from "../../types/protocol";
import styles from "./DevCardHand.module.css";

export const DEV_CARD_ORDER: readonly DevCardType[] = [
  "knight",
  "road_building",
  "year_of_plenty",
  "monopoly",
  "victory_point",
];

export const DEV_CARD_ICON: Record<DevCardType, string> = {
  knight: "\u{2694}\u{FE0F}",
  road_building: "\u{1F6E4}\u{FE0F}",
  year_of_plenty: "\u{1F381}",
  monopoly: "\u{1F4B0}",
  victory_point: "\u{2B50}",
};

export const DEV_CARD_LABEL: Record<DevCardType, string> = {
  knight: "Knight",
  road_building: "Road Building",
  year_of_plenty: "Year of Plenty",
  monopoly: "Monopoly",
  victory_point: "Victory Point",
};

/** Victory-point cards are banked automatically, never "played" by the
 * player -- so by default every other type with count > 0 is playable. */
const DEFAULT_PLAYABLE: readonly DevCardType[] = DEV_CARD_ORDER.filter(
  (type) => type !== "victory_point"
);

export interface DevCardHandProps {
  cards: DevCardHand;
  /** Card types the viewer is currently allowed to play (e.g. excludes
   * cards bought this same turn). Defaults to every non-VP type. */
  playableCardTypes?: readonly DevCardType[];
  onPlay?: (cardType: DevCardType) => void;
}

export function DevCardHand({
  cards,
  playableCardTypes = DEFAULT_PLAYABLE,
  onPlay,
}: DevCardHandProps) {
  const total = DEV_CARD_ORDER.reduce((sum, type) => sum + (cards[type] ?? 0), 0);

  return (
    <div className={styles.hand} aria-label={`Development cards, ${total} total`}>
      {DEV_CARD_ORDER.map((type) => {
        const count = cards[type] ?? 0;
        const canPlay = !!onPlay && count > 0 && playableCardTypes.includes(type);
        return (
          <div key={type} className={styles.row}>
            <span className={styles.icon} aria-hidden="true">
              {DEV_CARD_ICON[type]}
            </span>
            <span className={styles.label}>{DEV_CARD_LABEL[type]}</span>
            <span className={styles.count}>{count}</span>
            {canPlay && (
              <button
                type="button"
                className={styles.playButton}
                onClick={() => onPlay!(type)}
                aria-label={`Play ${DEV_CARD_LABEL[type]}`}
              >
                Play
              </button>
            )}
          </div>
        );
      })}
    </div>
  );
}
