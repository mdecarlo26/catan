/**
 * Blackjack-on-7 bet-placement panel, shown to an eligible non-dealer
 * player while `blackjack_round.status === "betting"` and they haven't
 * yet responded. Mirrors NukeFlowPanel's inline step-state-machine
 * pattern: `step` is owned by Game.tsx (since the "pick a structure on
 * the board" step needs to drive BoardCanvas's legalVertexIds/
 * legalEdgeIds highlighting, exactly like the nuke flow's player/vertex/
 * edge picking), this component just renders the current step's prompt
 * and controls. The resource-selection step keeps its own local
 * selection state, mirroring the discard panel's ResourceStepper pattern.
 */
import { useState } from "react";
import type { ResourceHand, ResourceType } from "../../types/protocol";
import { RESOURCE_ICON, RESOURCE_LABEL, RESOURCE_ORDER } from "./ResourceTray";
import styles from "./BlackjackBetPanel.module.css";

export type BlackjackBetStep = "idle" | "resources" | "pick_structure" | "confirm_structure";

function BetResourceStepper({
  hand,
  selection,
  onChange,
}: {
  hand: ResourceHand;
  selection: ResourceHand;
  onChange: (next: ResourceHand) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 12 }}>
      {RESOURCE_ORDER.map((type: ResourceType) => {
        const owned = hand[type] ?? 0;
        const count = selection[type] ?? 0;
        return (
          <div key={type} title={RESOURCE_LABEL[type]}>
            <div>
              {RESOURCE_ICON[type]} {count}/{owned}
            </div>
            <button
              type="button"
              disabled={count <= 0}
              onClick={() => onChange({ ...selection, [type]: count - 1 })}
            >
              -
            </button>
            <button
              type="button"
              disabled={count >= owned}
              onClick={() => onChange({ ...selection, [type]: count + 1 })}
            >
              +
            </button>
          </div>
        );
      })}
    </div>
  );
}

export interface BlackjackBetPanelProps {
  step: BlackjackBetStep;
  hand: ResourceHand;
  /** True once the bettor owns at least one settlement/city/road to stake. */
  hasStakeableStructure: boolean;
  /** Human-readable description of whatever was just clicked on the
   * board (e.g. "your settlement"), for the confirm_structure step. */
  pendingStructureLabel: string | null;
  onStartResources: () => void;
  onStartStructure: () => void;
  onSubmitResources: (resources: ResourceHand) => void;
  onConfirmStructure: () => void;
  onCancel: () => void;
  onDecline: () => void;
}

export function BlackjackBetPanel({
  step,
  hand,
  hasStakeableStructure,
  pendingStructureLabel,
  onStartResources,
  onStartStructure,
  onSubmitResources,
  onConfirmStructure,
  onCancel,
  onDecline,
}: BlackjackBetPanelProps) {
  const [selection, setSelection] = useState<ResourceHand>({});

  if (step === "idle") {
    return (
      <div className={styles.panel}>
        <p className={styles.title}>{"\u{2660}\u{FE0F}"} Blackjack round open</p>
        <p>Bet resources or a structure you own, or sit this one out.</p>
        <button type="button" onClick={onStartResources}>
          Bet Resources
        </button>
        <button type="button" disabled={!hasStakeableStructure} onClick={onStartStructure}>
          Bet a Structure
        </button>
        <button type="button" onClick={onDecline}>
          Decline
        </button>
      </div>
    );
  }

  if (step === "resources") {
    const total = RESOURCE_ORDER.reduce((sum, type) => sum + (selection[type] ?? 0), 0);
    return (
      <div className={styles.panel}>
        <p className={styles.title}>Choose resources to bet ({total} selected)</p>
        <BetResourceStepper hand={hand} selection={selection} onChange={setSelection} />
        <button type="button" disabled={total === 0} onClick={() => onSubmitResources(selection)}>
          Place Bet
        </button>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    );
  }

  if (step === "pick_structure") {
    return (
      <div className={styles.panel}>
        <p className={styles.title}>Click one of your own settlements/cities or roads on the board.</p>
        <button type="button" onClick={onCancel}>
          Cancel
        </button>
      </div>
    );
  }

  return (
    <div className={styles.panel}>
      <p className={styles.title}>
        Bet {pendingStructureLabel ?? "this piece"}? If you lose, it's removed from the board and
        returned to your own available-to-build supply.
      </p>
      <button type="button" onClick={onConfirmStructure}>
        Confirm Bet
      </button>
      <button type="button" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
