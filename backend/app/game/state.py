"""The authoritative, in-memory server-side game state.

`GameState` is the single source of truth for one room's game. It is a
rich Python-internal object -- it is never sent to the wire as-is. The
per-recipient, hidden-information-masked wire view is produced by
`app.game.serialization.to_client_view()` and typed as
`app.protocol.events.ClientGameStateView`. Keep that separation in mind
when extending this file: fields here should reflect what the *server*
needs to know, not what's safe to reveal to a given client.
"""

from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field

from app.game.board import Board, EdgeId, PlayerId, VertexId
from app.game.players import DevCardType, PlayerState, ResourceHand, ResourceType
from app.game.settings_schema import GameSettings


class Phase(str, Enum):
    """Top-level turn/game phase. Drives the allowed-actions table in
    `app.game.turn_state_machine`, which `rules_engine.validate_action()`
    consults before any rule-specific validation.
    """

    #: Room exists, players joining/adjusting settings; game not started.
    LOBBY = "lobby"
    #: Initial snake-draft (or rush-mode) settlement/road placement.
    SETUP = "setup"
    #: Current player must roll the dice to start their turn.
    ROLL = "roll"
    #: A 7 was rolled; players holding more than the discard limit must
    #: discard before play continues. See `PendingAction.AwaitingDiscard`.
    ROBBER_DISCARD = "robber_discard"
    #: The robber must be moved (after a 7 roll, once discards are
    #: resolved, or immediately after a Knight is played).
    ROBBER_MOVE = "robber_move"
    #: Normal turn actions: build, trade, buy/play dev cards, end turn.
    MAIN = "main"
    #: Official 5-6p expansion's Special Build Phase: entered after the
    #: current player ends their normal `MAIN` turn, when the effective
    #: `settings.special_build_phase` is on (see
    #: `GameSettings.special_build_phase`'s docstring). Every other
    #: player, in turn order starting right after the player who just
    #: went, gets one build-only mini-turn (trade + build, no dice, no
    #: dev cards) before `current_player_index` actually advances. See
    #: `GameState.special_build_queue`.
    SPECIAL_BUILD = "special_build"
    #: House rule (`settings.blackjack_mode`): inserted right after the
    #: normal post-7 discard -> move-robber -> steal sequence fully
    #: resolves, in place of the immediate return to `Phase.MAIN` --
    #: exactly the same "new inserted sub-phase, gated by its own queue
    #: field" shape as `SPECIAL_BUILD`. See `GameState.blackjack_round`
    #: and the plan's "Workstream 2: Blackjack-on-7 Mode" section for the
    #: full rule set. Never entered while `settings.rush_mode` is on (no
    #: single "current player" to be dealer) or for a Knight-triggered
    #: robber move (only a *dice-roll* 7 offers blackjack).
    BLACKJACK_ROUND = "blackjack_round"
    #: `victory_points_target` reached; game frozen for the post-game
    #: summary until the room's TTL sweep evicts it.
    GAME_OVER = "game_over"


class AwaitingDiscard(BaseModel):
    """`GameState.pending` value while players owe a robber-7 discard.

    `rules_engine` should stay in `Phase.ROBBER_DISCARD` until
    `required_counts` is empty (i.e. every player who owed cards has
    submitted a valid `DISCARD_CARDS` action).
    """

    kind: Literal["awaiting_discard"] = "awaiting_discard"
    #: player_id -> number of cards that player still owes. Entries are
    #: removed as each player successfully discards.
    required_counts: dict[PlayerId, int]


class AwaitingRobberPlacement(BaseModel):
    """`GameState.pending` value while the robber must be moved.

    Triggered by a dice roll of 7 (after any discards resolve) or by
    playing a KNIGHT dev card.
    """

    kind: Literal["awaiting_robber_placement"] = "awaiting_robber_placement"
    actor: PlayerId
    reason: Literal["dice_roll", "knight_card"]


class AwaitingSteal(BaseModel):
    """`GameState.pending` value after the robber has been moved onto a
    hex, while `actor` chooses (or auto-resolves, if only one candidate)
    a victim via `STEAL_RESOURCE`.
    """

    kind: Literal["awaiting_steal"] = "awaiting_steal"
    actor: PlayerId
    #: Players with a settlement/city on the robber's new hex who have at
    #: least one resource card. Empty means the steal auto-resolves to a
    #: no-op.
    candidate_targets: list[PlayerId]
    #: Carried over from the `AwaitingRobberPlacement` that preceded this
    #: steal, since blackjack-on-7 (`settings.blackjack_mode`) must only
    #: ever trigger for a *dice-roll* 7, never a Knight-triggered robber
    #: move -- and by the time a multi-candidate steal is being resolved,
    #: the original `AwaitingRobberPlacement.reason` would otherwise be
    #: lost. See `rules_engine._maybe_enter_blackjack_round`'s call sites.
    reason: Literal["dice_roll", "knight_card"]


class AwaitingTradeResponse(BaseModel):
    """`GameState.pending` value while a `PROPOSE_TRADE` is outstanding.

    `GameState.phase` stays `Phase.MAIN` during a pending trade -- other
    MAIN-phase actions (e.g. ending the turn) implicitly cancel it, which
    `rules_engine` is responsible for enforcing.
    """

    kind: Literal["awaiting_trade_response"] = "awaiting_trade_response"
    trade_id: str
    proposer: PlayerId
    offered: ResourceHand
    requested: ResourceHand
    #: Players who have not yet responded. Trade resolves (accepted by
    #: the first ACCEPT, or cancelled once this empties with no accept)
    #: as responses come in.
    responses_pending: list[PlayerId]


#: Discriminated union of every "the game is explicitly waiting on
#: something before normal action validation resumes" state. Using an
#: explicit `pending` field (rather than inferring legal next-actions
#: purely from `phase`) keeps illegal-action rejection centralized and
#: unambiguous -- see the plan's "Turn/phase state machine" section.
PendingAction: TypeAlias = Annotated[
    AwaitingDiscard
    | AwaitingRobberPlacement
    | AwaitingSteal
    | AwaitingTradeResponse,
    Field(discriminator="kind"),
]

#: Rush-mode-only shape for `GameState.rush_pending_robber`: reuses the
#: same `AwaitingRobberPlacement` / `AwaitingSteal` models as normal
#: mode's `pending` (deliberately -- see that field's docstring for why
#: rush mode can't just store these in `pending` itself), narrowed to the
#: two kinds relevant to "the robber is currently being handled by one
#: assigned player" (discard debts have their own dict-shaped field,
#: `rush_pending_discard`, since -- unlike the robber -- many players can
#: owe a discard at once).
RushRobberPending: TypeAlias = Annotated[
    AwaitingRobberPlacement | AwaitingSteal,
    Field(discriminator="kind"),
]


class CardSuit(str, Enum):
    """Suit of a standard playing card. Irrelevant to blackjack scoring
    (only `CardRank` matters for hand value) but carried on the wire for
    a real-looking card display."""

    SPADES = "spades"
    HEARTS = "hearts"
    DIAMONDS = "diamonds"
    CLUBS = "clubs"


class CardRank(str, Enum):
    """Rank of a standard playing card. Value string doubles as the pip
    count for number cards (`"2"`.."10"`) -- see
    `app.game.rules.blackjack.hand_value` for ace (11-or-1) and face-card
    (10) handling."""

    ACE = "A"
    TWO = "2"
    THREE = "3"
    FOUR = "4"
    FIVE = "5"
    SIX = "6"
    SEVEN = "7"
    EIGHT = "8"
    NINE = "9"
    TEN = "10"
    JACK = "J"
    QUEEN = "Q"
    KING = "K"


class Card(BaseModel):
    """One playing card, for `settings.blackjack_mode`'s standard 52-card
    deck (`app.game.rules.blackjack.build_shuffled_deck`)."""

    model_config = ConfigDict(frozen=True)

    rank: CardRank
    suit: CardSuit


class BlackjackStake(BaseModel):
    """What a bettor put up for one blackjack round, per the plan's two
    stake kinds. Exactly one of `resources` / `vertex_id` / `edge_id` is
    populated, matching `kind`:
      - `"resources"`: a subset of the bettor's hand at bet time.
      - `"settlement"` / `"city"`: one of the bettor's own buildings
        (`vertex_id`), resolved to this concrete kind from
        `Board.buildings` at bet-placement time.
      - `"road"`: one of the bettor's own roads (`edge_id`).

    Deliberately public (not masked anywhere) -- placing a bet is treated
    as a public table commitment, like a `PROPOSE_TRADE` offer, not
    hidden information; only the dealer's hole card is ever hidden. See
    `app.game.rules_engine._validate_blackjack_place_bet` for the
    ownership/affordability checks made before this is constructed.
    """

    kind: Literal["resources", "settlement", "city", "road"]
    resources: ResourceHand | None = None
    vertex_id: VertexId | None = None
    edge_id: EdgeId | None = None


class BlackjackParticipant(BaseModel):
    """One bettor's state for the current blackjack round."""

    stake: BlackjackStake
    hand: list[Card] = Field(default_factory=list)
    status: Literal["playing", "stood", "busted"] = "playing"


class BlackjackRoundState(BaseModel):
    """`GameState.blackjack_round` -- populated only while `phase ==
    Phase.BLACKJACK_ROUND`. See that phase's docstring and the plan's
    "Workstream 2: Blackjack-on-7 Mode" section for the full rule set.

    Lifecycle: created with `status == "betting"` the moment the round
    starts, with every other connected, seated player listed in
    `responses_pending`. Each may `BLACKJACK_PLACE_BET` or
    `BLACKJACK_DECLINE` exactly once; once `responses_pending` empties
    (everyone answered, or the stalled-turn timer force-declined the
    rest -- see `app.api.websocket._force_advance_stalled_blackjack`),
    `rules_engine._close_blackjack_betting` either aborts the whole round
    back to `Phase.MAIN` (nobody bet) or shuffles `deck`, deals the
    dealer and every participant two cards, builds `bettor_queue` in seat
    order, and flips `status` to `"bettor_turn"`. `bettor_queue[0]` may
    then `BLACKJACK_HIT`/`BLACKJACK_STAND` until they stand or bust (same
    queue-walk shape as `GameState.special_build_queue`), advancing the
    queue each time. Once `bettor_queue` empties, `rules_engine` reveals
    and auto-plays the dealer's hand and resolves every participant's
    payout synchronously in that same `apply()` call, then returns
    `GameState.phase` to `Phase.MAIN` and clears this field back to
    `None` -- there is no separate persisted "dealer turn" / "resolved"
    status to wait on.
    """

    dealer_id: PlayerId
    dealer_hand: list[Card] = Field(default_factory=list)
    #: False until the dealer's hole (second) card is revealed at
    #: resolution time. See `app.serialization`'s masking of
    #: `dealer_hand[1]` while this is `False`.
    dealer_hole_card_revealed: bool = False

    status: Literal["betting", "bettor_turn"] = "betting"

    #: Connected, seated non-dealer players who haven't yet placed a bet
    #: or declined this round. See this model's docstring.
    responses_pending: list[PlayerId] = Field(default_factory=list)

    #: player_id -> that bettor's participation. Only players who bet
    #: (not those who declined) get an entry.
    participants: dict[PlayerId, BlackjackParticipant] = Field(default_factory=dict)

    #: Seat-order queue of bettor ids still needing to hit/stand this
    #: round; `bettor_queue[0]` is whoever may currently act. Mirrors
    #: `special_build_queue`'s shape exactly. Empty during `status ==
    #: "betting"`.
    bettor_queue: list[PlayerId] = Field(default_factory=list)

    #: The round's shuffled deck, dealt from via `list.pop()` (same "last
    #: element is next draw" convention as `Bank.dev_card_pile`). Never
    #: exposed on the wire -- see `app.protocol.events.BlackjackRoundView`,
    #: which omits it entirely.
    deck: list[Card] = Field(default_factory=list)


class Bank(BaseModel):
    """The shared resource and dev-card supply.

    `resources` starts at 19 of each `ResourceType` in standard Catan.
    `dev_card_pile` is the shuffled, face-down remainder of the 25-card
    dev card deck; the convention is that the *last* element is the top
    of the pile (so drawing is a `list.pop()`). This raw pile is
    internal-only -- the wire view exposes only `bank_dev_card_count`
    (see `app.protocol.events.ClientGameStateView`), never card order or
    identity, since dev card contents must stay hidden until played.
    """

    resources: ResourceHand
    dev_card_pile: list[DevCardType] = Field(default_factory=list)


class ActionLogEntry(BaseModel):
    """One human-readable entry in the room's turn log, used both for the
    in-client turn log UI and to give a reconnecting client context.
    """

    #: Matches the WS envelope `seq` of the event that produced this
    #: entry, so the client can correlate log lines with protocol events.
    seq: int
    #: Unix timestamp (seconds).
    ts: float
    message: str


class GameState(BaseModel):
    """One room's complete, authoritative game state.

    `action_log` is expected to be capped at a bounded length by whatever
    appends to it (e.g. `rules_engine`) -- this model does not enforce a
    max length itself.
    """

    room_code: str

    #: Snapshot of the room's settings, locked at `START_GAME` time. Not
    #: the same object as the lobby's live-editable settings (see
    #: `app.core.room.Room`), which stops accepting `UPDATE_SETTINGS`
    #: once the game starts.
    settings: GameSettings

    phase: Phase = Phase.LOBBY

    #: Seating order, fixed at game start. Index into this list, not
    #: dict iteration order, determines turn order.
    turn_order: list[PlayerId] = Field(default_factory=list)
    current_player_index: int = 0

    #: (die1, die2) of the most recent roll, or `None` before the first
    #: roll of the game.
    last_dice_roll: tuple[int, int] | None = None

    board: Board
    bank: Bank
    #: player_id -> that player's full state. Iterate `turn_order` for
    #: seating/turn order; this dict is unordered w.r.t. gameplay.
    players: dict[PlayerId, PlayerState] = Field(default_factory=dict)

    longest_road_holder: PlayerId | None = None
    largest_army_holder: PlayerId | None = None

    #: While `phase == Phase.SPECIAL_BUILD`: the players who still owe a
    #: special-build mini-turn this round, in the order they'll take it.
    #: `special_build_queue[0]` is whoever may currently act (build/trade,
    #: then submit `END_TURN` to mean "done with my special turn" and
    #: advance to the next entry). Empty whenever `phase !=
    #: Phase.SPECIAL_BUILD`. `current_player_index` is deliberately left
    #: pointing at the player who just finished their normal turn for the
    #: whole round (it only advances to the real next player once this
    #: queue empties) -- clients render "whose special-build turn is it"
    #: from this field, not from `current_player_index`, while `phase ==
    #: Phase.SPECIAL_BUILD`.
    special_build_queue: list[PlayerId] = Field(default_factory=list)

    #: While `phase == Phase.BLACKJACK_ROUND`: the full state of that
    #: round (dealer hand, bettors' bets/hands/status, the bettor-turn
    #: queue, and the round's deck). `None` at every other phase -- see
    #: `BlackjackRoundState`'s docstring for the full lifecycle. A
    #: dedicated field rather than a `PendingAction` member, following
    #: `special_build_queue`'s precedent: this round carries real,
    #: multi-player internal state that doesn't fit `PendingAction`'s
    #: flat, single-purpose shapes.
    blackjack_round: BlackjackRoundState | None = None

    #: What the game is explicitly waiting on before normal action
    #: validation resumes, or `None` during ordinary play. See
    #: `PendingAction`. In rush-mode `Phase.MAIN`, this field is used
    #: ONLY for `AwaitingTradeResponse` (a `PROPOSE_TRADE` still occupies
    #: one global slot even in rush mode -- see `rules_engine
    #: ._validate_propose_trade`'s docstring for that documented scope
    #: decision) -- discard/robber obligations use the rush-specific
    #: fields below instead, since unlike normal mode (where `pending`
    #: being set always means "the whole game is blocked on this"), rush
    #: mode needs those to coexist with everyone else still playing
    #: normally. `pending` is never `AwaitingDiscard` /
    #: `AwaitingRobberPlacement` / `AwaitingSteal` while `settings.rush_mode`
    #: is on.
    pending: PendingAction | None = None

    #: Rush-mode-only: player_id -> cards still owed, for every player
    #: currently over `settings.discard_limit` after an auto-rolled 7.
    #: Shaped exactly like `AwaitingDiscard.required_counts` (reusing
    #: that model as-is) but tracked independently of `pending` so it can
    #: coexist with everyone else's ordinary play -- each owing player is
    #: individually blocked from other actions until they resolve their
    #: own entry (see `rules_engine._require_rush_unblocked`), but nobody
    #: else is. `None` when nobody currently owes a rush-mode discard.
    rush_pending_discard: AwaitingDiscard | None = None

    #: Rush-mode-only: the one player currently assigned to move the
    #: robber (`AwaitingRobberPlacement`) or, once they've moved it and
    #: multiple steal candidates exist, to choose a steal target
    #: (`AwaitingSteal`) -- reusing those same models as normal mode's
    #: `pending`, just stored here so only *that* player is blocked (see
    #: `rules_engine._require_rush_unblocked`) instead of the whole game.
    #: Assigned by rotating through `turn_order` (see
    #: `rush_robber_turn_index`) each time an auto-roll lands on 7;
    #: `None` when nobody currently has an unresolved robber obligation.
    #: A Knight card played in rush mode also routes through this same
    #: field (assigned to whoever played it) rather than the dice-roll
    #: rotation -- see `rules_engine._apply_play_dev_card`.
    rush_pending_robber: RushRobberPending | None = None

    #: Rush-mode-only rotation pointer: the `turn_order` index of the
    #: player who will be assigned `rush_pending_robber` the *next* time
    #: an auto-rolled 7 needs a fresh assignment (i.e. `rush_pending_robber`
    #: is currently `None`). Advanced past disconnected players only at
    #: resolution time (`rules_engine._next_rush_robber_mover`), never
    #: baked into the stored index itself, so a player who reconnects
    #: later still gets their fair turn in the rotation. If a 7 lands
    #: while a previous robber-move is still unresolved, `rules_engine`
    #: does not create a second concurrent assignment for the one
    #: physical robber (see `_apply_roll_dice`'s rush branch / the
    #: module's dedicated comment) -- it only advances this pointer, so
    #: the same player isn't unfairly assigned twice in a row once the
    #: current one finally resolves. This is a deliberate, documented
    #: "queue by skipping" tradeoff rather than a real per-player FIFO
    #: queue of pending robber-moves.
    rush_robber_turn_index: int = 0

    #: Unix timestamp (seconds) of the most recent dice roll -- set by
    #: both the client-invoked `ROLL_DICE` action and rush mode's
    #: system-driven `apply_rush_auto_roll` (and, for rush mode, primed
    #: to "now" the moment `Phase.MAIN` begins -- see `RushModeSetup
    #: .on_setup_complete`). Exposed on `ClientGameStateView` so clients
    #: can render a "next auto-roll in Ns" countdown from
    #: `settings.rush_roll_interval_seconds` without needing their own
    #: clock synced to a server-authoritative roll schedule. `None`
    #: before the first roll of a non-rush game (rush mode always primes
    #: it at setup completion, before any roll has actually happened).
    last_dice_roll_ts: float | None = None

    action_log: list[ActionLogEntry] = Field(default_factory=list)
