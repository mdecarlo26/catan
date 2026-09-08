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
`SnakeDraftSetup.get_next_setup_action`.
"""

from __future__ import annotations

from typing import Protocol

from app.game.actions import ActionType
from app.game.board import BuildingType, PlayerId
from app.game.state import GameState, Phase


class SetupStrategy(Protocol):
    """Interface `rules_engine` codes against for `Phase.SETUP` handling."""

    def get_next_setup_action(self, state: GameState) -> tuple[PlayerId, ActionType]:
        """Return `(expected_player, expected_action_type)` for the next
        setup placement. Only ever called while `state.phase == Phase.SETUP`.
        """
        ...

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
    """Placeholder for the `rush_mode` alternate setup/pacing rules.

    Per `GameSettings.rush_mode`'s docstring and the plan's "Settings /
    Toggle System" section, rush mode's actual gameplay behavior is
    deliberately deferred -- design TBD. This class exists only so the
    `SETUP_STRATEGIES` registry has a slot to select today without
    requiring a restructure once the real rules land; every method is an
    explicit stub.
    """

    def get_next_setup_action(self, state: GameState) -> tuple[PlayerId, ActionType]:
        raise NotImplementedError("rush mode rules TBD")

    def on_setup_complete(self, state: GameState) -> None:
        raise NotImplementedError("rush mode rules TBD")


#: Selected once at game start via `state.settings.rush_mode`, mirroring
#: the plan's `SETUP_STRATEGIES[rush_mode]` pattern.
SETUP_STRATEGIES: dict[bool, SetupStrategy] = {
    False: SnakeDraftSetup(),
    True: RushModeSetup(),
}
