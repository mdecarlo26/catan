/**
 * Realistic mock fixtures for the Lobby component group. These stand in
 * for what will, in Wave 2, come from gameStore (mirroring
 * RoomStatePayload / SettingsUpdatedPayload over the WS protocol).
 */
import type { GameSettings, PlayerId, PlayerSummary } from "../../types/protocol";
import type { LobbySettingFieldMeta } from "./SettingsForm";

/**
 * Mirrors backend/app/game/settings_schema.py's SETTINGS_REGISTRY 1:1
 * (same key/type/default/description/min/max ordering), with `options`
 * added to `board_layout` purely as UI-only metadata (see
 * SettingsForm.tsx's `LobbySettingFieldMeta` doc comment) since the wire
 * registry intentionally leaves that field an open string.
 */
export const SETTINGS_REGISTRY_MOCK: LobbySettingFieldMeta[] = [
  {
    key: "player_count",
    type: "int",
    default: 4,
    description: "Number of players in the game.",
    min_value: 2,
    max_value: 8,
  },
  {
    key: "victory_points_target",
    type: "int",
    default: 10,
    description: "Victory points required to win the game.",
    min_value: 3,
    max_value: null,
  },
  {
    key: "board_layout",
    type: "string",
    default: "random",
    description:
      "Board layout / generation strategy, selected from the registry of layouts available for the current player count.",
    options: ["random", "fixed", "standard", "expansion_5_6", "extended_7_8", "two_player"],
  },
  {
    key: "rush_mode",
    type: "bool",
    default: false,
    description:
      "Alternate, faster initial-setup strategy. Exact behavior is still being designed; currently a placeholder toggle.",
  },
  {
    key: "nuke_mode",
    type: "bool",
    default: false,
    description:
      "Custom house rule: a player holding 2 of each resource may destroy an opponent's road or settlement/city.",
  },
  {
    key: "special_build_phase",
    type: "bool",
    default: false,
    description:
      "Extra build-only round between turns (official 5-6 player expansion rule). Auto-enabled when player count is 5 or more unless explicitly overridden here.",
  },
  {
    key: "discard_limit",
    type: "int",
    default: 7,
    description: "Card count threshold that triggers a mandatory discard when a 7 is rolled.",
    min_value: 1,
    max_value: null,
  },
];

export const MOCK_GAME_SETTINGS: GameSettings = {
  player_count: 4,
  victory_points_target: 10,
  board_layout: "random",
  rush_mode: false,
  nuke_mode: false,
  special_build_phase: null,
  discard_limit: 7,
};

export const MOCK_VIEWER_PLAYER_ID: PlayerId = "player-1";

export const MOCK_PLAYERS: PlayerSummary[] = [
  {
    player_id: "player-1",
    nickname: "Alice",
    seat: 0,
    is_connected: true,
    is_host: true,
  },
  {
    player_id: "player-2",
    nickname: "Bob",
    seat: 1,
    is_connected: true,
    is_host: false,
  },
  {
    player_id: "player-3",
    nickname: "Chloe",
    seat: 2,
    is_connected: false,
    is_host: false,
  },
  {
    player_id: "player-4",
    nickname: "Dave",
    seat: 3,
    is_connected: true,
    is_host: false,
  },
];
