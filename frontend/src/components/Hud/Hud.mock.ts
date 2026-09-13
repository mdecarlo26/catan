/**
 * Realistic mock fixtures for the Hud component group. These stand in
 * for what will, in Wave 2, be derived from gameStore's mirrored
 * ClientGameStateView (see src/types/protocol.ts).
 */
import type { DevCardHand, PlayerSummary, ResourceHand, TradeOfferedPayload } from "../../types/protocol";
import type { PortAccess } from "./TradePanel";
import type { TurnLogEntry } from "./TurnLog";

export const MOCK_RESOURCE_HAND: ResourceHand = {
  brick: 3,
  lumber: 2,
  ore: 1,
  grain: 4,
  wool: 0,
};

export const MOCK_DEV_CARD_HAND: DevCardHand = {
  knight: 2,
  road_building: 1,
  year_of_plenty: 0,
  monopoly: 1,
  victory_point: 1,
};

// ---------------------------------------------------------------------
// ResourceHandFan / DevCardHand fixtures -- exercise empty, small (1-3),
// and large (15+) fanned hands, plus the dev-card locked ("bought this
// turn") and victory-point visual states.
// ---------------------------------------------------------------------

export const MOCK_RESOURCE_HAND_EMPTY: ResourceHand = {};

export const MOCK_RESOURCE_HAND_SMALL: ResourceHand = {
  brick: 1,
  lumber: 1,
};

export const MOCK_RESOURCE_HAND_LARGE: ResourceHand = {
  brick: 4,
  lumber: 4,
  ore: 3,
  grain: 5,
  wool: 4,
};

export const MOCK_DEV_CARD_HAND_EMPTY: DevCardHand = {};

export const MOCK_DEV_CARD_HAND_SMALL: DevCardHand = {
  knight: 1,
  monopoly: 1,
};

export const MOCK_DEV_CARD_HAND_LARGE: DevCardHand = {
  knight: 6,
  road_building: 3,
  year_of_plenty: 2,
  monopoly: 3,
  victory_point: 2,
};

/** No dev cards locked -- everything eligible in `MOCK_DEV_CARD_HAND` was
 * bought in a prior turn. */
export const MOCK_DEV_CARDS_BOUGHT_THIS_TURN_NONE: DevCardHand = {};

/**
 * Paired with `MOCK_DEV_CARD_HAND_LARGE`: 2 of the 6 knights and both
 * year-of-plenty cards were just bought this turn, so those instances
 * render locked/dimmed while the rest of the hand (including the 2
 * victory-point cards, which are never playable regardless) stays
 * interactive.
 */
export const MOCK_DEV_CARDS_BOUGHT_THIS_TURN: DevCardHand = {
  knight: 2,
  year_of_plenty: 2,
};

export const MOCK_VP = {
  victoryPoints: 6,
  targetVictoryPoints: 10,
  hasLongestRoad: true,
  hasLargestArmy: false,
};

export const MOCK_OTHER_PLAYERS: PlayerSummary[] = [
  { player_id: "player-2", nickname: "Bob", seat: 1, is_connected: true, is_host: false, is_bot: false },
  { player_id: "player-3", nickname: "Chloe", seat: 2, is_connected: false, is_host: false, is_bot: false },
  { player_id: "player-4", nickname: "Dave", seat: 3, is_connected: true, is_host: false, is_bot: false },
];

export const MOCK_PORTS: PortAccess[] = [{ port_type: "grain", rate: 2 }];

export const MOCK_PENDING_TRADES: TradeOfferedPayload[] = [
  {
    trade_id: "trade-1",
    proposer: "player-2",
    offered: { brick: 2 },
    requested: { ore: 1 },
    target_player_ids: null,
  },
];

export const MOCK_TURN_LOG: TurnLogEntry[] = [
  { id: "log-1", timestamp: Date.now() - 5 * 60_000, message: "Alice rolled 8." },
  { id: "log-2", timestamp: Date.now() - 4 * 60_000, message: "Alice built a settlement." },
  { id: "log-3", timestamp: Date.now() - 3 * 60_000, message: "Bob traded 3 lumber for 1 ore with the bank." },
  { id: "log-4", timestamp: Date.now() - 2 * 60_000, message: "Chloe rolled 7 -- robber moved to (0, 0)." },
  { id: "log-5", timestamp: Date.now() - 1 * 60_000, message: "Dave played a Knight card." },
  { id: "log-6", timestamp: Date.now(), message: "Alice's turn." },
];
