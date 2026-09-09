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

from pydantic import BaseModel, Field

from app.game.board import Board, PlayerId
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
