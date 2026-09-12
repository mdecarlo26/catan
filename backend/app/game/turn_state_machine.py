"""Phase -> legal `ActionType` table.

Per the plan's "Turn/phase state machine" section:
``rules_engine.validate_action()`` checks this table first, then
rule-specific validation -- centralizing illegal-action rejection instead
of duplicating it per handler. This module owns that table and nothing
else; it has no gameplay logic of its own.

Room-lifecycle actions (`JOIN_ROOM`, `LEAVE_ROOM`, `KICK_PLAYER`,
`UPDATE_SETTINGS`, `START_GAME`) are included here for completeness (this
table is meant to be the single source of truth for "what's legal when"),
but in practice they never reach `app.game.rules_engine` -- the WS
handler routes them straight to `app.core.room` (see `ARCHITECTURE.md`'s
repo structure). `rules_engine` only ever calls `is_action_allowed` for
actual gameplay actions.
"""

from app.game.actions import ActionType
from app.game.state import Phase

#: Action types legal in *every* phase, independent of game state.
ALWAYS_ALLOWED: frozenset[ActionType] = frozenset({ActionType.CHAT_MESSAGE})

#: Phase -> the set of `ActionType`s legal to submit while in that phase.
#: This is deliberately just "is this action type even on the table right
#: now" -- whose turn it is, whether the actor can afford it, board
#: legality, etc. are all rule-specific concerns `rules_engine` checks
#: after this table passes.
ALLOWED_ACTIONS: dict[Phase, frozenset[ActionType]] = {
    Phase.LOBBY: frozenset(
        {
            ActionType.JOIN_ROOM,
            ActionType.LEAVE_ROOM,
            ActionType.KICK_PLAYER,
            ActionType.UPDATE_SETTINGS,
            ActionType.START_GAME,
        }
    ),
    Phase.SETUP: frozenset(
        {
            ActionType.BUILD_SETTLEMENT,
            ActionType.BUILD_ROAD,
            ActionType.LEAVE_ROOM,
        }
    ),
    Phase.ROLL: frozenset(
        {
            ActionType.ROLL_DICE,
            ActionType.LEAVE_ROOM,
        }
    ),
    Phase.ROBBER_DISCARD: frozenset(
        {
            ActionType.DISCARD_CARDS,
            ActionType.LEAVE_ROOM,
        }
    ),
    Phase.ROBBER_MOVE: frozenset(
        {
            # Both are legal in this phase; which one is actually valid
            # *right now* (move vs. steal) is further narrowed by
            # `GameState.pending` (`AwaitingRobberPlacement` vs.
            # `AwaitingSteal`) -- see `rules_engine`.
            ActionType.MOVE_ROBBER,
            ActionType.STEAL_RESOURCE,
            ActionType.LEAVE_ROOM,
        }
    ),
    Phase.MAIN: frozenset(
        {
            ActionType.BUILD_SETTLEMENT,
            ActionType.BUILD_ROAD,
            ActionType.BUILD_CITY,
            ActionType.BUY_DEV_CARD,
            ActionType.PLAY_DEV_CARD,
            ActionType.BANK_TRADE,
            ActionType.PORT_TRADE,
            ActionType.PROPOSE_TRADE,
            # Legal in MAIN because `GameState.phase` stays MAIN while a
            # trade is pending (see `AwaitingTradeResponse`'s docstring).
            ActionType.RESPOND_TRADE,
            ActionType.PLAY_NUKE,
            ActionType.END_TURN,
            # Only ever actually reachable in rush mode: `settings.rush_mode`
            # never leaves `Phase.MAIN` after setup (there's no ROBBER_MOVE/
            # ROBBER_DISCARD phase transition -- see `rules_engine`'s rush
            # helpers), so these three have to be legal here for that mode
            # to ever use them. Normal mode's own use of them only ever
            # happens from `Phase.ROBBER_DISCARD`/`Phase.ROBBER_MOVE`
            # (below), never while `phase == MAIN`, so adding them here is
            # a no-op for normal mode -- the rule-specific validators
            # (`_validate_discard_cards`/`_validate_move_robber`/
            # `_validate_steal_resource`) still reject them via
            # `GameState.pending` being the wrong shape (or `None`) outside
            # that context.
            ActionType.DISCARD_CARDS,
            ActionType.MOVE_ROBBER,
            ActionType.STEAL_RESOURCE,
            ActionType.LEAVE_ROOM,
        }
    ),
    Phase.SPECIAL_BUILD: frozenset(
        {
            # Official 5-6p expansion rule: the player up in
            # `GameState.special_build_queue` may trade and build using
            # resources already in hand, but may not roll dice or play
            # dev cards. `END_TURN` here means "done with my special
            # build turn" -- see `rules_engine._apply_end_turn`.
            ActionType.BUILD_SETTLEMENT,
            ActionType.BUILD_ROAD,
            ActionType.BUILD_CITY,
            ActionType.BUY_DEV_CARD,
            ActionType.BANK_TRADE,
            ActionType.PORT_TRADE,
            ActionType.PROPOSE_TRADE,
            ActionType.RESPOND_TRADE,
            ActionType.END_TURN,
            ActionType.LEAVE_ROOM,
        }
    ),
    Phase.BLACKJACK_ROUND: frozenset(
        {
            # Whoever may currently act (an eligible bettor during
            # `status == "betting"`, or `bettor_queue[0]` during
            # `status == "bettor_turn"` -- see `GameState.blackjack_round`)
            # is further narrowed by `rules_engine`'s rule-specific
            # validators, exactly like `Phase.ROBBER_MOVE`'s
            # MOVE_ROBBER/STEAL_RESOURCE pair above. No building/trading/
            # dev cards/dice during this house-rule side round.
            ActionType.BLACKJACK_PLACE_BET,
            ActionType.BLACKJACK_DECLINE,
            ActionType.BLACKJACK_HIT,
            ActionType.BLACKJACK_STAND,
            ActionType.LEAVE_ROOM,
        }
    ),
    Phase.GAME_OVER: frozenset(
        {
            ActionType.LEAVE_ROOM,
        }
    ),
}


def is_action_allowed(phase: Phase, action_type: ActionType) -> bool:
    """True if `action_type` is even on the table during `phase`.

    This is the first, coarse-grained check `rules_engine.validate()`
    performs, before any rule-specific validation (turn ownership,
    resource costs, board legality, ...).
    """
    if action_type in ALWAYS_ALLOWED:
        return True
    return action_type in ALLOWED_ACTIONS.get(phase, frozenset())
