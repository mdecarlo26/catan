"""Initial-placement setup strategies, selected via `GameSettings.rush_mode`.

Per the plan (`ARCHITECTURE.md`'s "Settings / Toggle System" section):
each toggle is implemented as a strategy object selected once at game
start from a registry -- `SETUP_STRATEGIES[rush_mode]` here -- rather
than scattered `if settings.rush_mode:` conditionals through
`rules_engine`.

`GameState`/`PlayerState` are frozen contracts this module must not
extend, so "whose turn is it, and are they placing a settlement or the
paired road" is derived purely from how many settlements/roads are
already on the board rather than from any extra tracked field -- see
`SnakeDraftSetup.get_next_setup_action` (sequential/global) and
`RushModeSetup.next_expected_action_for_player` (simultaneous/per-player).

The two strategies' "what should be placed next" query is intentionally
NOT unified behind one `SetupStrategy` Protocol method: `SnakeDraftSetup`
answers "who's up, and what do they owe" (one global answer); `RushModeSetup`
answers "what does *this specific player* owe" (independent per player,
since rush mode has no single global "next placer"). Forcing one shared
signature would mean one side always ignoring an argument it can't use.
`rules_engine._validate_setup_turn` branches on `state.settings.rush_mode`
and calls whichever shape applies. Both strategies do share
`on_setup_complete`'s shape (see the `SetupStrategy` Protocol below),
since "what happens once everyone's placed their full allotment" only
needs one game-wide answer regardless of how placement order was decided.
"""

from __future__ import annotations

import time
from typing import Protocol

from app.game.actions import ActionType
from app.game.board import BuildingType, PlayerId
from app.game.state import GameState, Phase


class SetupStrategy(Protocol):
    """Interface `rules_engine` codes against for `Phase.SETUP` handling.

    Only the part both strategies genuinely share -- see the module
    docstring for why "what should be placed next" isn't part of this
    shared interface.
    """

    def on_setup_complete(self, state: GameState) -> None:
        """Called once every player has placed their full setup allotment
        (2 settlements + 2 roads each). Responsible for transitioning
        `state.phase` away from `Phase.SETUP` and setting up whoever acts
        next.
        """
        ...


class SnakeDraftSetup:
    """Standard Catan initial placement.

    Forward turn order for the first settlement+road pair (player 1..N),
    then reverse order for the second pair (N..1) -- so the last player
    to go in the forward round immediately goes again first in the
    reverse round. `rules_engine` grants a player their starting
    resources (for hexes adjacent to their settlement) the moment their
    *second* settlement is placed, per standard rules; this class only
    tracks turn order, not resource grants.

    Whose turn it is, and whether they owe a settlement or the paired
    road, is derived purely from counting settlements/roads already on
    the board -- during `Phase.SETUP` every settlement on the board was
    placed via this draft, so the counts alone pin down the exact
    position in the draft order.
    """

    @staticmethod
    def draft_order(turn_order: list[PlayerId]) -> list[PlayerId]:
        """The full 2N-long placement sequence: forward then reverse."""
        return list(turn_order) + list(reversed(turn_order))

    def get_next_setup_action(self, state: GameState) -> tuple[PlayerId, ActionType]:
        order = self.draft_order(state.turn_order)
        settlements_built = sum(
            1
            for building in state.board.buildings.values()
            if building.building_type == BuildingType.SETTLEMENT
        )
        roads_built = len(state.board.roads)

        if settlements_built == roads_built:
            if settlements_built >= len(order):
                raise RuntimeError("setup is already complete")
            # This player owes their next settlement.
            return order[settlements_built], ActionType.BUILD_SETTLEMENT

        # settlements_built == roads_built + 1: the most recent settlement
        # still owes its paired road, placed by the same player. This can
        # happen even after every settlement has been placed (the very
        # last player still owes their final road), so it's checked
        # before the "is setup complete" cutoff above.
        return order[roads_built], ActionType.BUILD_ROAD

    def on_setup_complete(self, state: GameState) -> None:
        state.phase = Phase.ROLL
        # The player who went first in the forward round rolls first.
        state.current_player_index = 0


class RushModeSetup:
    """Rush mode's initial placement: simultaneous, not snake-drafted.

    Every seated player may place their 2 settlements + 2 roads in any
    order *relative to every other player* -- there is no single global
    "whose turn is it" the way `SnakeDraftSetup.get_next_setup_action`
    computes. Instead, each player's own placements are independently
    validated against the same distance/legality rules, gated only by
    what *that player* has placed so far: they must place their first
    settlement, then their paired road, then their second settlement,
    then its paired road, in that order for themselves, but are never
    blocked by another player's incomplete progress.

    This intentionally does NOT implement `SnakeDraftSetup`'s
    `get_next_setup_action` shape (there is no single "next" player/
    action pair to return) -- `rules_engine._validate_setup_turn`
    branches on `state.settings.rush_mode` and calls
    `next_expected_action_for_player` here instead. Both classes still
    share `on_setup_complete`'s shape, since "what happens once
    everyone's done" only differs in *what* happens next, not in when
    it's called from (`rules_engine._maybe_complete_setup`, which counts
    total settlements/roads across all players -- a strategy-agnostic
    check that already works unmodified for simultaneous placement).
    """

    def next_expected_action_for_player(
        self, state: GameState, actor_id: PlayerId
    ) -> ActionType | None:
        """The action `actor_id` personally owes next during their own
        setup placement, derived purely from counting *their own*
        buildings already on the board (mirroring
        `SnakeDraftSetup.get_next_setup_action`'s "derive from board
        counts, no extra tracked field" approach, just scoped to one
        player instead of the whole draft order). Returns `None` once
        `actor_id` has placed their full 2 settlements + 2 roads quota --
        nothing more is expected from them, and `rules_engine` treats any
        further attempt as illegal.
        """
        settlements = sum(
            1
            for building in state.board.buildings.values()
            if building.player_id == actor_id
            and building.building_type == BuildingType.SETTLEMENT
        )
        roads = sum(1 for owner in state.board.roads.values() if owner == actor_id)
        if settlements >= 2 and roads >= 2:
            return None
        if settlements == roads:
            return ActionType.BUILD_SETTLEMENT
        # settlements == roads + 1: this player's most recent settlement
        # still owes its paired road.
        return ActionType.BUILD_ROAD

    def on_setup_complete(self, state: GameState) -> None:
        # Rush mode has no ROLL phase or "current player" -- MAIN begins
        # immediately for everyone, and dice roll automatically from here
        # on (see rules_engine.apply_rush_auto_roll, wired up by
        # app.api.websocket's rush-roll background task).
        # `current_player_index` is deliberately left at its default;
        # nothing reads it in rush mode's MAIN phase (see
        # rules_engine._require_actionable_player).
        state.phase = Phase.MAIN
        state.last_dice_roll_ts = time.time()


#: Selected once at game start via `state.settings.rush_mode`, mirroring
#: the plan's `SETUP_STRATEGIES[rush_mode]` pattern.
SETUP_STRATEGIES: dict[bool, SetupStrategy] = {
    False: SnakeDraftSetup(),
    True: RushModeSetup(),
}
