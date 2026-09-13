/**
 * Mandatory-discard-on-7 UI: a bounded +/- resource stepper the viewer
 * uses to pick exactly `required` cards to discard, then submit.
 * Mechanically extracted out of Game.tsx (was defined inline there) --
 * same props, same logic, only the styling moved from inline `style={}`
 * objects to this CSS module.
 */
import { useState } from "react";
import type { ResourceHand } from "../../types/protocol";
import { RESOURCE_ICON, RESOURCE_LABEL, RESOURCE_ORDER } from "./ResourceTray";
import styles from "./DiscardPanel.module.css";

/** Minimal +/- resource picker, bounded by `hand`, used for the discard UI. */
function ResourceStepper({
  hand,
  selection,
  onChange,
}: {
  hand: ResourceHand;
  selection: ResourceHand;
  onChange: (next: ResourceHand) => void;
}) {
  return (
    <div className={styles.stepperRow}>
      {RESOURCE_ORDER.map((type) => {
        const owned = hand[type] ?? 0;
        const count = selection[type] ?? 0;
        return (
          <div key={type} className={styles.stepperSlot} title={RESOURCE_LABEL[type]}>
            <div className={styles.stepperCount}>
              {RESOURCE_ICON[type]} {count}/{owned}
            </div>
            <div className={styles.stepperButtons}>
              <button
                type="button"
                className={styles.stepperButton}
                disabled={count <= 0}
                onClick={() => onChange({ ...selection, [type]: count - 1 })}
              >
                -
              </button>
              <button
                type="button"
                className={styles.stepperButton}
                disabled={count >= owned}
                onClick={() => onChange({ ...selection, [type]: count + 1 })}
              >
                +
              </button>
            </div>
          </div>
        );
      })}
    </div>
  );
}

export interface DiscardPanelProps {
  hand: ResourceHand;
  required: number;
  onSubmit: (resources: ResourceHand) => void;
}

export function DiscardPanel({ hand, required, onSubmit }: DiscardPanelProps) {
  const [selection, setSelection] = useState<ResourceHand>({});
  const total = RESOURCE_ORDER.reduce((sum, type) => sum + (selection[type] ?? 0), 0);
  return (
    <div role="alert" className={styles.panel}>
      <p className={styles.message}>
        You must discard {required} cards ({total}/{required} selected).
      </p>
      <ResourceStepper hand={hand} selection={selection} onChange={setSelection} />
      <button type="button" className={styles.submitButton} disabled={total !== required} onClick={() => onSubmit(selection)}>
        Discard
      </button>
    </div>
  );
}
