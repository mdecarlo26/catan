"""Host-configurable game settings and the generic metadata registry that
drives the lobby settings UI.

`GameSettings` is the typed, validated shape used by the server and by
`UPDATE_SETTINGS` / `SETTINGS_UPDATED` payloads (see `app.game.actions`
and `app.protocol.events`). `SETTINGS_REGISTRY` is a parallel, generic
description of the same fields ({key, type, default, description, ...})
that the frontend lobby settings form (`frontend/src/components/Lobby/`)
can render without hardcoding a widget per setting -- adding a new toggle
later means adding one `GameSettings` field plus one `SettingFieldMeta`
entry, not touching UI code.
"""

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field


class GameSettings(BaseModel):
    """All host-configurable settings for a room, per the plan's
    "Settings / Toggle System" section. Locked (copied into
    `app.game.state.GameState.settings`) at `START_GAME` and immutable
    for the rest of that game.
    """

    #: Number of seated players required to start. Extended ranges
    #: (2p, 7-8p) use house-ruled defaults per the plan's "Board Layout
    #: for 2p and 7-8p" section rather than official Catan rules, which
    #: only natively cover 3-4 (6 with the 5-6p expansion).
    player_count: int = Field(default=4, ge=2, le=8)

    #: Victory points required to win. Standard Catan default is 10.
    victory_points_target: int = Field(default=10, ge=3)

    #: Named key into the board layout registry
    #: (`app.game.rules.board_layouts`), e.g. "standard", "expansion_5_6",
    #: "extended_7_8", "two_player", or a generation mode like "random" /
    #: "fixed". Deliberately a plain string rather than a hardcoded enum:
    #: the registry is designed to be extended with new named layouts per
    #: player-count bucket without changing this schema. Validity against
    #: the current `player_count` bucket is checked at board-generation
    #: time, not by this field's type.
    board_layout: str = "random"

    #: Enables an alternate initial-setup/pacing strategy. Selected via
    #: the `SETUP_STRATEGIES` registry in `app.game.rules.setup_strategies`
    #: (`RushModeSetup` vs. the default `SnakeDraftSetup`). Real gameplay
    #: behavior is deliberately deferred per the plan -- this flag only
    #: selects which strategy object is used.
    rush_mode: bool = False

    #: Enables the custom "nuke" house rule (`PLAY_NUKE` action). See the
    #: plan's "Nuke Mode" section and `app.game.rules.nuke_mode`.
    nuke_mode: bool = False

    #: Enables the extra build-only round between turns from the official
    #: 5-6 player expansion (see `app.game.state.Phase.SPECIAL_BUILD` and
    #: `rules_engine._apply_end_turn`). `None` means "unset": the host has
    #: not explicitly chosen, so the effective value -- computed by
    #: `rules_engine._effective_special_build_phase` -- is derived as
    #: `player_count >= 5` at game-start time. Once the host sets this
    #: explicitly via `UPDATE_SETTINGS`, it becomes a concrete override
    #: that sticks regardless of `player_count`.
    special_build_phase: bool | None = None

    #: Card-count threshold that triggers a mandatory discard for a
    #: player when a 7 is rolled (standard Catan default: 7).
    discard_limit: int = Field(default=7, ge=1)

    #: Friendly-robber house rule: moving the robber still blocks a
    #: hex's production, but never steals a card from the players
    #: settled there. Selects `app.game.rules.robber_strategies
    #: .friendly_robber_steal_candidates` instead of the normal
    #: candidate-gathering in `rules_engine._apply_move_robber`.
    friendly_robber: bool = False

    #: Seconds a turn may sit idle (no action from the current player)
    #: before the stalled-turn timer force-ends it -- see
    #: `app.game.rules.turn_timer.should_force_end_turn`, wired up by
    #: `app.api.websocket`'s per-room background task. `0` disables the
    #: timer.
    turn_timer_seconds: int = Field(default=120, ge=0)


class SettingFieldType(str, Enum):
    """The primitive type a `SettingFieldMeta` entry describes, used by
    the lobby UI to pick a form widget (number input, checkbox, select).
    """

    INT = "int"
    BOOL = "bool"
    STRING = "string"


class SettingFieldMeta(BaseModel):
    """Generic UI-facing metadata for one `GameSettings` field."""

    #: Must exactly match a `GameSettings` field name.
    key: str
    type: SettingFieldType
    default: Any
    #: Human-readable description shown in the lobby settings form.
    description: str
    #: Inclusive bounds, only meaningful when `type == SettingFieldType.INT`.
    min_value: int | None = None
    max_value: int | None = None


#: Ordered so the lobby UI can render settings in a sensible default
#: order without needing its own curation logic. Every `GameSettings`
#: field must have exactly one corresponding entry here.
SETTINGS_REGISTRY: list[SettingFieldMeta] = [
    SettingFieldMeta(
        key="player_count",
        type=SettingFieldType.INT,
        default=4,
        description="Number of players in the game.",
        min_value=2,
        max_value=8,
    ),
    SettingFieldMeta(
        key="victory_points_target",
        type=SettingFieldType.INT,
        default=10,
        description="Victory points required to win the game.",
        min_value=3,
        max_value=None,
    ),
    SettingFieldMeta(
        key="board_layout",
        type=SettingFieldType.STRING,
        default="random",
        description=(
            "Board layout / generation strategy, selected from the "
            "registry of layouts available for the current player count."
        ),
    ),
    SettingFieldMeta(
        key="rush_mode",
        type=SettingFieldType.BOOL,
        default=False,
        description=(
            "Alternate, faster initial-setup strategy. Exact behavior is "
            "still being designed; currently a placeholder toggle."
        ),
    ),
    SettingFieldMeta(
        key="nuke_mode",
        type=SettingFieldType.BOOL,
        default=False,
        description=(
            "Custom house rule: a player holding 2 of each resource may "
            "destroy an opponent's road or settlement/city."
        ),
    ),
    SettingFieldMeta(
        key="special_build_phase",
        type=SettingFieldType.BOOL,
        default=False,
        description=(
            "Extra build-only round between turns (official 5-6 player "
            "expansion rule). Auto-enabled when player count is 5 or "
            "more unless explicitly overridden here."
        ),
    ),
    SettingFieldMeta(
        key="discard_limit",
        type=SettingFieldType.INT,
        default=7,
        description=(
            "Card count threshold that triggers a mandatory discard "
            "when a 7 is rolled."
        ),
        min_value=1,
        max_value=None,
    ),
    SettingFieldMeta(
        key="friendly_robber",
        type=SettingFieldType.BOOL,
        default=False,
        description=(
            "Friendly robber: moving the robber still blocks a hex's "
            "production, but never steals a card."
        ),
    ),
    SettingFieldMeta(
        key="turn_timer_seconds",
        type=SettingFieldType.INT,
        default=120,
        description=(
            "Seconds a turn may sit idle before it's automatically "
            "ended. 0 disables the timer."
        ),
        min_value=0,
        max_value=None,
    ),
]
