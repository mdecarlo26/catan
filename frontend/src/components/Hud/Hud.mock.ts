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

export const MOCK_VP = {
  victoryPoints: 6,
  targetVictoryPoints: 10,
  hasLongestRoad: true,
  hasLargestArmy: false,
};

export const MOCK_OTHER_PLAYERS: PlayerSummary[] = [
  { player_id: "player-2", nickname: "Bob", seat: 1, is_connected: true, is_host: false },
  { player_id: "player-3", nickname: "Chloe", seat: 2, is_connected: false, is_host: false },
  { player_id: "player-4", nickname: "Dave", seat: 3, is_connected: true, is_host: false },
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
