/**
 * Dealer + bettor hand displays for `settings.blackjack_mode`'s
 * Blackjack-on-7 round. Pure/presentational: Game.tsx supplies the
 * masked `BlackjackRoundView` (from `ClientGameStateView.blackjack_round`)
 * plus nicknames. Per the plan's "real table" rule, every bettor's hand
 * is always fully visible; only the dealer's hole card is hidden (shown
 * as a face-down card back) until `dealer_hole_card_revealed`.
 */
import type { BlackjackRoundView, Card, PlayerId } from "../../types/protocol";
import { handTotal } from "../../state/blackjackHandValue";
import styles from "./BlackjackHands.module.css";

const SUIT_SYMBOL: Record<Card["suit"], string> = {
  spades: "♠",
  hearts: "♥",
  diamonds: "♦",
  clubs: "♣",
};

const SUIT_COLOR: Record<Card["suit"], string> = {
  spades: "#1a1a1a",
  clubs: "#1a1a1a",
  hearts: "#a83232",
  diamonds: "#a83232",
};

function CardFace({ card }: { card: Card }) {
  return (
    <span className={styles.card} style={{ color: SUIT_COLOR[card.suit] }}>
      {card.rank}
      {SUIT_SYMBOL[card.suit]}
    </span>
  );
}

function CardBack() {
  return <span className={`${styles.card} ${styles.cardBack}`}>{"\u{1F0A0}"}</span>;
}

function nameFor(playerId: PlayerId, nicknames: Record<PlayerId, string>): string {
  return nicknames[playerId] ?? playerId;
}

function stakeLabel(stake: BlackjackRoundView["participants"][string]["stake"]): string {
  if (stake.kind === "resources") {
    const parts = Object.entries(stake.resources ?? {})
      .filter(([, amount]) => (amount ?? 0) > 0)
      .map(([resource, amount]) => `${amount} ${resource}`);
    return parts.length > 0 ? parts.join(", ") : "resources";
  }
  return stake.kind === "road" ? "a road" : `a ${stake.kind}`;
}

export interface BlackjackHandsProps {
  round: BlackjackRoundView;
  /** player_id -> nickname, for display only. */
  nicknames: Record<PlayerId, string>;
}

export function BlackjackHands({ round, nicknames }: BlackjackHandsProps) {
  const dealerRevealed = round.dealer_hole_card_revealed;
  const dealerCards: Card[] = dealerRevealed
    ? round.dealer_hand
    : round.dealer_up_card
      ? [round.dealer_up_card]
      : [];

  return (
    <div className={styles.table}>
      <div className={styles.hand}>
        <div className={styles.handHeader}>
          <strong>Dealer: {nameFor(round.dealer_id, nicknames)}</strong>
          {dealerRevealed && <span className={styles.total}>{handTotal(round.dealer_hand)}</span>}
        </div>
        <div className={styles.cards}>
          {dealerCards.map((card, i) => (
            <CardFace key={i} card={card} />
          ))}
          {!dealerRevealed && round.dealer_up_card && <CardBack />}
        </div>
      </div>

      {Object.entries(round.participants).map(([playerId, participant]) => {
        const isUp = round.status === "bettor_turn" && round.bettor_queue[0] === playerId;
        return (
          <div key={playerId} className={`${styles.hand} ${isUp ? styles.handActive : ""}`}>
            <div className={styles.handHeader}>
              <strong>{nameFor(playerId, nicknames)}</strong>
              <span className={styles.badge} data-status={participant.status}>
                {participant.status}
              </span>
              {participant.hand.length > 0 && (
                <span className={styles.total}>{handTotal(participant.hand)}</span>
              )}
            </div>
            <div className={styles.cards}>
              {participant.hand.map((card, i) => (
                <CardFace key={i} card={card} />
              ))}
            </div>
            <div className={styles.stake}>Bet: {stakeLabel(participant.stake)}</div>
          </div>
        );
      })}

      {round.responses_pending.length > 0 && (
        <p className={styles.waiting}>
          Waiting on: {round.responses_pending.map((pid) => nameFor(pid, nicknames)).join(", ")}
        </p>
      )}
    </div>
  );
}
