/**
 * Dev-card "second hand": one fanned card face per owned development
 * card (synthetic `${type}-${index}` keys -- same-type dev cards are
 * interchangeable, so there's no real per-card identity to key on).
 * Fanned via the same `cardFan.ts` arc math `ResourceHandFan` uses, at a
 * smaller default footprint, so it reads as a second hand positioned
 * beside the resource fan. Pure/presentational -- takes a `DevCardHand`
 * (Partial<Record<DevCardType, number>>, per protocol.ts) plus an
 * optional `onPlay(cardType)` callback, same as before this rewrite.
 *
 * Three visual states per card:
 *  - normal playable: full opacity, hover-lift, dispatches `onPlay`.
 *  - locked/just-bought: derived from `boughtThisTurn` (same shape as
 *    `cards`) -- dimmed, no hover-lift, click is a visible no-op (lock
 *    glyph + "Not yet" label), never silently ignored.
 *  - victory-point: non-interactive, distinct gold/starred styling,
 *    always excluded from play regardless of `playableCardTypes` -- this
 *    mirrors `DEFAULT_PLAYABLE`'s pre-existing exclusion, which must stay
 *    unchanged.
 */
import type { CSSProperties } from "react";
import type { DevCardHand as DevCardHandData, DevCardType } from "../../types/protocol";
import { computeCardFan } from "./cardFan";
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

const CARD_WIDTH = 64;
const MAX_SPREAD_DEGREES = 40;

interface FannedDevCard {
  key: string;
  type: DevCardType;
  /** True when this individual card falls within `boughtThisTurn[type]`. */
  locked: boolean;
}

function buildCards(cards: DevCardHandData, boughtThisTurn: DevCardHandData): FannedDevCard[] {
  const result: FannedDevCard[] = [];
  for (const type of DEV_CARD_ORDER) {
    const count = cards[type] ?? 0;
    // Same-type cards are interchangeable, so which specific indices are
    // "the ones bought this turn" is arbitrary -- just the count matters.
    const lockedCount = Math.min(boughtThisTurn[type] ?? 0, count);
    for (let i = 0; i < count; i++) {
      result.push({ key: `${type}-${i}`, type, locked: i < lockedCount });
    }
  }
  return result;
}

export interface DevCardHandProps {
  cards: DevCardHandData;
  /**
   * Per-type count of cards bought this turn (same shape as `cards`).
   * Cards within that count render locked -- Catan rules forbid playing a
   * dev card the same turn it was bought. Defaults to none locked.
   */
  boughtThisTurn?: DevCardHandData;
  /** Card types the viewer is currently allowed to play (e.g. excludes
   * cards bought this same turn). Defaults to every non-VP type. */
  playableCardTypes?: readonly DevCardType[];
  onPlay?: (cardType: DevCardType) => void;
}

export function DevCardHand({
  cards,
  boughtThisTurn = {},
  playableCardTypes = DEFAULT_PLAYABLE,
  onPlay,
}: DevCardHandProps) {
  const total = DEV_CARD_ORDER.reduce((sum, type) => sum + (cards[type] ?? 0), 0);
  const fannedCards = buildCards(cards, boughtThisTurn);
  const count = fannedCards.length;

  return (
    <div className={styles.hand} aria-label={`Development cards, ${total} total`}>
      {count === 0 && <div className={styles.empty}>No development cards</div>}
      {fannedCards.map((card, i) => {
        const { x, y, rotationDeg } = computeCardFan(count, i, {
          cardWidth: CARD_WIDTH,
          maxSpreadDegrees: MAX_SPREAD_DEGREES,
        });
        const isVictoryPoint = card.type === "victory_point";
        const isPlayableType = !isVictoryPoint && playableCardTypes.includes(card.type);
        const canPlay = !!onPlay && isPlayableType && !card.locked;
        const style = {
          "--fan-x": `${x}px`,
          "--fan-y": `${y}px`,
          "--fan-rot": `${rotationDeg}deg`,
          zIndex: i,
        } as CSSProperties;

        const stateClass = isVictoryPoint
          ? styles.victoryPoint
          : card.locked
            ? styles.locked
            : styles.playable;

        return (
          <button
            key={card.key}
            type="button"
            className={`${styles.card} ${stateClass}`}
            style={style}
            disabled={!canPlay}
            onClick={canPlay ? () => onPlay!(card.type) : undefined}
            aria-label={
              isVictoryPoint
                ? `${DEV_CARD_LABEL[card.type]} card (banked automatically)`
                : card.locked
                  ? `${DEV_CARD_LABEL[card.type]} card, bought this turn, not yet playable`
                  : `${DEV_CARD_LABEL[card.type]} card${canPlay ? "" : " (not playable)"}`
            }
          >
            <span className={styles.icon} aria-hidden="true">
              {DEV_CARD_ICON[card.type]}
            </span>
            <span className={styles.label}>{DEV_CARD_LABEL[card.type]}</span>
            {card.locked && !isVictoryPoint && (
              <span className={styles.lockBadge} aria-hidden="true">
                {"\u{1F512}"} Not yet
              </span>
            )}
            {isVictoryPoint && (
              <span className={styles.vpBadge} aria-hidden="true">
                VP
              </span>
            )}
          </button>
        );
      })}
    </div>
  );
}
