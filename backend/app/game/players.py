"""Player-facing game state: hand, dev cards, buildings, score flags.

`PlayerState` is the per-player slice of `app.game.state.GameState.players`.
It is never sent to the wire as-is -- `app.game.serialization.to_client_view`
masks opponents' `hand` / `dev_cards` down to counts-only. The wire-safe
shape is `app.protocol.events.MaskedPlayerView`.
"""

from enum import Enum
from typing import TypeAlias

from pydantic import BaseModel, Field

from app.game.board import PlayerId


class ResourceType(str, Enum):
    """The five tradeable resource types, each produced by one `Terrain`.

    See `app.game.board.Terrain` for the terrain -> resource mapping.
    """

    BRICK = "brick"
    LUMBER = "lumber"
    ORE = "ore"
    GRAIN = "grain"
    WOOL = "wool"


class DevCardType(str, Enum):
    """The five development card kinds in the standard 25-card deck.

    Standard deck composition (for reference; the actual deck is built by
    `app.game.dev_cards`, not here): 14 KNIGHT, 5 VICTORY_POINT,
    2 each of ROAD_BUILDING / YEAR_OF_PLENTY / MONOPOLY.
    """

    KNIGHT = "knight"
    ROAD_BUILDING = "road_building"
    YEAR_OF_PLENTY = "year_of_plenty"
    MONOPOLY = "monopoly"
    VICTORY_POINT = "victory_point"


#: A player's resource hand: resource type -> count held. Missing keys are
#: equivalent to a count of 0 (constructors should still populate all five
#: keys for clarity).
ResourceHand: TypeAlias = dict[ResourceType, int]

#: A player's development card holdings: dev card type -> count held.
DevCardHand: TypeAlias = dict[DevCardType, int]


class PlayerState(BaseModel):
    """Full server-side state for a single seated player.

    Buildings-remaining counts start at the standard Catan piece supply
    (5 settlements, 4 cities, 15 roads) and only ever decrease as pieces
    are placed (Nuke Mode returns a destroyed piece to this supply rather
    than to the board, per the plan's Nuke Mode section -- it does not
    replenish beyond the starting supply).
    """

    player_id: PlayerId
    nickname: str

    #: Fixed turn-order / display position, assigned at game start.
    #: 0-indexed.
    seat: int

    #: False while the player's WebSocket is disconnected; the player's
    #: seat and state are preserved so they can reconnect with their
    #: session token (see `app.core.connection_manager`).
    is_connected: bool = True

    #: True for a server-generated bot seat (see `app.core.room.Room
    #: .add_bot`, used by `app.api.websocket._handle_start_game` to
    #: auto-fill empty seats down to `GameSettings.player_count` at
    #: `START_GAME` time). A bot `PlayerState` is otherwise identical in
    #: shape to a human's -- same hand/dev cards/buildings/VP fields,
    #: participates in turn order and board ownership the same way, no
    #: special-cased "not a real player" bypasses anywhere in
    #: `rules_engine` except that its decisions are computed by
    #: `app.game.rules.bot_ai` instead of coming from a client action.
    #: `is_connected` is always `True` for a bot (no real socket is ever
    #: bound to one, but it should never look "disconnected" to the
    #: existing reconnect/turn-timer machinery).
    is_bot: bool = False

    hand: ResourceHand = Field(
        default_factory=lambda: {r: 0 for r in ResourceType}
    )

    #: Dev cards already owned and eligible to be played this turn.
    dev_cards: DevCardHand = Field(
        default_factory=lambda: {d: 0 for d in DevCardType}
    )
    #: Dev cards bought during the *current* turn. Standard Catan rules
    #: forbid playing a dev card the same turn it was bought (except when
    #: revealing a Victory Point card to win) -- the turn-end handler is
    #: expected to fold these into `dev_cards` and clear this mapping.
    dev_cards_bought_this_turn: DevCardHand = Field(
        default_factory=lambda: {d: 0 for d in DevCardType}
    )

    settlements_remaining: int = 5
    cities_remaining: int = 4
    roads_remaining: int = 15

    #: Public victory points: settlements + 2*cities + longest road (2) +
    #: largest army (2). Recomputed by `app.game.scoring` after every
    #: state-changing action. Does NOT include unrevealed Victory Point
    #: dev cards -- see `app.game.scoring` for the total-including-hidden
    #: calculation used only for the actor's own win-condition check.
    victory_points: int = 0

    #: Count of KNIGHT dev cards played this game; drives largest army.
    knights_played: int = 0

    has_longest_road: bool = False
    has_largest_army: bool = False
