/**
 * The PLAY_NUKE target-selection flow's inline panel: which sub-step it's
 * on drives which prompt/controls show. Vertex/edge picking itself
 * happens on the board (BoardCanvas, driven by the legalVertexIds/
 * legalEdgeIds computed in Game.tsx) -- this panel is just the
 * player-pick list, running commentary, and confirm/cancel controls.
 *
 * Mechanically extracted out of Game.tsx (was defined inline there) --
 * same props, same logic, only the styling moved from inline `style={}`
 * objects to this CSS module.
 */
import type { PlayerId, PlayerSummary } from "../../types/protocol";
import styles from "./NukeFlowPanel.module.css";

/** Steps of the PLAY_NUKE target-selection flow: pick the victim, then one
 * of their settlements/cities, then one of their roads, then confirm. */
export type NukeStep = "idle" | "pick_player" | "pick_vertex" | "pick_edge" | "confirm";

export interface NukeFlowPanelProps {
  step: NukeStep;
  otherPlayers: readonly PlayerSummary[];
  targetPlayerId: PlayerId | null;
  onPickPlayer: (playerId: PlayerId) => void;
  onConfirm: () => void;
  onCancel: () => void;
}

export function NukeFlowPanel({
  step,
  otherPlayers,
  targetPlayerId,
  onPickPlayer,
  onConfirm,
  onCancel,
}: NukeFlowPanelProps) {
  if (step === "idle") return null;
  const targetName = targetPlayerId
    ? otherPlayers.find((p) => p.player_id === targetPlayerId)?.nickname ?? targetPlayerId
    : null;

  return (
    <div className={styles.panel}>
      <p className={styles.title}>{"\u{1F4A3}"} Drop Nuke</p>

      {step === "pick_player" && (
        <>
          <p>Choose a target player:</p>
          <div className={styles.playerList}>
            {otherPlayers.map((p) => (
              <button key={p.player_id} type="button" className={styles.playerButton} onClick={() => onPickPlayer(p.player_id)}>
                {p.nickname}
              </button>
            ))}
          </div>
        </>
      )}

      {step === "pick_vertex" && <p>Click one of {targetName}'s settlements/cities on the board.</p>}

      {step === "pick_edge" && <p>Now click one of {targetName}'s roads on the board.</p>}

      {step === "confirm" && (
        <>
          <p>
            Destroy {targetName}'s selected settlement/city and road? This spends 2 of each resource (10 cards).
          </p>
          <button type="button" className={styles.confirmButton} onClick={onConfirm}>
            Confirm Nuke
          </button>
        </>
      )}

      <button type="button" className={styles.cancelButton} onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}
