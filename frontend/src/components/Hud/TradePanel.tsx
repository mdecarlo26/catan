/**
 * Trade panel: propose a bank/port trade or a player-to-player trade,
 * and respond to incoming trade offers. Pure/presentational -- all
 * outbound intents are handed back via callback props using the exact
 * payload shapes from src/types/protocol.ts (BankTradePayload,
 * PortTradePayload, ProposeTradePayload, RespondTradePayload) so Wave 2
 * can wire each straight into a wsClient dispatch with no reshaping.
 *
 * Local UI-only state (which resources/quantities are currently
 * selected in the builder, which tab is active) lives in this
 * component via useState -- that's ephemeral form state, not game
 * state, and does not come from gameStore.
 */
import { useMemo, useState } from "react";
import type {
  BankTradePayload,
  PlayerId,
  PlayerSummary,
  PortTradePayload,
  PortType,
  ProposeTradePayload,
  ResourceHand,
  ResourceType,
  RespondTradePayload,
  TradeOfferedPayload,
} from "../../types/protocol";
import { RESOURCE_ICON, RESOURCE_LABEL, RESOURCE_ORDER } from "./ResourceTray";
import styles from "./TradePanel.module.css";

export interface PortAccess {
  port_type: PortType;
  /** Units of the port's resource given up per unit received, e.g. 2 or 3. */
  rate: number;
}

export interface TradePanelProps {
  /** The viewer's own hand -- bounds how much they can offer. */
  hand: ResourceHand;
  /** Other seated players, as candidate player-to-player trade targets. */
  otherPlayers: readonly PlayerSummary[];
  /** Bank trade rate when no matching port is held (standard Catan: 4). */
  bankRate?: number;
  /** Ports the viewer currently has access to, if any. */
  ports?: readonly PortAccess[];
  /** Trades awaiting a response, e.g. from TRADE_OFFERED events. */
  pendingTrades?: readonly TradeOfferedPayload[];
  onProposeBankTrade: (payload: BankTradePayload) => void;
  onProposePortTrade: (payload: PortTradePayload) => void;
  onProposePlayerTrade: (payload: ProposeTradePayload) => void;
  onRespondTrade?: (payload: RespondTradePayload) => void;
}

type TradeMode = "bank_port" | "player";

function emptyHand(): ResourceHand {
  return {};
}

function handTotal(hand: ResourceHand): number {
  return RESOURCE_ORDER.reduce((sum, type) => sum + (hand[type] ?? 0), 0);
}

function adjust(hand: ResourceHand, type: ResourceType, delta: number, max?: number): ResourceHand {
  const next = (hand[type] ?? 0) + delta;
  const clamped = Math.max(0, max !== undefined ? Math.min(next, max) : next);
  if (clamped === 0) {
    const { [type]: _drop, ...rest } = hand;
    return rest;
  }
  return { ...hand, [type]: clamped };
}

function ResourcePicker({
  title,
  hand,
  maxPerType,
  onChange,
}: {
  title: string;
  hand: ResourceHand;
  maxPerType?: (type: ResourceType) => number | undefined;
  onChange: (next: ResourceHand) => void;
}) {
  return (
    <div className={styles.picker}>
      <p className={styles.pickerTitle}>{title}</p>
      <div className={styles.pickerRow}>
        {RESOURCE_ORDER.map((type) => {
          const count = hand[type] ?? 0;
          const max = maxPerType?.(type);
          return (
            <div key={type} className={styles.pickerSlot} title={RESOURCE_LABEL[type]}>
              <span className={styles.pickerIcon} aria-hidden="true">
                {RESOURCE_ICON[type]}
              </span>
              <div className={styles.stepper}>
                <button
                  type="button"
                  aria-label={`Decrease ${RESOURCE_LABEL[type]}`}
                  onClick={() => onChange(adjust(hand, type, -1, max))}
                  disabled={count <= 0}
                >
                  -
                </button>
                <span className={styles.stepperCount}>{count}</span>
                <button
                  type="button"
                  aria-label={`Increase ${RESOURCE_LABEL[type]}`}
                  onClick={() => onChange(adjust(hand, type, 1, max))}
                  disabled={max !== undefined && count >= max}
                >
                  +
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}

export function TradePanel({
  hand,
  otherPlayers,
  bankRate = 4,
  ports = [],
  pendingTrades = [],
  onProposeBankTrade,
  onProposePortTrade,
  onProposePlayerTrade,
  onRespondTrade,
}: TradePanelProps) {
  const [mode, setMode] = useState<TradeMode>("bank_port");
  const [offered, setOffered] = useState<ResourceHand>(emptyHand());
  const [requested, setRequested] = useState<ResourceHand>(emptyHand());
  const [targetPlayerIds, setTargetPlayerIds] = useState<PlayerId[]>([]);

  const ownedMax = (type: ResourceType) => hand[type] ?? 0;

  const bestPortForOffer = useMemo(() => {
    const offeredTypes = RESOURCE_ORDER.filter((type) => (offered[type] ?? 0) > 0);
    if (offeredTypes.length !== 1) return undefined;
    const [type] = offeredTypes;
    return ports.find((p) => p.port_type === type || p.port_type === "generic");
  }, [offered, ports]);

  const canReset = () => {
    setOffered(emptyHand());
    setRequested(emptyHand());
    setTargetPlayerIds([]);
  };

  const submitBankOrPort = () => {
    if (handTotal(offered) === 0 || handTotal(requested) === 0) return;
    if (bestPortForOffer) {
      onProposePortTrade({ offered, requested });
    } else {
      onProposeBankTrade({ offered, requested });
    }
    canReset();
  };

  const submitPlayerTrade = () => {
    if (handTotal(offered) === 0 || handTotal(requested) === 0) return;
    onProposePlayerTrade({
      offered,
      requested,
      target_player_ids: targetPlayerIds.length > 0 ? targetPlayerIds : null,
    });
    canReset();
  };

  const toggleTarget = (playerId: PlayerId) => {
    setTargetPlayerIds((prev) =>
      prev.includes(playerId) ? prev.filter((id) => id !== playerId) : [...prev, playerId]
    );
  };

  return (
    <div className={styles.panel} aria-label="Trade panel">
      <div className={styles.tabs} role="tablist">
        <button
          type="button"
          role="tab"
          aria-selected={mode === "bank_port"}
          className={mode === "bank_port" ? styles.tabActive : styles.tab}
          onClick={() => setMode("bank_port")}
        >
          Bank / Port
        </button>
        <button
          type="button"
          role="tab"
          aria-selected={mode === "player"}
          className={mode === "player" ? styles.tabActive : styles.tab}
          onClick={() => setMode("player")}
        >
          Player Trade
        </button>
      </div>

      <ResourcePicker title="You give" hand={offered} maxPerType={ownedMax} onChange={setOffered} />
      <ResourcePicker title="You get" hand={requested} onChange={setRequested} />

      {mode === "bank_port" && (
        <div className={styles.hint}>
          {bestPortForOffer
            ? `Trading via ${bestPortForOffer.port_type} port at ${bestPortForOffer.rate}:1.`
            : `Bank trade rate: ${bankRate}:1 (no matching port).`}
          <button
            type="button"
            className={styles.submitButton}
            onClick={submitBankOrPort}
            disabled={handTotal(offered) === 0 || handTotal(requested) === 0}
          >
            Propose Trade
          </button>
        </div>
      )}

      {mode === "player" && (
        <div className={styles.playerTradeSection}>
          <p className={styles.pickerTitle}>Offer to</p>
          <div className={styles.targetList}>
            {otherPlayers.length === 0 && <span className={styles.hint}>No other players seated.</span>}
            {otherPlayers.map((player) => (
              <label key={player.player_id} className={styles.targetOption}>
                <input
                  type="checkbox"
                  checked={targetPlayerIds.includes(player.player_id)}
                  onChange={() => toggleTarget(player.player_id)}
                />
                {player.nickname}
              </label>
            ))}
          </div>
          <p className={styles.hintSmall}>Leave everyone unchecked to offer to all players.</p>
          <button
            type="button"
            className={styles.submitButton}
            onClick={submitPlayerTrade}
            disabled={handTotal(offered) === 0 || handTotal(requested) === 0}
          >
            Propose Trade
          </button>
        </div>
      )}

      {pendingTrades.length > 0 && (
        <div className={styles.pendingSection}>
          <p className={styles.pickerTitle}>Pending trades</p>
          <ul className={styles.pendingList}>
            {pendingTrades.map((trade) => (
              <li key={trade.trade_id} className={styles.pendingRow}>
                <span className={styles.pendingSummary}>
                  {summarizeHand(trade.offered)} {"→"} {summarizeHand(trade.requested)}
                </span>
                {onRespondTrade && (
                  <span className={styles.pendingActions}>
                    <button
                      type="button"
                      className={styles.acceptButton}
                      onClick={() => onRespondTrade({ trade_id: trade.trade_id, accept: true })}
                    >
                      Accept
                    </button>
                    <button
                      type="button"
                      className={styles.declineButton}
                      onClick={() => onRespondTrade({ trade_id: trade.trade_id, accept: false })}
                    >
                      Decline
                    </button>
                  </span>
                )}
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  );
}

function summarizeHand(hand: ResourceHand): string {
  const parts = RESOURCE_ORDER.filter((type) => (hand[type] ?? 0) > 0).map(
    (type) => `${hand[type]}${RESOURCE_ICON[type]}`
  );
  return parts.length > 0 ? parts.join(" ") : "nothing";
}
