/**
 * The viewer's own resource hand as a fanned parabolic arc of large
 * "poster style" cards -- one visual card per individual resource unit
 * held (a count-4 grain hand renders four grain cards), not one slot per
 * resource type. Mirrors the reference prototype's own-hand treatment:
 * big centered icon over a tinted glow, resource name in caps below,
 * small corner glyphs, gold border + lift-and-scale on selection.
 *
 * Pure/presentational, matching every other Hud component's convention:
 * the caller owns selection state and passes it in via `selected` +
 * `onToggle`. Reuses `ResourceTray`'s icon/label exports (so icon/label
 * choices stay in one place) and `cardFan.ts`'s shared arc math.
 */
import type { CSSProperties } from "react";
import type { ResourceHand, ResourceType } from "../../types/protocol";
import { RESOURCE_ICON, RESOURCE_LABEL, RESOURCE_ORDER } from "./ResourceTray";
import { computeCardFan } from "./cardFan";
import styles from "./ResourceHandFan.module.css";

/** Per-type tint used for the card's glowing inner area. */
const RESOURCE_TINT: Record<ResourceType, string> = {
  brick: "#c1543a",
  lumber: "#3f8a4a",
  ore: "#7b8794",
  grain: "#d9ab2e",
  wool: "#8fbf63",
};

const CARD_WIDTH = 92;
const MAX_SPREAD_DEGREES = 50;

interface FannedResourceCard {
  key: string;
  type: ResourceType;
}

function buildCards(hand: ResourceHand): FannedResourceCard[] {
  const cards: FannedResourceCard[] = [];
  for (const type of RESOURCE_ORDER) {
    const count = hand[type] ?? 0;
    for (let i = 0; i < count; i++) {
      cards.push({ key: `${type}-${i}`, type });
    }
  }
  return cards;
}

export interface ResourceHandFanProps {
  hand: ResourceHand;
  /** Keys (e.g. "brick-0") of the currently selected individual cards. */
  selected?: ReadonlySet<string>;
  onToggle?: (cardKey: string, type: ResourceType) => void;
}

export function ResourceHandFan({ hand, selected, onToggle }: ResourceHandFanProps) {
  const cards = buildCards(hand);
  const count = cards.length;

  return (
    <div className={styles.fan} aria-label={`Resource hand, ${count} cards`}>
      {count === 0 && <div className={styles.empty}>No resource cards</div>}
      {cards.map((card, i) => {
        const { x, y, rotationDeg } = computeCardFan(count, i, {
          cardWidth: CARD_WIDTH,
          maxSpreadDegrees: MAX_SPREAD_DEGREES,
        });
        const isSelected = selected?.has(card.key) ?? false;
        const style = {
          "--fan-x": `${x}px`,
          "--fan-y": `${y}px`,
          "--fan-rot": `${rotationDeg}deg`,
          "--fan-tint": RESOURCE_TINT[card.type],
          zIndex: i,
        } as CSSProperties;

        return (
          <button
            key={card.key}
            type="button"
            className={`${styles.card} ${isSelected ? styles.selected : ""}`}
            style={style}
            disabled={!onToggle}
            onClick={() => onToggle?.(card.key, card.type)}
            aria-pressed={isSelected}
            aria-label={`${RESOURCE_LABEL[card.type]} card${isSelected ? ", selected" : ""}`}
          >
            <span className={`${styles.corner} ${styles.cornerTop}`} aria-hidden="true">
              {RESOURCE_ICON[card.type]}
            </span>
            <span className={styles.inner}>
              <span className={styles.icon} aria-hidden="true">
                {RESOURCE_ICON[card.type]}
              </span>
            </span>
            <span className={styles.label}>{RESOURCE_LABEL[card.type]}</span>
            <span className={`${styles.corner} ${styles.cornerBottom}`} aria-hidden="true">
              {RESOURCE_ICON[card.type]}
            </span>
          </button>
        );
      })}
    </div>
  );
}
