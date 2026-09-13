/**
 * Shared fanned parabolic-arc layout math for hand-of-cards components
 * (`ResourceHandFan`, `DevCardHand`). Pure function: given how many cards
 * are in a hand and which index a particular card occupies, returns the
 * 2D offset + rotation to lay that card out along a shallow parabolic fan
 * -- cards near the center of the fan sit lower, cards toward the edges
 * lift slightly and rotate outward, matching the reference prototype's
 * "poster style" resource-card treatment.
 *
 * Both the horizontal spread and the per-card rotation are clamped so the
 * fan degrades gracefully at the extremes instead of overlapping cards
 * illegibly (very high counts, 15-20+) or spreading them absurdly wide
 * apart (very low counts, 1-2).
 */

export interface CardFanOptions {
  /** Width of one card, in px -- drives the horizontal spacing/overlap math. */
  cardWidth: number;
  /** Max total angular spread (degrees), from the leftmost to the rightmost card. */
  maxSpreadDegrees: number;
  /**
   * Max total horizontal width (px) the whole fan may occupy, edge card
   * center to edge card center. Defaults to 6x `cardWidth`.
   */
  maxFanWidth?: number;
  /**
   * Floor on adjacent-card spacing, as a fraction of `cardWidth` (0-1).
   * Keeps very large hands from fully occluding one another. Default 0.22.
   */
  minOverlapFraction?: number;
  /**
   * Ceiling on adjacent-card spacing, as a fraction of `cardWidth` (0-1).
   * Keeps very small hands (2-3 cards) from spreading absurdly wide.
   * Default 0.92.
   */
  maxSpacingFraction?: number;
  /** How far the fan's center dips down (px) at full spread. Default 16. */
  arcDepth?: number;
  /** How far the outermost cards lift up (px) relative to the center. Default 10. */
  edgeLift?: number;
}

export interface CardFanTransform {
  /** Horizontal offset from the fan's center, in px. */
  x: number;
  /** Vertical offset from the fan's center, in px (positive = lower on screen). */
  y: number;
  /** Rotation applied to the card, in degrees (negative = tilts left). */
  rotationDeg: number;
}

const DEFAULT_MAX_FAN_WIDTH_MULTIPLIER = 6;
const DEFAULT_MIN_OVERLAP_FRACTION = 0.22;
const DEFAULT_MAX_SPACING_FRACTION = 0.92;
const DEFAULT_ARC_DEPTH = 16;
const DEFAULT_EDGE_LIFT = 10;
/** Card count at which the fan reaches its full `maxSpreadDegrees`; the
 * spread stays clamped at that max for any larger hand. */
const SPREAD_REACHES_FULL_AT_COUNT = 11;

function clamp(value: number, min: number, max: number): number {
  return Math.min(max, Math.max(min, value));
}

/** A single card's fan-relative offset with no rotation or displacement --
 * used for empty/degenerate inputs so callers never need a special case. */
const IDENTITY_TRANSFORM: CardFanTransform = { x: 0, y: 0, rotationDeg: 0 };

/**
 * Compute the layout transform for one card in a fanned hand.
 *
 * @param count total number of cards in the hand
 * @param index zero-based position of this card within the hand (0..count-1)
 * @param options layout tuning -- see `CardFanOptions`
 */
export function computeCardFan(count: number, index: number, options: CardFanOptions): CardFanTransform {
  if (count <= 1 || index < 0 || index >= count) {
    return IDENTITY_TRANSFORM;
  }

  const {
    cardWidth,
    maxSpreadDegrees,
    maxFanWidth = cardWidth * DEFAULT_MAX_FAN_WIDTH_MULTIPLIER,
    minOverlapFraction = DEFAULT_MIN_OVERLAP_FRACTION,
    maxSpacingFraction = DEFAULT_MAX_SPACING_FRACTION,
    arcDepth = DEFAULT_ARC_DEPTH,
    edgeLift = DEFAULT_EDGE_LIFT,
  } = options;

  // -1 (leftmost) .. 0 (center) .. 1 (rightmost)
  const maxCentered = (count - 1) / 2;
  const centered = index - maxCentered;
  const norm = centered / maxCentered;

  const anglePerCard = maxSpreadDegrees / SPREAD_REACHES_FULL_AT_COUNT;
  const totalSpreadDeg = clamp((count - 1) * anglePerCard, 0, maxSpreadDegrees);
  const rotationDeg = norm * (totalSpreadDeg / 2);

  const spacing = clamp(
    maxFanWidth / (count - 1),
    cardWidth * minOverlapFraction,
    cardWidth * maxSpacingFraction,
  );
  const x = centered * spacing;

  // Parabola: center (norm=0) dips down by `arcDepth`; edges (norm=+-1)
  // lift up by `edgeLift` relative to the center.
  const y = arcDepth - (arcDepth + edgeLift) * norm * norm;

  return { x, y, rotationDeg };
}
