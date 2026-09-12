"""Every Server -> Client WebSocket event: type enum + payload schemas.

Envelope
--------
Every outbound message has the shape ``{type, payload, seq, ts}``:

- ``type`` identifies the event (see `EventType`) and is also the
  pydantic discriminator tag on each wrapper model below.
- ``payload`` is the event-specific data.
- ``seq`` is a monotonically increasing integer assigned per room by the
  server (see `app.core.room.Room`). Clients track the last `seq` they
  saw; a detected gap (e.g. after a reconnect) means "request a fresh
  `STATE_SNAPSHOT` rather than trying to replay deltas" -- state updates
  are always sent as full masked snapshots, never diffs, so this is safe.
- ``ts`` is the server's unix timestamp (seconds) when the event was
  emitted, used for the turn log UI.

`ServerEvent` is the discriminated union of every concrete event below.

Wire safety of board/game-state data
-------------------------------------
`app.game.board.Board` and `app.game.state.GameState` are the backend's
*internal* representations and use tuple-keyed dicts
(`dict[HexCoord, HexTile]` etc.) that are not valid JSON. This module
defines separate, JSON-safe "wire view" shapes (`WireBoardView` and
friends, `ClientGameStateView`) that flatten those into lists of tagged
records instead. `app.game.serialization.to_client_view()` is
responsible for building a `ClientGameStateView` from a `GameState` --
masking opponents' hands/dev cards down to counts and the bank's dev
card pile down to a count in the process. `GAME_STARTED` and
`STATE_SNAPSHOT` are the two events that carry this view.
"""

from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field

from app.game.board import (
    Board,
    BuildingType,
    EdgeId,
    HexCoord,
    Port,
    PlayerId,
    Terrain,
    VertexId,
)
from app.game.players import DevCardType, ResourceHand, ResourceType
from app.game.settings_schema import GameSettings
from app.game.state import (
    AwaitingDiscard,
    BlackjackStake,
    Card,
    PendingAction,
    Phase,
    RushRobberPending,
)


class EventType(str, Enum):
    SESSION_ESTABLISHED = "SESSION_ESTABLISHED"
    ROOM_STATE = "ROOM_STATE"
    PLAYER_JOINED = "PLAYER_JOINED"
    PLAYER_LEFT = "PLAYER_LEFT"
    PLAYER_KICKED = "PLAYER_KICKED"
    PLAYER_DISCONNECTED = "PLAYER_DISCONNECTED"
    PLAYER_RECONNECTED = "PLAYER_RECONNECTED"
    SETTINGS_UPDATED = "SETTINGS_UPDATED"
    GAME_STARTED = "GAME_STARTED"
    STATE_SNAPSHOT = "STATE_SNAPSHOT"
    DICE_ROLLED = "DICE_ROLLED"
    RESOURCES_DISTRIBUTED = "RESOURCES_DISTRIBUTED"
    ROBBER_MOVED = "ROBBER_MOVED"
    RESOURCE_STOLEN = "RESOURCE_STOLEN"
    DISCARD_REQUIRED = "DISCARD_REQUIRED"
    TRADE_OFFERED = "TRADE_OFFERED"
    TRADE_RESOLVED = "TRADE_RESOLVED"
    DEV_CARD_COUNT_CHANGED = "DEV_CARD_COUNT_CHANGED"
    NUKE_DROPPED = "NUKE_DROPPED"
    BLACKJACK_ROUND_STARTED = "BLACKJACK_ROUND_STARTED"
    BLACKJACK_BET_PLACED = "BLACKJACK_BET_PLACED"
    BLACKJACK_BET_DECLINED = "BLACKJACK_BET_DECLINED"
    BLACKJACK_HAND_UPDATED = "BLACKJACK_HAND_UPDATED"
    BLACKJACK_DEALER_REVEALED = "BLACKJACK_DEALER_REVEALED"
    BLACKJACK_ROUND_RESOLVED = "BLACKJACK_ROUND_RESOLVED"
    LONGEST_ROAD_CHANGED = "LONGEST_ROAD_CHANGED"
    LARGEST_ARMY_CHANGED = "LARGEST_ARMY_CHANGED"
    TURN_TIMER_EXPIRED = "TURN_TIMER_EXPIRED"
    GAME_OVER = "GAME_OVER"
    ERROR = "ERROR"


class EmptyPayload(BaseModel):
    """Payload for events that carry no data beyond the envelope type."""


# ---------------------------------------------------------------------
# Shared sub-shapes
# ---------------------------------------------------------------------


class PlayerSummary(BaseModel):
    """Public (non-hidden-information) info about a seated player, used
    in lobby/roster events. Distinct from the full `PlayerState` (which
    has hand/dev-card contents) and from `MaskedPlayerView` (the
    in-game, per-recipient-masked equivalent used in `STATE_SNAPSHOT`).
    """

    player_id: PlayerId
    nickname: str
    seat: int
    is_connected: bool
    is_host: bool


class MaskedPlayerView(BaseModel):
    """Per-player info as seen by one specific recipient inside a
    `ClientGameStateView`. Every player's public fields are always
    populated; `hand` and `dev_cards` are populated only in the entry
    belonging to the viewing player (`ClientGameStateView.viewer_player_id`)
    and are `None` for every other entry -- opponents' hidden information
    is instead summarized by `resource_card_count` / `dev_card_count`,
    which are always accurate totals.
    """

    player_id: PlayerId
    nickname: str
    seat: int
    is_connected: bool
    victory_points: int
    knights_played: int
    has_longest_road: bool
    has_largest_army: bool

    resource_card_count: int
    dev_card_count: int

    #: Populated only for the viewer's own entry.
    hand: ResourceHand | None = None
    #: Populated only for the viewer's own entry.
    dev_cards: dict[DevCardType, int] | None = None


class WireHexTile(BaseModel):
    coord: HexCoord
    terrain: Terrain
    number_token: int | None


class WireVertexBuilding(BaseModel):
    vertex_id: VertexId
    player_id: PlayerId
    building_type: BuildingType


class WireRoad(BaseModel):
    edge_id: EdgeId
    player_id: PlayerId


class WireBoardView(BaseModel):
    """JSON-safe flattening of `app.game.board.Board`: the internal model
    keys collections by tuple ids (`HexCoord`/`VertexId`/`EdgeId`), which
    are not valid JSON object keys, so the wire view instead uses lists
    of records that each carry their id as an ordinary field.
    """

    hexes: list[WireHexTile]
    ports: list[Port]
    buildings: list[WireVertexBuilding]
    roads: list[WireRoad]
    robber_hex: HexCoord


class BlackjackParticipantView(BaseModel):
    """One bettor's public state within `BlackjackRoundView`. Always
    fully visible to every player -- per the plan, all bettors' hands are
    public at a real table; only the dealer's hole card is ever hidden
    (see `BlackjackRoundView.dealer_hole_card`).
    """

    stake: BlackjackStake
    hand: list[Card]
    status: Literal["playing", "stood", "busted"]


class BlackjackRoundView(BaseModel):
    """Masked wire view of `app.game.state.BlackjackRoundState`, built by
    `app.serialization.to_client_view` -- identical for every recipient
    (nobody's own bet/hand needs unmasking the way `MaskedPlayerView`
    does), except that the dealer's hole card is never included before
    `dealer_hole_card_revealed`. The round's `deck` is never exposed at
    all -- card order/remaining composition would let a client predict
    future draws.
    """

    dealer_id: PlayerId
    #: The dealer's first-dealt card -- always visible once dealt.
    #: `None` only while `status == "betting"` (nobody's been dealt yet).
    dealer_up_card: Card | None
    #: The dealer's second (hole) card. `None` until
    #: `dealer_hole_card_revealed` is `True`, at which point it's the
    #: dealer's actual final second card (auto-play may have added more
    #: cards beyond it -- see `dealer_hand` below).
    dealer_hole_card: Card | None
    dealer_hole_card_revealed: bool
    #: The dealer's full hand, revealed-and-auto-played. Empty until
    #: `dealer_hole_card_revealed` -- before that, `dealer_up_card` /
    #: `dealer_hole_card` above are the only dealer card info exposed.
    dealer_hand: list[Card]
    status: Literal["betting", "bettor_turn"]
    responses_pending: list[PlayerId]
    participants: dict[PlayerId, BlackjackParticipantView]
    bettor_queue: list[PlayerId]


class ClientGameStateView(BaseModel):
    """The masked, per-recipient view of `app.game.state.GameState`
    produced by `app.game.serialization.to_client_view()`. Opponents'
    hands and dev cards are reduced to counts only (via
    `MaskedPlayerView`); the bank's dev card pile is reduced to a count.
    """

    room_code: str
    phase: Phase
    settings: GameSettings
    turn_order: list[PlayerId]
    current_player_index: int
    last_dice_roll: tuple[int, int] | None

    board: WireBoardView

    bank_resource_counts: ResourceHand
    bank_dev_card_count: int

    #: player_id -> that player's masked view.
    players: dict[PlayerId, MaskedPlayerView]

    longest_road_holder: PlayerId | None
    largest_army_holder: PlayerId | None
    pending: PendingAction | None

    #: While `phase == "special_build"`: the players who still owe a
    #: special-build mini-turn this round, in the order they'll take it --
    #: `special_build_queue[0]` is whoever may currently act. Empty
    #: otherwise. Mirrors `app.game.state.GameState.special_build_queue`;
    #: see its docstring for why this (not `current_player_index`) is
    #: what conveys "whose special build turn is it" to clients.
    special_build_queue: list[PlayerId] = Field(default_factory=list)

    #: While `phase == "blackjack_round"`: the masked view of
    #: `app.game.state.GameState.blackjack_round` (dealer's hole card
    #: hidden until reveal, deck never exposed). `None` at every other
    #: phase. See `BlackjackRoundView`.
    blackjack_round: BlackjackRoundView | None = None

    #: Rush-mode-only concurrent obligations -- mirror
    #: `app.game.state.GameState.rush_pending_discard` /
    #: `rush_pending_robber` 1:1 (same models, safe to expose as-is: see
    #: `app.game.serialization`'s "what is not masked here" note for why
    #: `pending`'s equivalent sub-shapes are already public-safe). `None`
    #: for both outside rush mode, or whenever nobody currently owes
    #: either. The frontend renders the discard/robber-move UI from these
    #: (instead of `pending`) whenever `settings.rush_mode` is on, and can
    #: show "who's currently handling the robber" from
    #: `rush_pending_robber.actor` without that blocking anyone else's UI.
    rush_pending_discard: AwaitingDiscard | None = None
    rush_pending_robber: RushRobberPending | None = None

    #: Unix timestamp (seconds) of the most recent dice roll -- mirrors
    #: `app.game.state.GameState.last_dice_roll_ts`. Combined with
    #: `settings.rush_roll_interval_seconds`, lets a rush-mode client
    #: render a live "next auto-roll in Ns" countdown without needing a
    #: server-push tick every second.
    last_dice_roll_ts: float | None = None

    #: Whose masked view this is -- i.e. which player's `hand` /
    #: `dev_cards` are unmasked in `players` above.
    viewer_player_id: PlayerId


# ---------------------------------------------------------------------
# Payload models
# ---------------------------------------------------------------------


class SessionEstablishedPayload(BaseModel):
    """Sent to exactly one socket, immediately after a successful
    `JOIN_ROOM` handshake: the freshly minted `player_id` and reconnect
    `token` that connection must persist client-side (per
    `frontend/src/api/session.ts`'s `StoredSession`) to reconnect via
    `/ws/{room_code}?token=...` later. Not part of the original
    Wave-0/Wave-1 frozen contract -- added during backend/WS integration
    to close a real gap: nothing else in this protocol ever hands a
    freshly-joined (non-host) player their own `player_id`/`token`. See
    `app.api.websocket`'s module docstring for the full rationale,
    including why this reuses `Room.last_seq` rather than consuming a
    new `seq`.
    """

    player_id: PlayerId
    token: str
    room_code: str


class RoomStatePayload(BaseModel):
    """Full lobby roster + settings snapshot, sent on join and whenever
    lobby membership changes.
    """

    room_code: str
    host_player_id: PlayerId
    players: list[PlayerSummary]
    settings: GameSettings
    phase: Phase


class PlayerJoinedPayload(BaseModel):
    player: PlayerSummary


class PlayerLeftPayload(BaseModel):
    player_id: PlayerId


class PlayerKickedPayload(BaseModel):
    player_id: PlayerId
    reason: str | None = None


class PlayerDisconnectedPayload(BaseModel):
    player_id: PlayerId


class PlayerReconnectedPayload(BaseModel):
    player_id: PlayerId


class SettingsUpdatedPayload(BaseModel):
    settings: GameSettings


class GameStartedPayload(BaseModel):
    """Signals the LOBBY -> SETUP transition. The full initial board/game
    state follows immediately as a `STATE_SNAPSHOT`, per-recipient
    masked; it is not duplicated here.
    """

    turn_order: list[PlayerId]


class StateSnapshotPayload(BaseModel):
    state: ClientGameStateView


class DiceRolledPayload(BaseModel):
    #: `None` for a rush-mode auto-roll (system-driven, nobody "rolled"
    #: it) -- see `app.game.rules_engine.apply_rush_auto_roll`. Always
    #: populated for a normal-mode, client-invoked `ROLL_DICE`.
    player_id: PlayerId | None
    die1: int
    die2: int
    total: int


class ResourcesDistributedPayload(BaseModel):
    """Resources granted to each player as a result of the most recent
    dice roll. A player absent from `distribution` (or present with all
    zero counts) received nothing.
    """

    distribution: dict[PlayerId, ResourceHand]


class RobberMovedPayload(BaseModel):
    actor: PlayerId
    hex: HexCoord


class ResourceStolenPayload(BaseModel):
    actor: PlayerId
    victim: PlayerId
    #: The stolen resource type. Populated in the copy of this event sent
    #: to `actor` and `victim`; `None` in the copy broadcast to every
    #: other player, who are only told that a theft happened (matches
    #: standard Catan's hidden-steal convention).
    resource: ResourceType | None


class DiscardRequiredPayload(BaseModel):
    """player_id -> number of cards that player must still discard.
    Mirrors `app.game.state.AwaitingDiscard.required_counts`.
    """

    required_counts: dict[PlayerId, int]


class TradeOfferedPayload(BaseModel):
    trade_id: str
    proposer: PlayerId
    offered: ResourceHand
    requested: ResourceHand
    target_player_ids: list[PlayerId] | None


class TradeResolvedPayload(BaseModel):
    trade_id: str
    status: Literal["accepted", "declined", "cancelled"]
    #: The player whose ACCEPT closed the trade. `None` when `status` is
    #: "declined" (every eligible responder declined) or "cancelled"
    #: (e.g. the proposer ended their turn with the trade still open).
    accepted_by: PlayerId | None


class DevCardCountChangedPayload(BaseModel):
    """Sent after a `BUY_DEV_CARD` or a dev card play changes counts.
    Individual card identity is never broadcast except to the owning
    player (via their own masked `STATE_SNAPSHOT`).
    """

    player_id: PlayerId
    player_dev_card_count: int
    bank_dev_card_count: int


class NukeDroppedPayload(BaseModel):
    """Exact shape per the plan's "Nuke Mode" section."""

    actor: PlayerId
    target: PlayerId
    destroyed_vertex: VertexId
    destroyed_edge: EdgeId


class BlackjackRoundStartedPayload(BaseModel):
    """A dice-roll 7's blackjack round has opened for betting. Also
    conveys the eligible bettor list (every other connected, seated
    player at the moment the round started) -- there's no separate
    "bets open" event; this doubles as that announcement.
    """

    dealer_id: PlayerId
    eligible_player_ids: list[PlayerId]


class BlackjackBetPlacedPayload(BaseModel):
    player_id: PlayerId
    stake: BlackjackStake


class BlackjackBetDeclinedPayload(BaseModel):
    player_id: PlayerId


class BlackjackHandUpdatedPayload(BaseModel):
    """A bettor's hand changed from a `BLACKJACK_HIT`. Never used for the
    dealer (whose hand is only ever announced via `BLACKJACK_DEALER_REVEALED`,
    and whose interim up-card is conveyed by the next `STATE_SNAPSHOT`'s
    `BlackjackRoundView`, not a dedicated event).
    """

    player_id: PlayerId
    hand: list[Card]
    status: Literal["playing", "stood", "busted"]


class BlackjackDealerRevealedPayload(BaseModel):
    dealer_id: PlayerId
    dealer_hand: list[Card]
    dealer_total: int
    dealer_busted: bool


class BlackjackOutcome(BaseModel):
    result: Literal["win", "loss", "push"]
    stake: BlackjackStake
    final_hand: list[Card]
    final_total: int
    busted: bool


class BlackjackRoundResolvedPayload(BaseModel):
    """Per-bettor final outcome + the dealer's final hand, for the
    round-resolution toast/log. Emitted once, right before `Phase.MAIN`
    resumes.
    """

    dealer_id: PlayerId
    dealer_hand: list[Card]
    dealer_total: int
    dealer_busted: bool
    outcomes: dict[PlayerId, BlackjackOutcome]


class LongestRoadChangedPayload(BaseModel):
    new_holder: PlayerId | None
    previous_holder: PlayerId | None


class LargestArmyChangedPayload(BaseModel):
    new_holder: PlayerId | None
    previous_holder: PlayerId | None


class TurnTimerExpiredPayload(BaseModel):
    """The named player's turn was auto-ended by the stalled-turn timer
    (see `app.game.rules.turn_timer`)."""

    player_id: PlayerId


class GameOverPayload(BaseModel):
    winner: PlayerId
    final_scores: dict[PlayerId, int]


class ErrorPayload(BaseModel):
    """Exact shape per the plan: ``ERROR{code, message}``."""

    code: str
    message: str


# ---------------------------------------------------------------------
# Envelope wrappers ({type, payload, seq, ts}) and the discriminated union
# ---------------------------------------------------------------------


class _EventEnvelopeBase(BaseModel):
    seq: int
    ts: float


class SessionEstablishedEvent(_EventEnvelopeBase):
    type: Literal[EventType.SESSION_ESTABLISHED] = EventType.SESSION_ESTABLISHED
    payload: SessionEstablishedPayload


class RoomStateEvent(_EventEnvelopeBase):
    type: Literal[EventType.ROOM_STATE] = EventType.ROOM_STATE
    payload: RoomStatePayload


class PlayerJoinedEvent(_EventEnvelopeBase):
    type: Literal[EventType.PLAYER_JOINED] = EventType.PLAYER_JOINED
    payload: PlayerJoinedPayload


class PlayerLeftEvent(_EventEnvelopeBase):
    type: Literal[EventType.PLAYER_LEFT] = EventType.PLAYER_LEFT
    payload: PlayerLeftPayload


class PlayerKickedEvent(_EventEnvelopeBase):
    type: Literal[EventType.PLAYER_KICKED] = EventType.PLAYER_KICKED
    payload: PlayerKickedPayload


class PlayerDisconnectedEvent(_EventEnvelopeBase):
    type: Literal[EventType.PLAYER_DISCONNECTED] = EventType.PLAYER_DISCONNECTED
    payload: PlayerDisconnectedPayload


class PlayerReconnectedEvent(_EventEnvelopeBase):
    type: Literal[EventType.PLAYER_RECONNECTED] = EventType.PLAYER_RECONNECTED
    payload: PlayerReconnectedPayload


class SettingsUpdatedEvent(_EventEnvelopeBase):
    type: Literal[EventType.SETTINGS_UPDATED] = EventType.SETTINGS_UPDATED
    payload: SettingsUpdatedPayload


class GameStartedEvent(_EventEnvelopeBase):
    type: Literal[EventType.GAME_STARTED] = EventType.GAME_STARTED
    payload: GameStartedPayload


class StateSnapshotEvent(_EventEnvelopeBase):
    type: Literal[EventType.STATE_SNAPSHOT] = EventType.STATE_SNAPSHOT
    payload: StateSnapshotPayload


class DiceRolledEvent(_EventEnvelopeBase):
    type: Literal[EventType.DICE_ROLLED] = EventType.DICE_ROLLED
    payload: DiceRolledPayload


class ResourcesDistributedEvent(_EventEnvelopeBase):
    type: Literal[EventType.RESOURCES_DISTRIBUTED] = EventType.RESOURCES_DISTRIBUTED
    payload: ResourcesDistributedPayload


class RobberMovedEvent(_EventEnvelopeBase):
    type: Literal[EventType.ROBBER_MOVED] = EventType.ROBBER_MOVED
    payload: RobberMovedPayload


class ResourceStolenEvent(_EventEnvelopeBase):
    type: Literal[EventType.RESOURCE_STOLEN] = EventType.RESOURCE_STOLEN
    payload: ResourceStolenPayload


class DiscardRequiredEvent(_EventEnvelopeBase):
    type: Literal[EventType.DISCARD_REQUIRED] = EventType.DISCARD_REQUIRED
    payload: DiscardRequiredPayload


class TradeOfferedEvent(_EventEnvelopeBase):
    type: Literal[EventType.TRADE_OFFERED] = EventType.TRADE_OFFERED
    payload: TradeOfferedPayload


class TradeResolvedEvent(_EventEnvelopeBase):
    type: Literal[EventType.TRADE_RESOLVED] = EventType.TRADE_RESOLVED
    payload: TradeResolvedPayload


class DevCardCountChangedEvent(_EventEnvelopeBase):
    type: Literal[EventType.DEV_CARD_COUNT_CHANGED] = EventType.DEV_CARD_COUNT_CHANGED
    payload: DevCardCountChangedPayload


class NukeDroppedEvent(_EventEnvelopeBase):
    type: Literal[EventType.NUKE_DROPPED] = EventType.NUKE_DROPPED
    payload: NukeDroppedPayload


class BlackjackRoundStartedEvent(_EventEnvelopeBase):
    type: Literal[EventType.BLACKJACK_ROUND_STARTED] = EventType.BLACKJACK_ROUND_STARTED
    payload: BlackjackRoundStartedPayload


class BlackjackBetPlacedEvent(_EventEnvelopeBase):
    type: Literal[EventType.BLACKJACK_BET_PLACED] = EventType.BLACKJACK_BET_PLACED
    payload: BlackjackBetPlacedPayload


class BlackjackBetDeclinedEvent(_EventEnvelopeBase):
    type: Literal[EventType.BLACKJACK_BET_DECLINED] = EventType.BLACKJACK_BET_DECLINED
    payload: BlackjackBetDeclinedPayload


class BlackjackHandUpdatedEvent(_EventEnvelopeBase):
    type: Literal[EventType.BLACKJACK_HAND_UPDATED] = EventType.BLACKJACK_HAND_UPDATED
    payload: BlackjackHandUpdatedPayload


class BlackjackDealerRevealedEvent(_EventEnvelopeBase):
    type: Literal[EventType.BLACKJACK_DEALER_REVEALED] = EventType.BLACKJACK_DEALER_REVEALED
    payload: BlackjackDealerRevealedPayload


class BlackjackRoundResolvedEvent(_EventEnvelopeBase):
    type: Literal[EventType.BLACKJACK_ROUND_RESOLVED] = EventType.BLACKJACK_ROUND_RESOLVED
    payload: BlackjackRoundResolvedPayload


class LongestRoadChangedEvent(_EventEnvelopeBase):
    type: Literal[EventType.LONGEST_ROAD_CHANGED] = EventType.LONGEST_ROAD_CHANGED
    payload: LongestRoadChangedPayload


class LargestArmyChangedEvent(_EventEnvelopeBase):
    type: Literal[EventType.LARGEST_ARMY_CHANGED] = EventType.LARGEST_ARMY_CHANGED
    payload: LargestArmyChangedPayload


class TurnTimerExpiredEvent(_EventEnvelopeBase):
    type: Literal[EventType.TURN_TIMER_EXPIRED] = EventType.TURN_TIMER_EXPIRED
    payload: TurnTimerExpiredPayload


class GameOverEvent(_EventEnvelopeBase):
    type: Literal[EventType.GAME_OVER] = EventType.GAME_OVER
    payload: GameOverPayload


class ErrorEvent(_EventEnvelopeBase):
    type: Literal[EventType.ERROR] = EventType.ERROR
    payload: ErrorPayload


#: Discriminated union of every possible Server -> Client message.
ServerEvent: TypeAlias = Annotated[
    SessionEstablishedEvent
    | RoomStateEvent
    | PlayerJoinedEvent
    | PlayerLeftEvent
    | PlayerKickedEvent
    | PlayerDisconnectedEvent
    | PlayerReconnectedEvent
    | SettingsUpdatedEvent
    | GameStartedEvent
    | StateSnapshotEvent
    | DiceRolledEvent
    | ResourcesDistributedEvent
    | RobberMovedEvent
    | ResourceStolenEvent
    | DiscardRequiredEvent
    | TradeOfferedEvent
    | TradeResolvedEvent
    | DevCardCountChangedEvent
    | NukeDroppedEvent
    | BlackjackRoundStartedEvent
    | BlackjackBetPlacedEvent
    | BlackjackBetDeclinedEvent
    | BlackjackHandUpdatedEvent
    | BlackjackDealerRevealedEvent
    | BlackjackRoundResolvedEvent
    | LongestRoadChangedEvent
    | LargestArmyChangedEvent
    | TurnTimerExpiredEvent
    | GameOverEvent
    | ErrorEvent,
    Field(discriminator="type"),
]
