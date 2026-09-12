/**
 * Client-side mirror of backend/app/game/rules/blackjack.py's
 * `hand_value` -- UI-display only (a running total next to each hand),
 * never authoritative: the server is the sole source of truth for who
 * won/lost/pushed (see BLACKJACK_HAND_UPDATED / BLACKJACK_ROUND_RESOLVED),
 * this just saves the viewer from adding cards up by hand.
 */
import type { Card } from "../types/protocol";

const FACE_RANKS: ReadonlySet<Card["rank"]> = new Set(["J", "Q", "K"]);

export function handTotal(cards: readonly Card[]): number {
  let total = 0;
  let aces = 0;
  for (const card of cards) {
    if (card.rank === "A") {
      total += 11;
      aces += 1;
    } else if (FACE_RANKS.has(card.rank)) {
      total += 10;
    } else {
      total += Number(card.rank);
    }
  }
  while (total > 21 && aces > 0) {
    total -= 10;
    aces -= 1;
  }
  return total;
}

export function isBust(cards: readonly Card[]): boolean {
  return handTotal(cards) > 21;
}
