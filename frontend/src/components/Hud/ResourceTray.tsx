/**
 * Resource tray: one icon + count per resource type in the viewer's
 * hand. Pure/presentational -- takes a `ResourceHand` (Partial<Record
 * <ResourceType, number>>, per protocol.ts) as its only data prop.
 */
import type { ResourceHand, ResourceType } from "../../types/protocol";
import styles from "./ResourceTray.module.css";

export const RESOURCE_ORDER: readonly ResourceType[] = ["brick", "lumber", "ore", "grain", "wool"];

export const RESOURCE_ICON: Record<ResourceType, string> = {
  brick: "\u{1F9F1}",
  lumber: "\u{1F332}",
  ore: "\u{26F0}\u{FE0F}",
  grain: "\u{1F33E}",
  wool: "\u{1F411}",
};

export const RESOURCE_LABEL: Record<ResourceType, string> = {
  brick: "Brick",
  lumber: "Lumber",
  ore: "Ore",
  grain: "Grain",
  wool: "Wool",
};

export interface ResourceTrayProps {
  hand: ResourceHand;
}

export function ResourceTray({ hand }: ResourceTrayProps) {
  const total = RESOURCE_ORDER.reduce((sum, type) => sum + (hand[type] ?? 0), 0);

  return (
    <div className={styles.tray} aria-label={`Resource hand, ${total} cards`}>
      {RESOURCE_ORDER.map((type) => (
        <div key={type} className={styles.slot} title={RESOURCE_LABEL[type]}>
          <span className={styles.icon} aria-hidden="true">
            {RESOURCE_ICON[type]}
          </span>
          <span className={styles.count}>{hand[type] ?? 0}</span>
        </div>
      ))}
    </div>
  );
}
