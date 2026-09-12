"""`/ws/{room_code}?token=...` -- the realtime game WebSocket endpoint.

Connection handshake
---------------------
On connect (after `websocket.accept()`):

- If `room_code` doesn't name a live room, send an `ERROR` and close.
- If a `token` query param is present and validates against the room's
  `SessionStore`, this is a **reconnect**: `ConnectionManager.reconnect`
  rebinds the socket to the existing `player_id`/seat, we send that
  player a private catch-up (`ROOM_STATE` pre-game, else a masked
  `STATE_SNAPSHOT`), and broadcast `PLAYER_RECONNECTED`. Note this also
  covers the *host's very first connection*: `POST /api/rooms` already
  seats the host and issues their token (see `app.api.rooms`), so their
  first WS connection is, from this endpoint's point of view,
  indistinguishable from -- and handled by -- the reconnect path.
- Otherwise (`token` absent or invalid) the connection has no
  `player_id` yet; the first message it must send is a `JOIN_ROOM`
  action (nickname), matching `frontend/src/app/Lobby.tsx`'s flow.
  `_handle_join` seats the new player, issues a token, and replies with
  a `SESSION_ESTABLISHED` message (see below) before broadcasting
  `PLAYER_JOINED`/`ROOM_STATE`.

A protocol gap this closes (flagged for the frontend/wiring pass)
---------------------------------------------------------------------
`app.protocol.events` (as merged) has no event that ever hands a
freshly-joined, non-host player their own `player_id`/`token` --
`frontend/src/api/session.ts`'s `StoredSession` has nowhere to come from
for that player. This module adds `EventType.SESSION_ESTABLISHED` /
`SessionEstablishedPayload` to `app.protocol.events` to close that gap:
sent to exactly the joining socket, immediately after `JOIN_ROOM`
succeeds. `frontend/src/types/protocol.ts` and `gameStore.ts` don't know
about it yet -- that's real frontend work for whoever wires this up next
(see this backend integration's final report).

`seq` and connection-private messages
--------------------------------------
Per `ARCHITECTURE.md`, `seq` is "a monotonically increasing integer
assigned per room by the server"; the frontend's `wsClient.ts` treats
`envelope.seq > lastSeq + 1` (per *that socket's own* observed stream)
as a dropped-message signal and forces a resync. That invariant only
holds if every `seq` value a room ever issues is actually delivered, in
*some* form, to every currently-connected seat -- which is true for
ordinary broadcasts (`PLAYER_JOINED`, `STATE_SNAPSHOT` rounds, ...) but
would be violated by a message meant for exactly one socket (a private
`ERROR`, a reconnecting player's catch-up state, a joiner's
`SESSION_ESTABLISHED`) if it consumed a fresh `room.next_seq()` that no
other connected client ever sees -- every *other* client's next real
broadcast would then look like a gap.

So: broadcasts use `room.next_seq()` (a new number, delivered to every
connected seat, possibly with per-recipient-masked content -- see
`_dispatch_rule_events`'s `RESOURCE_STOLEN` handling and
`_broadcast_snapshots`). Connection-private messages reuse
`room.last_seq` (the most recently issued number, unincremented) via
`_send_private`/`_send_catch_up`/`_handle_join`'s `SESSION_ESTABLISHED`
send.

Dispatch
--------
`JOIN_ROOM`/`LEAVE_ROOM`/`KICK_PLAYER`/`UPDATE_SETTINGS`/`START_GAME`
are room-lifecycle actions handled directly against `app.core.room.Room`
(mirroring `app.game.rules_engine`'s own module docstring: these never
reach the rules engine). Every other `ActionType` is routed through
`app.game.rules_engine.validate_and_apply` against the room's
`GameState`; on success, the returned `RuleEvent`s are mapped to
`ServerEvent`s and broadcast, followed by one fresh masked
`STATE_SNAPSHOT` round (see `app.serialization.to_client_view`).

Stalled-turn timer
-------------------
`_turn_timer_loop` is a per-room background `asyncio.Task`, started at
`START_GAME` and cancelled at `GAME_OVER` (or if the room is evicted out
from under it), that polls `app.game.rules.turn_timer
.should_force_end_turn` against each room's `settings.turn_timer_seconds`
and the wall-clock time of that room's last successful action. When it
fires, it synthesizes a `ROLL_DICE` (if the stalled player hasn't rolled
yet) or `END_TURN` (if they're in `MAIN` with nothing pending) on that
player's behalf, broadcasts `TURN_TIMER_EXPIRED` plus whatever events
that synthesized action produced. A stall while a robber
discard/placement/steal is pending is deliberately left alone -- those
require a specific player's own choice (which cards to discard, whom to
rob) that can't be safely auto-resolved without a real "auto-play"
policy, which is out of scope here. This entire loop is a no-op while
`settings.rush_mode` is on -- rush mode has no "current player" for it to
stall/force-advance (see `_force_advance_stalled_turn`'s early return).

Rush-mode auto-roll timer
--------------------------
`_rush_roll_loop` is a second per-room background `asyncio.Task`,
started/cancelled alongside `_turn_timer_loop`, that mirrors its exact
shape for a different job: once a rush-mode game reaches `Phase.MAIN`, it
polls `app.game.rules.rush_timer.should_auto_roll` against
`settings.rush_roll_interval_seconds` and
`app.game.state.GameState.last_dice_roll_ts` (that field is already the
authoritative "when did the last roll happen" timestamp -- primed to
`time.time()` the moment rush-mode `Phase.MAIN` begins by `RushModeSetup
.on_setup_complete`, and updated on every subsequent auto-roll -- so this
loop reads it directly rather than keeping its own separately-seeded
copy; see `_rush_roll_loop`'s own docstring for a real bug that
independent-copy approach had). On a `True` result it calls
`app.game.rules_engine.apply_rush_auto_roll` (never a synthesized client
action -- there's no "actor" for a system-driven roll) and broadcasts the
resulting events plus a fresh snapshot round, exactly like any other
gameplay action. For a non-rush-mode room, or a rush-mode room still in
`Phase.SETUP`, the loop simply does nothing that poll.
"""

from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, TypeAdapter, ValidationError

from app.config import settings as app_settings
from app.core.registry import connection_manager, room_manager
from app.core.room import Room, SettingsLockedError
from app.game import board_generator, dev_cards, rules_engine
from app.game.actions import (
    ActionType,
    BlackjackDeclineAction,
    BlackjackStandAction,
    ClientAction,
    EndTurnAction,
    JoinRoomPayload,
    KickPlayerPayload,
    RollDiceAction,
    UpdateSettingsPayload,
)
from app.game.players import PlayerState, ResourceType
from app.game.rules import rush_timer, turn_timer
from app.game.rules_engine import RuleViolation
from app.game.state import Bank, GameState, Phase
from app.protocol.events import (
    BlackjackBetDeclinedEvent,
    BlackjackBetPlacedEvent,
    BlackjackDealerRevealedEvent,
    BlackjackHandUpdatedEvent,
    BlackjackRoundResolvedEvent,
    BlackjackRoundStartedEvent,
    DevCardCountChangedEvent,
    DiceRolledEvent,
    DiscardRequiredEvent,
    ErrorEvent,
    ErrorPayload,
    EventType,
    GameOverEvent,
    GameStartedEvent,
    GameStartedPayload,
    LargestArmyChangedEvent,
    LongestRoadChangedEvent,
    NukeDroppedEvent,
    PlayerDisconnectedEvent,
    PlayerDisconnectedPayload,
    PlayerJoinedEvent,
    PlayerJoinedPayload,
    PlayerKickedEvent,
    PlayerKickedPayload,
    PlayerLeftEvent,
    PlayerLeftPayload,
    PlayerReconnectedEvent,
    PlayerReconnectedPayload,
    PlayerSummary,
    ResourceStolenEvent,
    ResourceStolenPayload,
    ResourcesDistributedEvent,
    RobberMovedEvent,
    RoomStateEvent,
    RoomStatePayload,
    SessionEstablishedEvent,
    SessionEstablishedPayload,
    SettingsUpdatedEvent,
    SettingsUpdatedPayload,
    StateSnapshotEvent,
    StateSnapshotPayload,
    TradeOfferedEvent,
    TradeResolvedEvent,
    TurnTimerExpiredEvent,
    TurnTimerExpiredPayload,
)
from app.serialization import to_client_view

router = APIRouter()

_action_adapter: TypeAdapter[ClientAction] = TypeAdapter(ClientAction)

#: EventType -> the matching ServerEvent wrapper, for `RuleEvent`s whose
#: payload is identical for every recipient (everything except
#: `RESOURCE_STOLEN`, which is masked per-recipient -- see
#: `_dispatch_rule_events`).
_SIMPLE_EVENT_WRAPPERS: dict[EventType, type[BaseModel]] = {
    EventType.DICE_ROLLED: DiceRolledEvent,
    EventType.RESOURCES_DISTRIBUTED: ResourcesDistributedEvent,
    EventType.ROBBER_MOVED: RobberMovedEvent,
    EventType.DISCARD_REQUIRED: DiscardRequiredEvent,
    EventType.TRADE_OFFERED: TradeOfferedEvent,
    EventType.TRADE_RESOLVED: TradeResolvedEvent,
    EventType.DEV_CARD_COUNT_CHANGED: DevCardCountChangedEvent,
    EventType.NUKE_DROPPED: NukeDroppedEvent,
    EventType.BLACKJACK_ROUND_STARTED: BlackjackRoundStartedEvent,
    EventType.BLACKJACK_BET_PLACED: BlackjackBetPlacedEvent,
    EventType.BLACKJACK_BET_DECLINED: BlackjackBetDeclinedEvent,
    EventType.BLACKJACK_HAND_UPDATED: BlackjackHandUpdatedEvent,
    EventType.BLACKJACK_DEALER_REVEALED: BlackjackDealerRevealedEvent,
    EventType.BLACKJACK_ROUND_RESOLVED: BlackjackRoundResolvedEvent,
    EventType.LONGEST_ROAD_CHANGED: LongestRoadChangedEvent,
    EventType.LARGEST_ARMY_CHANGED: LargestArmyChangedEvent,
    EventType.GAME_OVER: GameOverEvent,
}

#: room_code -> background stalled-turn-timer task.
_timer_tasks: dict[str, asyncio.Task] = {}
#: room_code -> wall-clock time.time() of the last successful gameplay
#: (or lifecycle) action in that room. Drives the turn timer.
_last_action_ts: dict[str, float] = {}

#: room_code -> background rush-mode auto-roll task. See the module
#: docstring's "Rush-mode auto-roll timer" section.
_rush_roll_tasks: dict[str, asyncio.Task] = {}


def _now() -> float:
    return time.time()


def _touch(room_code: str) -> None:
    _last_action_ts[room_code] = _now()


@dataclass
class _ConnState:
    """Mutable per-connection state for one open WS socket's lifetime."""

    player_id: str | None = None
    #: Set once this connection's own action handling has already
    #: cleanly torn down the seat/socket binding (LEAVE_ROOM, or being
    #: the target of a KICK_PLAYER), so the outer `finally` block
    #: shouldn't also run disconnect bookkeeping for it.
    left: bool = False


# ---------------------------------------------------------------------
# Low-level send helpers
# ---------------------------------------------------------------------


async def _send_event(socket: WebSocket | None, event: BaseModel) -> None:
    if socket is None:
        return
    try:
        await socket.send_text(event.model_dump_json())
    except Exception:
        # Best-effort: a socket that's gone stale surfaces on its own
        # receive loop (WebSocketDisconnect), not here.
        pass


async def _broadcast_event(room: Room, event: BaseModel) -> None:
    """Send `event` (already carrying a real `room.next_seq()` seq) to
    every currently-connected seated player.
    """
    for player_id in list(room.seats.keys()):
        await _send_event(connection_manager.get_socket(player_id), event)


async def _send_private_error(websocket: WebSocket, room: Room, code: str, message: str) -> None:
    await _send_event(
        websocket,
        ErrorEvent(seq=room.last_seq, ts=_now(), payload=ErrorPayload(code=code, message=message)),
    )


def _room_state_payload(room: Room) -> RoomStatePayload:
    phase = room.game_state.phase if room.game_state is not None else Phase.LOBBY
    return RoomStatePayload(
        room_code=room.room_code,
        host_player_id=room.host_player_id,
        players=[
            PlayerSummary(
                player_id=seat.player_id,
                nickname=seat.nickname,
                seat=seat.seat,
                is_connected=seat.is_connected,
                is_host=seat.is_host,
            )
            for seat in sorted(room.seats.values(), key=lambda s: s.seat)
        ],
        settings=room.settings,
        phase=phase,
    )


async def _send_catch_up(websocket: WebSocket, room: Room, player_id: str) -> None:
    """Private (reused-seq) message sent to a reconnecting player so
    they're caught up immediately, without waiting for the next
    broadcast: full lobby roster pre-game, or their own masked
    `STATE_SNAPSHOT` once a game exists.
    """
    seq = room.last_seq
    ts = _now()
    if room.game_state is None:
        await _send_event(websocket, RoomStateEvent(seq=seq, ts=ts, payload=_room_state_payload(room)))
    else:
        view = to_client_view(room.game_state, player_id)
        await _send_event(
            websocket, StateSnapshotEvent(seq=seq, ts=ts, payload=StateSnapshotPayload(state=view))
        )


async def _broadcast_snapshots(room: Room, state: GameState) -> None:
    """One logical `STATE_SNAPSHOT` round: a single `room.next_seq()`,
    reused across every connected seat's individually-masked view.
    """
    seq = room.next_seq()
    ts = _now()
    for player_id in list(room.seats.keys()):
        socket = connection_manager.get_socket(player_id)
        if socket is None:
            continue
        view = to_client_view(state, player_id)
        await _send_event(
            socket, StateSnapshotEvent(seq=seq, ts=ts, payload=StateSnapshotPayload(state=view))
        )


async def _dispatch_rule_events(room: Room, events: list[rules_engine.RuleEvent]) -> None:
    """Map each `RuleEvent` from `rules_engine.apply()` to its
    `ServerEvent` wrapper and broadcast it. `RESOURCE_STOLEN` is the one
    payload that's per-recipient masked (per its own docstring: the
    stolen resource type is only revealed to the actor/victim), so it's
    handled separately rather than through `_SIMPLE_EVENT_WRAPPERS`.
    """
    for ev in events:
        if ev.type == EventType.RESOURCE_STOLEN:
            payload: ResourceStolenPayload = ev.payload  # type: ignore[assignment]
            seq = room.next_seq()
            ts = _now()
            for player_id in list(room.seats.keys()):
                socket = connection_manager.get_socket(player_id)
                if socket is None:
                    continue
                visible = (
                    payload
                    if player_id in (payload.actor, payload.victim)
                    else payload.model_copy(update={"resource": None})
                )
                await _send_event(socket, ResourceStolenEvent(seq=seq, ts=ts, payload=visible))
            continue

        wrapper_cls = _SIMPLE_EVENT_WRAPPERS.get(ev.type)
        if wrapper_cls is None:
            continue
        seq = room.next_seq()
        await _broadcast_event(room, wrapper_cls(seq=seq, ts=_now(), payload=ev.payload))


# ---------------------------------------------------------------------
# Lifecycle action handlers (JOIN_ROOM / LEAVE_ROOM / KICK_PLAYER /
# UPDATE_SETTINGS / START_GAME) -- never routed through rules_engine.
# ---------------------------------------------------------------------


async def _handle_join(websocket: WebSocket, room: Room, conn: _ConnState, payload: JoinRoomPayload) -> bool:
    if conn.player_id is not None:
        await _send_private_error(websocket, room, "already_joined", "This connection already joined.")
        return False
    if room.game_state is not None:
        await _send_private_error(
            websocket, room, "game_already_started", "Cannot join after the game has started."
        )
        return False
    if len(room.seats) >= 8:
        await _send_private_error(websocket, room, "room_full", "This room already has 8 players.")
        return False

    new_player_id = str(uuid.uuid4())
    seat, token = room.add_player(new_player_id, payload.nickname)
    connection_manager.connect(room, new_player_id, websocket)
    conn.player_id = new_player_id
    _touch(room.room_code)

    await _send_event(
        websocket,
        SessionEstablishedEvent(
            seq=room.last_seq,
            ts=_now(),
            payload=SessionEstablishedPayload(
                player_id=new_player_id, token=token, room_code=room.room_code
            ),
        ),
    )

    joined_seq = room.next_seq()
    await _broadcast_event(
        room,
        PlayerJoinedEvent(
            seq=joined_seq,
            ts=_now(),
            payload=PlayerJoinedPayload(
                player=PlayerSummary(
                    player_id=seat.player_id,
                    nickname=seat.nickname,
                    seat=seat.seat,
                    is_connected=seat.is_connected,
                    is_host=seat.is_host,
                )
            ),
        ),
    )
    state_seq = room.next_seq()
    await _broadcast_event(
        room, RoomStateEvent(seq=state_seq, ts=_now(), payload=_room_state_payload(room))
    )
    return False


async def _handle_leave(websocket: WebSocket, room: Room, conn: _ConnState) -> bool:
    player_id = conn.player_id
    assert player_id is not None
    if room.game_state is None:
        freed = room.remove_player(player_id)
        connection_manager.forget(player_id)
        conn.left = True
        if freed is not None:
            left_seq = room.next_seq()
            await _broadcast_event(
                room, PlayerLeftEvent(seq=left_seq, ts=_now(), payload=PlayerLeftPayload(player_id=player_id))
            )
            state_seq = room.next_seq()
            await _broadcast_event(
                room, RoomStateEvent(seq=state_seq, ts=_now(), payload=_room_state_payload(room))
            )
    else:
        # Leaving mid-game is treated the same as a disconnect: the seat
        # (and the player's place in turn_order) is preserved so a
        # reconnect can still resume it, rather than corrupting turn
        # order / board ownership by fully removing a seated player from
        # an in-progress game.
        result = connection_manager.disconnect(room, player_id)
        conn.left = True
        if result.ok:
            seq = room.next_seq()
            await _broadcast_event(
                room,
                PlayerDisconnectedEvent(seq=seq, ts=_now(), payload=PlayerDisconnectedPayload(player_id=player_id)),
            )
    return True


async def _handle_kick(websocket: WebSocket, room: Room, conn: _ConnState, payload: KickPlayerPayload) -> bool:
    actor_seat = room.seats.get(conn.player_id) if conn.player_id else None
    if actor_seat is None or not actor_seat.is_host:
        await _send_private_error(websocket, room, "not_host", "Only the host can kick players.")
        return False
    if room.game_state is not None:
        await _send_private_error(
            websocket, room, "game_already_started", "Cannot kick players after the game has started."
        )
        return False
    target_id = payload.target_player_id
    if target_id == conn.player_id:
        await _send_private_error(websocket, room, "invalid_target", "Cannot kick yourself.")
        return False

    target_socket = connection_manager.get_socket(target_id)
    freed = room.kick_player(target_id)
    connection_manager.forget(target_id)
    if freed is None:
        await _send_private_error(websocket, room, "unknown_player", "That player isn't seated.")
        return False

    kicked_seq = room.next_seq()
    await _broadcast_event(
        room,
        PlayerKickedEvent(seq=kicked_seq, ts=_now(), payload=PlayerKickedPayload(player_id=target_id, reason=None)),
    )
    state_seq = room.next_seq()
    await _broadcast_event(room, RoomStateEvent(seq=state_seq, ts=_now(), payload=_room_state_payload(room)))

    if target_socket is not None and target_socket is not websocket:
        try:
            await target_socket.close(code=4403)
        except Exception:
            pass
    return False


async def _handle_update_settings(
    websocket: WebSocket, room: Room, conn: _ConnState, payload: UpdateSettingsPayload
) -> bool:
    actor_seat = room.seats.get(conn.player_id) if conn.player_id else None
    if actor_seat is None or not actor_seat.is_host:
        await _send_private_error(websocket, room, "not_host", "Only the host can update settings.")
        return False
    try:
        updated = room.update_settings(payload.settings)
    except SettingsLockedError:
        await _send_private_error(
            websocket, room, "settings_locked", "Settings are locked once the game has started."
        )
        return False
    seq = room.next_seq()
    await _broadcast_event(room, SettingsUpdatedEvent(seq=seq, ts=_now(), payload=SettingsUpdatedPayload(settings=updated)))
    return False


async def _handle_start_game(websocket: WebSocket, room: Room, conn: _ConnState) -> bool:
    actor_seat = room.seats.get(conn.player_id) if conn.player_id else None
    if actor_seat is None or not actor_seat.is_host:
        await _send_private_error(websocket, room, "not_host", "Only the host can start the game.")
        return False
    if room.game_state is not None:
        await _send_private_error(websocket, room, "already_started", "The game has already started.")
        return False
    player_count = len(room.seats)
    if not (2 <= player_count <= 8):
        await _send_private_error(
            websocket, room, "invalid_player_count", "Need between 2 and 8 seated players to start."
        )
        return False

    # `player_count` is locked to however many seats actually filled, not
    # whatever value the host's settings form last had -- board
    # generation is keyed off the real seat count.
    locked_settings = room.settings.model_copy(update={"player_count": player_count})
    try:
        board = board_generator.generate_board_for_settings(locked_settings)
    except ValueError as exc:
        await _send_private_error(websocket, room, "invalid_board_layout", str(exc))
        return False

    ordered_seats = sorted(room.seats.values(), key=lambda s: s.seat)
    turn_order = [seat.player_id for seat in ordered_seats]
    players = {
        seat.player_id: PlayerState(player_id=seat.player_id, nickname=seat.nickname, seat=seat.seat)
        for seat in ordered_seats
    }
    bank = Bank(
        resources={resource: 19 for resource in ResourceType},
        dev_card_pile=dev_cards.build_deck(player_count),
    )
    state = GameState(
        room_code=room.room_code,
        settings=locked_settings,
        phase=Phase.SETUP,
        turn_order=turn_order,
        board=board,
        bank=bank,
        players=players,
    )

    room.settings = locked_settings
    room.game_state = state
    room.lock_settings()
    _touch(room.room_code)

    seq = room.next_seq()
    await _broadcast_event(room, GameStartedEvent(seq=seq, ts=_now(), payload=GameStartedPayload(turn_order=turn_order)))
    await _broadcast_snapshots(room, state)

    _timer_tasks[room.room_code] = asyncio.create_task(_turn_timer_loop(room))
    _rush_roll_tasks[room.room_code] = asyncio.create_task(_rush_roll_loop(room))
    return False


async def _handle_gameplay_action(websocket: WebSocket, room: Room, conn: _ConnState, action: ClientAction) -> bool:
    state = room.game_state
    if state is None:
        await _send_private_error(websocket, room, "game_not_started", "The game hasn't started yet.")
        return False

    assert conn.player_id is not None
    try:
        events = rules_engine.validate_and_apply(state, conn.player_id, action)
    except RuleViolation as exc:
        await _send_private_error(websocket, room, exc.code, exc.message)
        return False

    _touch(room.room_code)
    await _dispatch_rule_events(room, events)
    await _broadcast_snapshots(room, state)

    if state.phase == Phase.GAME_OVER:
        room.mark_finished()
        _cancel_timer(room.room_code)
        _cancel_rush_roll(room.room_code)

    return False


async def _handle_action(websocket: WebSocket, room: Room, conn: _ConnState, action: ClientAction) -> bool:
    """Dispatch one parsed `ClientAction`. Returns True if the socket
    should be closed (the connection's own receive loop should stop).
    """
    if action.type == ActionType.JOIN_ROOM:
        return await _handle_join(websocket, room, conn, action.payload)

    if conn.player_id is None:
        await _send_private_error(
            websocket, room, "not_joined", "Send JOIN_ROOM (or reconnect with a token) first."
        )
        return False

    if action.type == ActionType.LEAVE_ROOM:
        return await _handle_leave(websocket, room, conn)
    if action.type == ActionType.KICK_PLAYER:
        return await _handle_kick(websocket, room, conn, action.payload)
    if action.type == ActionType.UPDATE_SETTINGS:
        return await _handle_update_settings(websocket, room, conn, action.payload)
    if action.type == ActionType.START_GAME:
        return await _handle_start_game(websocket, room, conn)

    return await _handle_gameplay_action(websocket, room, conn, action)


# ---------------------------------------------------------------------
# Stalled-turn timer
# ---------------------------------------------------------------------


def _cancel_timer(room_code: str) -> None:
    task = _timer_tasks.pop(room_code, None)
    if task is not None:
        task.cancel()
    _last_action_ts.pop(room_code, None)


def _cancel_rush_roll(room_code: str) -> None:
    task = _rush_roll_tasks.pop(room_code, None)
    if task is not None:
        task.cancel()


async def _force_advance_stalled_blackjack(room: Room, state: GameState) -> None:
    """Blackjack-on-7's (`GameSettings.blackjack_mode`) stalled-timer
    handling, reusing `settings.turn_timer_seconds` per the plan's
    explicit call to lean on this same pattern rather than a new
    setting: auto-decline every player still owing a betting response
    once the window stalls, or auto-stand whoever's currently up in the
    hit/stand queue -- "auto-X on timeout, then advance".

    Unlike `_force_advance_stalled_turn`'s single current-player/forced-
    action shape, a stalled `status == "betting"` window can require
    forcing *every* remaining non-responder at once (mirroring
    `AwaitingDiscard`'s "several players can owe at the same time" shape,
    not `AwaitingRobberPlacement`'s single-actor one) -- so this applies
    them all in one pass instead of one player per poll tick. No
    `TURN_TIMER_EXPIRED` event is emitted here: that payload is scoped to
    a single ended turn, and the dedicated `BLACKJACK_BET_DECLINED` /
    `BLACKJACK_HAND_UPDATED` events already tell every client what
    happened.
    """
    round_ = state.blackjack_round
    if round_ is None:
        return

    forced_actions: list[tuple[str, ClientAction]]
    if round_.status == "betting":
        forced_actions = [(pid, BlackjackDeclineAction()) for pid in list(round_.responses_pending)]
    elif round_.bettor_queue:
        forced_actions = [(round_.bettor_queue[0], BlackjackStandAction())]
    else:
        forced_actions = []

    all_events: list[rules_engine.RuleEvent] = []
    for player_id, forced in forced_actions:
        try:
            all_events.extend(rules_engine.validate_and_apply(state, player_id, forced))
        except RuleViolation:
            continue
        # A forced stand can itself close out the round (and re-enter a
        # different phase, e.g. straight back to Phase.MAIN, possibly
        # even Phase.GAME_OVER) -- stop early if so (there's at most one
        # forced action queued for that branch anyway).
        if state.phase != Phase.BLACKJACK_ROUND:
            break

    if not all_events:
        return

    _touch(room.room_code)
    await _dispatch_rule_events(room, all_events)
    await _broadcast_snapshots(room, state)

    if state.phase == Phase.GAME_OVER:
        room.mark_finished()
        _cancel_timer(room.room_code)


async def _force_advance_stalled_turn(room: Room, state: GameState) -> None:
    if state.settings.rush_mode:
        # Rush mode has no "current player" / turn to stall -- every
        # seated player acts independently at all times (see
        # rules_engine._require_actionable_player), so turn_timer_seconds
        # simply doesn't apply. (Forcing an END_TURN here would also just
        # raise RuleViolation now that rush mode rejects it outright --
        # see _validate_end_turn -- but this early return avoids that
        # wasted round-trip and states the intent directly.)
        return
    if state.phase == Phase.BLACKJACK_ROUND:
        # Multi-action (possibly several forced BLACKJACK_DECLINEs at
        # once) and shaped quite differently from the single current/
        # forced-action model below -- handled by its own helper, which
        # does its own dispatch/snapshot broadcast and returns.
        await _force_advance_stalled_blackjack(room, state)
        return
    if state.phase == Phase.SPECIAL_BUILD:
        # During the special build phase, the player who may act is
        # whoever's up in the queue, not `turn_order[current_player_index]`
        # (that index is deliberately left pointing at whoever just
        # finished their normal turn for the whole round -- see
        # `GameState.special_build_queue`'s docstring). A stalled special
        # build turn is always safely auto-playable as "done" (END_TURN
        # here just means "pass on my special build turn").
        if not state.special_build_queue:
            return
        current = state.special_build_queue[0]
        forced: ClientAction = EndTurnAction()
    elif state.phase == Phase.ROLL:
        current = state.turn_order[state.current_player_index]
        forced = RollDiceAction()
    elif state.phase == Phase.MAIN and state.pending is None:
        current = state.turn_order[state.current_player_index]
        forced = EndTurnAction()
    else:
        # A pending discard/robber-placement/steal needs a specific
        # player's own choice; not safely auto-playable here.
        return

    try:
        events = rules_engine.validate_and_apply(state, current, forced)
    except RuleViolation:
        return

    _touch(room.room_code)
    seq = room.next_seq()
    await _broadcast_event(
        room, TurnTimerExpiredEvent(seq=seq, ts=_now(), payload=TurnTimerExpiredPayload(player_id=current))
    )
    await _dispatch_rule_events(room, events)
    await _broadcast_snapshots(room, state)

    if state.phase == Phase.GAME_OVER:
        room.mark_finished()
        _cancel_timer(room.room_code)


async def _turn_timer_loop(room: Room) -> None:
    room_code = room.room_code
    try:
        while True:
            await asyncio.sleep(app_settings.turn_timer_check_interval_seconds)

            if room_manager.get(room_code) is not room:
                return  # room was evicted out from under us

            state = room.game_state
            if state is None or state.phase == Phase.GAME_OVER:
                return

            last_ts = _last_action_ts.get(room_code, _now())
            if turn_timer.should_force_end_turn(state.settings.turn_timer_seconds, last_ts, _now()):
                await _force_advance_stalled_turn(room, state)
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------
# Rush-mode auto-roll timer
# ---------------------------------------------------------------------


def _rush_roll_due(state: GameState, now: float) -> bool:
    """True if a rush-mode `Phase.MAIN` game is due for its next
    system-driven auto-roll right now. Split out from `_rush_roll_loop`
    as a small, synchronous, directly-unit-testable helper -- see
    `app.game.tests.test_rush_mode`'s
    `test_rush_roll_due_uses_state_last_dice_roll_ts_directly` for the
    regression this guards: `state.last_dice_roll_ts` (not some other,
    separately-tracked timestamp) must be the one and only "when did the
    last roll happen" source of truth, since it's already correctly
    primed the instant rush-mode `Phase.MAIN` begins (see
    `RushModeSetup.on_setup_complete`) with no dependency on this loop
    having polled even once yet.
    """
    if not state.settings.rush_mode or state.phase != Phase.MAIN:
        return False
    last_ts = state.last_dice_roll_ts if state.last_dice_roll_ts is not None else now
    return rush_timer.should_auto_roll(state.settings.rush_roll_interval_seconds, last_ts, now)


async def _fire_rush_auto_roll(room: Room, state: GameState) -> None:
    events = rules_engine.apply_rush_auto_roll(state)
    _touch(room.room_code)
    await _dispatch_rule_events(room, events)
    await _broadcast_snapshots(room, state)

    if state.phase == Phase.GAME_OVER:
        room.mark_finished()
        _cancel_timer(room.room_code)
        _cancel_rush_roll(room.room_code)


async def _rush_roll_loop(room: Room) -> None:
    """Per-room background task, started/cancelled alongside
    `_turn_timer_loop` -- see the module docstring's "Rush-mode auto-roll
    timer" section for the full rationale.

    The "last roll" baseline this polls against is `GameState
    .last_dice_roll_ts` itself (never a separately-tracked timestamp here)
    -- that field is already the authoritative source (see its own
    docstring): `RushModeSetup.on_setup_complete` primes it to `time.time()`
    the moment rush-mode `Phase.MAIN` begins, and `apply_rush_auto_roll`
    updates it on every subsequent real auto-roll. An earlier version of
    this loop kept its own module-level `room_code -> last roll ts` dict,
    seeded only from this same loop's "not yet eligible" branch below; that
    had a real bug (caught in live multi-client verification, not by any
    unit test, since those all call `apply_rush_auto_roll` directly and
    never exercise this polling loop): whenever a room's rush-mode setup
    finished -- i.e. `Phase.MAIN` began -- *before* this loop's very first
    `asyncio.sleep(...)` poll had a chance to run once (entirely plausible
    for a real game: setup can easily complete in under
    `turn_timer_check_interval_seconds`, the poll period), the dict entry
    for that room was never seeded. Every later poll then fell through to
    `.get(room_code, _now())`'s fallback -- computing "now" twice, back to
    back, so the elapsed time was always ~0 -- and the auto-roll condition
    could then never become true for the rest of that game. Reading
    `state.last_dice_roll_ts` directly removes the redundant, independently
    -seeded copy that could fall out of sync with it in exactly this way.
    """
    room_code = room.room_code
    try:
        while True:
            await asyncio.sleep(app_settings.turn_timer_check_interval_seconds)

            if room_manager.get(room_code) is not room:
                return  # room was evicted out from under us

            state = room.game_state
            if state is None or state.phase == Phase.GAME_OVER:
                return

            if _rush_roll_due(state, _now()):
                await _fire_rush_auto_roll(room, state)
    except asyncio.CancelledError:
        return


# ---------------------------------------------------------------------
# The endpoint
# ---------------------------------------------------------------------


@router.websocket("/ws/{room_code}")
async def websocket_endpoint(websocket: WebSocket, room_code: str, token: str | None = None) -> None:
    room = room_manager.get(room_code)
    await websocket.accept()

    if room is None:
        await _send_event(
            websocket,
            ErrorEvent(seq=0, ts=_now(), payload=ErrorPayload(code="room_not_found", message=f"No room with code {room_code!r}.")),
        )
        await websocket.close(code=4404)
        return

    conn = _ConnState()

    if token:
        result = connection_manager.reconnect(room, token, websocket)
        if result.ok:
            conn.player_id = result.player_id
            assert conn.player_id is not None
            _touch(room.room_code)
            await _send_catch_up(websocket, room, conn.player_id)
            seq = room.next_seq()
            await _broadcast_event(
                room,
                PlayerReconnectedEvent(seq=seq, ts=_now(), payload=PlayerReconnectedPayload(player_id=conn.player_id)),
            )
        else:
            await _send_private_error(
                websocket, room, "invalid_token", "Reconnect token invalid or expired; send JOIN_ROOM to join fresh."
            )

    try:
        while True:
            try:
                raw = await websocket.receive_text()
            except WebSocketDisconnect:
                break

            try:
                action = _action_adapter.validate_json(raw)
            except ValidationError as exc:
                await _send_private_error(websocket, room, "bad_request", str(exc))
                continue

            should_close = await _handle_action(websocket, room, conn, action)
            if should_close:
                break
    finally:
        if conn.player_id is not None and not conn.left:
            result = connection_manager.disconnect(room, conn.player_id)
            if result.ok:
                seq = room.next_seq()
                await _broadcast_event(
                    room,
                    PlayerDisconnectedEvent(
                        seq=seq, ts=_now(), payload=PlayerDisconnectedPayload(player_id=conn.player_id)
                    ),
                )
