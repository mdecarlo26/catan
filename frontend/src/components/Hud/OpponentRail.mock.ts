/**
 * Mock fixtures for OpponentPanel/OpponentRail, for standalone visual
 * review at every supported opponent count (1-7, i.e. 2-8 total
 * players) -- see Hud.mock.ts for the equivalent convention used by the
 * rest of the Hud component group.
 */
import type { OpponentInfo } from "./OpponentPanel";

/**
 * A pool of 7 distinct opponents (max supported at an 8-player table),
 * covering: bots, a disconnected player, a card count of 0, and card
 * counts above the 7-back fan cap (to exercise the "+N" overflow chip).
 */
const MOCK_OPPONENT_POOL: OpponentInfo[] = [
  {
    player_id: "opp-bob",
    nickname: "Bob",
    seat: 1,
    is_bot: false,
    is_connected: true,
    resource_card_count: 4,
    dev_card_count: 1,
    victory_points: 3,
  },
  {
    player_id: "opp-chloe",
    nickname: "Chloe",
    seat: 2,
    is_bot: false,
    is_connected: false,
    resource_card_count: 9,
    dev_card_count: 2,
    victory_points: 5,
  },
  {
    player_id: "opp-dave-bot",
    nickname: "Dave (Bot)",
    seat: 3,
    is_bot: true,
    is_connected: true,
    resource_card_count: 0,
    dev_card_count: 0,
    victory_points: 2,
  },
  {
    player_id: "opp-erin",
    nickname: "Erin",
    seat: 4,
    is_bot: false,
    is_connected: true,
    resource_card_count: 7,
    dev_card_count: 3,
    victory_points: 8,
  },
  {
    player_id: "opp-frank-bot",
    nickname: "Frank (Bot)",
    seat: 5,
    is_bot: true,
    is_connected: true,
    resource_card_count: 12,
    dev_card_count: 1,
    victory_points: 4,
  },
  {
    player_id: "opp-grace",
    nickname: "Grace",
    seat: 6,
    is_bot: false,
    is_connected: true,
    resource_card_count: 2,
    dev_card_count: 0,
    victory_points: 6,
  },
  {
    player_id: "opp-hank",
    nickname: "Hank",
    seat: 7,
    is_bot: false,
    is_connected: true,
    resource_card_count: 15,
    dev_card_count: 4,
    victory_points: 7,
  },
];

/** Opponent arrays at each supported opponent count (1 through 7). */
export const MOCK_OPPONENTS_1: OpponentInfo[] = MOCK_OPPONENT_POOL.slice(0, 1);
export const MOCK_OPPONENTS_2: OpponentInfo[] = MOCK_OPPONENT_POOL.slice(0, 2);
export const MOCK_OPPONENTS_3: OpponentInfo[] = MOCK_OPPONENT_POOL.slice(0, 3);
export const MOCK_OPPONENTS_4: OpponentInfo[] = MOCK_OPPONENT_POOL.slice(0, 4);
export const MOCK_OPPONENTS_5: OpponentInfo[] = MOCK_OPPONENT_POOL.slice(0, 5);
export const MOCK_OPPONENTS_7: OpponentInfo[] = MOCK_OPPONENT_POOL.slice(0, 7);

/**
 * player_id to pass as `currentTurnPlayerId` in each fixture, to exercise
 * the gold current-turn ring -- present in every slice above (seat 1, the
 * first entry in the pool) so it's visible regardless of opponent count
 * or layout mode.
 */
export const MOCK_CURRENT_TURN_PLAYER_ID = "opp-bob";
