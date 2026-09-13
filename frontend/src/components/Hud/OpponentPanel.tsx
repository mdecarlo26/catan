/**
 * One opponent's card in the in-game HUD: nickname, seat badge, VP
 * count, a fanned stack of face-down card backs sized by their resource
 * card count (individual resource cards are never revealed -- only the
 * count is), a dev-card-count badge (never fanned -- opponents'
 * individual dev cards are never revealed either), bot/disconnected
 * badges, and an optional gold ring for "it's this player's turn."
 *
 * Pure/presentational -- `OpponentInfo` is a subset of `MaskedPlayerView`
 * (protocol.ts) so callers can spread a masked player entry straight in.
 * Bot/disconnected badges reuse PlayerList.module.css's class names so
 * the lobby roster and in-game opponent rail read as the same visual
 * language.
 */
import type { MaskedPlayerView } from "../../types/protocol";
import { playerColor } from "../../board/theme";
import lobbyStyles from "../Lobby/PlayerList.module.css";
import styles from "./OpponentPanel.module.css";

/** The subset of a masked player entry an opponent panel needs to render. */
export type OpponentInfo = Pick<
  MaskedPlayerView,
  | "player_id"
  | "nickname"
  | "seat"
  | "is_bot"
  | "is_connected"
  | "resource_card_count"
  | "dev_card_count"
  | "victory_points"
>;

export interface OpponentPanelProps extends OpponentInfo {
  /** Highlights the panel with a gold ring when true. */
  isCurrentTurn?: boolean;
  /** Palette index for the accent border/card-back color. Defaults to `seat`. */
  colorIndex?: number;
}

/** Beyond this many drawn card backs, collapse the rest into a "+N" chip. */
const MAX_DRAWN_BACKS = 7;
/** Horizontal overlap step (px) between successive fanned card backs. */
const FAN_STEP_PX = 12;

function toHexColor(color: number): string {
  return `#${color.toString(16).padStart(6, "0")}`;
}

/** Fan rotation (deg) for one card back among `total`, centered around 0deg. */
function fanRotation(index: number, total: number): number {
  if (total <= 1) return 0;
  const spread = Math.min(38, total * 7);
  const step = spread / (total - 1);
  return -spread / 2 + step * index;
}

export function OpponentPanel({
  player_id,
  nickname,
  seat,
  is_bot,
  is_connected,
  resource_card_count,
  dev_card_count,
  victory_points,
  isCurrentTurn = false,
  colorIndex,
}: OpponentPanelProps) {
  const accent = toHexColor(playerColor(colorIndex ?? seat));
  const drawnBacks = Math.min(resource_card_count, MAX_DRAWN_BACKS);
  const overflow = Math.max(0, resource_card_count - MAX_DRAWN_BACKS);

  return (
    <div
      className={isCurrentTurn ? `${styles.panel} ${styles.currentTurn}` : styles.panel}
      style={{ borderColor: accent }}
      data-player-id={player_id}
      aria-label={`${nickname}, seat ${seat}, ${victory_points} victory points`}
    >
      <div className={styles.header}>
        <span className={styles.seat} style={{ color: accent }}>
          #{seat}
        </span>
        <span className={styles.nickname}>{nickname}</span>
        <span className={styles.vp} title="Victory points">
          {victory_points} VP
        </span>
      </div>

      <div className={styles.badgeRow}>
        {is_bot && (
          <span className={lobbyStyles.botBadge} title="Bot player">
            {"\u{1F916}"} BOT
          </span>
        )}
        {!is_connected && (
          <span className={lobbyStyles.disconnected} title="Disconnected">
            <span className={lobbyStyles.statusDot} aria-hidden="true" />
            Disconnected
          </span>
        )}
      </div>

      <div className={styles.cardsRow}>
        <div
          className={styles.fan}
          style={{ width: `${Math.max(1, drawnBacks) * FAN_STEP_PX + 20}px` }}
          aria-label={`${resource_card_count} resource cards`}
        >
          {resource_card_count === 0 ? (
            <span className={styles.emptyFan}>0</span>
          ) : (
            Array.from({ length: drawnBacks }, (_, i) => (
              <span
                key={i}
                className={styles.cardBack}
                style={{
                  left: `${i * FAN_STEP_PX}px`,
                  transform: `rotate(${fanRotation(i, drawnBacks).toFixed(1)}deg)`,
                  backgroundColor: accent,
                  zIndex: i,
                }}
              />
            ))
          )}
          {overflow > 0 && <span className={styles.overflowChip}>+{overflow}</span>}
        </div>
      </div>

      <div className={styles.devCardBadge} title="Development cards">
        <span className={styles.devCardIcon} aria-hidden="true">
          {"\u{1F0A0}"}
        </span>
        {dev_card_count}
      </div>
    </div>
  );
}
