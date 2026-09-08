"""Every Client -> Server WebSocket action: type enum + payload schemas.

Wire shape for a single client message is ``{type, payload}`` (the outer
protocol envelope also carries ``seq``/``ts``, but those are assigned by
the *server* on outbound `app.protocol.events` messages -- the client
never needs to send them; see that module's docstring for the full
envelope note). Each action below is modeled as its own small wrapper
model carrying a `Literal` `type` tag plus a `payload` sub-model, and
`ClientAction` is the pydantic discriminated union of all of them, so a
WS handler can do a single
``ClientAction.model_validate(raw_json)``-equivalent parse and get back
the right concrete type.

This file intentionally contains only schemas -- no validation logic
beyond what pydantic gives for free (field types/ranges). Actual legality
checks (is it this player's turn, do they own the resources, etc.) live
in `app.game.rules_engine.validate_action()`.
"""

from enum import Enum
from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, Field

from app.game.board import EdgeId, HexCoord, PlayerId, VertexId
from app.game.players import DevCardType, ResourceHand, ResourceType
from app.game.settings_schema import GameSettings


class ActionType(str, Enum):
    JOIN_ROOM = "JOIN_ROOM"
    LEAVE_ROOM = "LEAVE_ROOM"
    KICK_PLAYER = "KICK_PLAYER"
    UPDATE_SETTINGS = "UPDATE_SETTINGS"
    START_GAME = "START_GAME"
    ROLL_DICE = "ROLL_DICE"
    BUILD_SETTLEMENT = "BUILD_SETTLEMENT"
    BUILD_ROAD = "BUILD_ROAD"
    BUILD_CITY = "BUILD_CITY"
    BUY_DEV_CARD = "BUY_DEV_CARD"
    PLAY_DEV_CARD = "PLAY_DEV_CARD"
    BANK_TRADE = "BANK_TRADE"
    PORT_TRADE = "PORT_TRADE"
    PROPOSE_TRADE = "PROPOSE_TRADE"
    RESPOND_TRADE = "RESPOND_TRADE"
    MOVE_ROBBER = "MOVE_ROBBER"
    STEAL_RESOURCE = "STEAL_RESOURCE"
    DISCARD_CARDS = "DISCARD_CARDS"
    PLAY_NUKE = "PLAY_NUKE"
    END_TURN = "END_TURN"
    CHAT_MESSAGE = "CHAT_MESSAGE"


class EmptyPayload(BaseModel):
    """Payload for actions that carry no data beyond the envelope type."""


# ---------------------------------------------------------------------
# Payload models
# ---------------------------------------------------------------------


class JoinRoomPayload(BaseModel):
    """Room code is taken from the `/ws/{room_code}` connection URL, not
    repeated here; this action is what a *new* player sends right after
    the socket opens to claim a seat and nickname. A reconnecting player
    does not send this -- they connect with `?token=...` and the server
    rebinds them to their existing seat (see `app.core.connection_manager`).
    """

    nickname: str = Field(min_length=1, max_length=32)


class KickPlayerPayload(BaseModel):
    """Host-only. Rejects the target and frees their seat."""

    target_player_id: PlayerId


class UpdateSettingsPayload(BaseModel):
    """Host-only, lobby-phase-only. The host sends the complete desired
    settings object (not a partial patch); the server re-validates every
    field and broadcasts `SETTINGS_UPDATED` with the accepted result.
    """

    settings: GameSettings


class BuildSettlementPayload(BaseModel):
    vertex_id: VertexId


class BuildRoadPayload(BaseModel):
    edge_id: EdgeId


class BuildCityPayload(BaseModel):
    #: Must currently hold a SETTLEMENT belonging to the actor; upgraded
    #: in place to a CITY.
    vertex_id: VertexId


class PlayDevCardPayload(BaseModel):
    """`card_type`-specific fields are optional and only meaningful for
    the matching card. KNIGHT needs no extra field here -- playing it
    transitions the game to `AwaitingRobberPlacement` and the actual
    placement is a subsequent `MOVE_ROBBER` action, mirroring the
    post-dice-7 flow.
    """

    card_type: DevCardType

    #: Required, single resource, when `card_type == MONOPOLY`.
    monopoly_resource: ResourceType | None = None
    #: Required, exactly 2 entries (may repeat the same resource), when
    #: `card_type == YEAR_OF_PLENTY`.
    year_of_plenty_resources: list[ResourceType] | None = None
    #: Required, 1-2 entries, when `card_type == ROAD_BUILDING`. Each
    #: edge is placed for free, subject to normal road-placement legality.
    road_building_edges: list[EdgeId] | None = None


class BankTradePayload(BaseModel):
    """Trade with the bank at the standard 4:1 rate."""

    offered: ResourceHand
    requested: ResourceHand


class PortTradePayload(BaseModel):
    """Trade with the bank at a port rate (2:1 or 3:1). The specific port
    used is not named explicitly in the payload -- the server infers the
    best rate the actor is entitled to from their settlement/city
    placement (see `Board.ports` / `Board.buildings`) and validates
    `offered`/`requested` against it.
    """

    offered: ResourceHand
    requested: ResourceHand


class ProposeTradePayload(BaseModel):
    offered: ResourceHand
    requested: ResourceHand
    #: `None` means open to all other players; otherwise only these
    #: players may respond.
    target_player_ids: list[PlayerId] | None = None


class RespondTradePayload(BaseModel):
    trade_id: str
    accept: bool


class MoveRobberPayload(BaseModel):
    hex: HexCoord


class StealResourcePayload(BaseModel):
    target_player_id: PlayerId


class DiscardCardsPayload(BaseModel):
    """Resources the actor is discarding in response to
    `AwaitingDiscard`. The total count must exactly match the amount the
    actor currently owes per `AwaitingDiscard.required_counts`.
    """

    resources: ResourceHand


class PlayNukePayload(BaseModel):
    """See the plan's "Nuke Mode" section. Precondition (actor holds >=2
    of each of the 5 resources) is enforced by `rules_engine`, not here.
    """

    target_player_id: PlayerId
    #: Settlement or city to destroy; must belong to `target_player_id`.
    target_vertex_id: VertexId
    #: Road to destroy; must belong to `target_player_id`.
    target_edge_id: EdgeId


class ChatMessagePayload(BaseModel):
    text: str = Field(min_length=1, max_length=500)


# ---------------------------------------------------------------------
# Envelope wrappers ({type, payload}) and the discriminated union
# ---------------------------------------------------------------------


class JoinRoomAction(BaseModel):
    type: Literal[ActionType.JOIN_ROOM] = ActionType.JOIN_ROOM
    payload: JoinRoomPayload


class LeaveRoomAction(BaseModel):
    type: Literal[ActionType.LEAVE_ROOM] = ActionType.LEAVE_ROOM
    payload: EmptyPayload = EmptyPayload()


class KickPlayerAction(BaseModel):
    type: Literal[ActionType.KICK_PLAYER] = ActionType.KICK_PLAYER
    payload: KickPlayerPayload


class UpdateSettingsAction(BaseModel):
    type: Literal[ActionType.UPDATE_SETTINGS] = ActionType.UPDATE_SETTINGS
    payload: UpdateSettingsPayload


class StartGameAction(BaseModel):
    type: Literal[ActionType.START_GAME] = ActionType.START_GAME
    payload: EmptyPayload = EmptyPayload()


class RollDiceAction(BaseModel):
    type: Literal[ActionType.ROLL_DICE] = ActionType.ROLL_DICE
    payload: EmptyPayload = EmptyPayload()


class BuildSettlementAction(BaseModel):
    type: Literal[ActionType.BUILD_SETTLEMENT] = ActionType.BUILD_SETTLEMENT
    payload: BuildSettlementPayload


class BuildRoadAction(BaseModel):
    type: Literal[ActionType.BUILD_ROAD] = ActionType.BUILD_ROAD
    payload: BuildRoadPayload


class BuildCityAction(BaseModel):
    type: Literal[ActionType.BUILD_CITY] = ActionType.BUILD_CITY
    payload: BuildCityPayload


class BuyDevCardAction(BaseModel):
    type: Literal[ActionType.BUY_DEV_CARD] = ActionType.BUY_DEV_CARD
    payload: EmptyPayload = EmptyPayload()


class PlayDevCardAction(BaseModel):
    type: Literal[ActionType.PLAY_DEV_CARD] = ActionType.PLAY_DEV_CARD
    payload: PlayDevCardPayload


class BankTradeAction(BaseModel):
    type: Literal[ActionType.BANK_TRADE] = ActionType.BANK_TRADE
    payload: BankTradePayload


class PortTradeAction(BaseModel):
    type: Literal[ActionType.PORT_TRADE] = ActionType.PORT_TRADE
    payload: PortTradePayload


class ProposeTradeAction(BaseModel):
    type: Literal[ActionType.PROPOSE_TRADE] = ActionType.PROPOSE_TRADE
    payload: ProposeTradePayload


class RespondTradeAction(BaseModel):
    type: Literal[ActionType.RESPOND_TRADE] = ActionType.RESPOND_TRADE
    payload: RespondTradePayload


class MoveRobberAction(BaseModel):
    type: Literal[ActionType.MOVE_ROBBER] = ActionType.MOVE_ROBBER
    payload: MoveRobberPayload


class StealResourceAction(BaseModel):
    type: Literal[ActionType.STEAL_RESOURCE] = ActionType.STEAL_RESOURCE
    payload: StealResourcePayload


class DiscardCardsAction(BaseModel):
    type: Literal[ActionType.DISCARD_CARDS] = ActionType.DISCARD_CARDS
    payload: DiscardCardsPayload


class PlayNukeAction(BaseModel):
    type: Literal[ActionType.PLAY_NUKE] = ActionType.PLAY_NUKE
    payload: PlayNukePayload


class EndTurnAction(BaseModel):
    type: Literal[ActionType.END_TURN] = ActionType.END_TURN
    payload: EmptyPayload = EmptyPayload()


class ChatMessageAction(BaseModel):
    type: Literal[ActionType.CHAT_MESSAGE] = ActionType.CHAT_MESSAGE
    payload: ChatMessagePayload


#: Discriminated union of every possible Client -> Server message.
#: A WS handler validates one incoming frame against this to get back
#: the correctly-typed action + payload in one step.
ClientAction: TypeAlias = Annotated[
    JoinRoomAction
    | LeaveRoomAction
    | KickPlayerAction
    | UpdateSettingsAction
    | StartGameAction
    | RollDiceAction
    | BuildSettlementAction
    | BuildRoadAction
    | BuildCityAction
    | BuyDevCardAction
    | PlayDevCardAction
    | BankTradeAction
    | PortTradeAction
    | ProposeTradeAction
    | RespondTradeAction
    | MoveRobberAction
    | StealResourceAction
    | DiscardCardsAction
    | PlayNukeAction
    | EndTurnAction
    | ChatMessageAction,
    Field(discriminator="type"),
]
