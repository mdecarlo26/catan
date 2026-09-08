"""`ConnectionManager`: player_id <-> active WebSocket binding.

Per the plan's "Room, Lobby & Reconnect" section. Exactly one active
socket is tracked per `player_id` at a time -- game state is keyed by
`player_id`, never by socket, so nothing in this module (or `Room`) ever
needs to know about a *previous* connection once a new one replaces it.

This module deliberately knows nothing about `app.game.*` internals. It
depends only on `app.core.room.Room` (for seat lookup by token, via
`Room.session`, and to flip `Seat.is_connected`) -- not on
`rules_engine` or `GameState`. The actual WebSocket transport
(`app.api.websocket`) is Wave 2 work; this module is written against a
minimal duck-typed "socket" (anything with a `close()` and, for
`Room.broadcast`, a `send`/`send_text` method), so it can be fully
exercised in tests with a lightweight fake socket instead of a real
ASGI connection.
"""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from app.core.room import Room


def _close_socket(socket: Any) -> None:
    """Best-effort close of a stale socket. Tolerates a plain synchronous
    `close()` (the fake sockets used in this package's tests) as well as
    a real ASGI socket whose `close()` is a coroutine function -- in the
    latter case, if there's a running event loop we schedule the close
    as a task (fire-and-forget); a bare, unscheduled coroutine with no
    running loop is simply dropped, since there's nothing safe to block
    on from here. Any exception raised by a synchronous `close()` is
    swallowed -- a socket that's already gone is exactly the case we're
    handling.
    """
    close_fn = getattr(socket, "close", None)
    if close_fn is None:
        return
    try:
        result = close_fn()
    except Exception:
        return
    if inspect.isawaitable(result):
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(result)


@dataclass
class ReconnectResult:
    """Outcome of `ConnectionManager.reconnect()`. On success, carries
    everything Wave 2's `app.api.websocket` handler needs to build and
    broadcast `PLAYER_RECONNECTED` without looking anything else up.
    """

    ok: bool
    player_id: str | None = None
    room_code: str | None = None
    seat_number: int | None = None
    nickname: str | None = None
    is_host: bool | None = None
    replaced_stale_socket: bool = False
    reason: str | None = None  # populated when ok is False


@dataclass
class DisconnectResult:
    """Outcome of `ConnectionManager.disconnect()`."""

    ok: bool
    player_id: str | None = None
    room_code: str | None = None
    reason: str | None = None


class ConnectionManager:
    """Tracks, per `player_id`: the currently-bound socket (if any) and
    whether that player is currently connected. `player_id`s are global
    (uuid4), so a single `ConnectionManager` instance can serve every
    room in the process -- there's no need for one per `Room`.
    """

    def __init__(self) -> None:
        self._sockets: dict[str, Any] = {}
        self._connected: dict[str, bool] = {}

    # -- low-level binding -------------------------------------------------

    def get_socket(self, player_id: str) -> Any | None:
        return self._sockets.get(player_id)

    def is_connected(self, player_id: str) -> bool:
        return self._connected.get(player_id, False)

    def bind(self, player_id: str, socket: Any) -> bool:
        """Bind `socket` as the active connection for `player_id`. If a
        (different) socket is already bound for this `player_id`, it is
        closed first (per the plan: "close the stale prior socket
        first, then bind the new one"). Returns `True` if a stale socket
        was found and closed.
        """
        stale = self._sockets.get(player_id)
        replaced = False
        if stale is not None and stale is not socket:
            _close_socket(stale)
            replaced = True
        self._sockets[player_id] = socket
        self._connected[player_id] = True
        return replaced

    def forget(self, player_id: str) -> None:
        """Fully drop all tracking for `player_id` (no `close()` call --
        callers that need the socket closed should do so, or use
        `disconnect`/`bind`'s stale-close behavior). Used when a player
        is permanently removed from a room (kick/leave), so a later
        reconnect attempt with their now-invalidated token has nothing
        to rebind to.
        """
        self._sockets.pop(player_id, None)
        self._connected.pop(player_id, None)

    # -- room-aware reconnect / disconnect ----------------------------------

    def reconnect(self, room: "Room", token: str, socket: Any) -> ReconnectResult:
        """Validate `token` against `room`'s session store and, on
        success, rebind `socket` as the player's active connection,
        flip their seat's `is_connected` to `True`, and return enough
        info to broadcast `PLAYER_RECONNECTED`. Returns
        `ReconnectResult(ok=False, ...)` for an invalid/unknown token or
        a token whose player is no longer seated (e.g. was kicked).
        """
        player_id = room.session.validate(token)
        if player_id is None:
            return ReconnectResult(ok=False, reason="invalid_token")

        seat = room.seats.get(player_id)
        if seat is None:
            # Token was valid but the seat is gone (shouldn't normally
            # happen -- remove_player() also invalidates the token --
            # but guard against it rather than rebinding to nothing).
            return ReconnectResult(ok=False, reason="not_seated")

        replaced = self.bind(player_id, socket)
        room.set_player_connected(player_id, True)

        return ReconnectResult(
            ok=True,
            player_id=player_id,
            room_code=room.room_code,
            seat_number=seat.seat,
            nickname=seat.nickname,
            is_host=seat.is_host,
            replaced_stale_socket=replaced,
        )

    def connect(self, room: "Room", player_id: str, socket: Any) -> None:
        """Bind a brand-new (non-reconnect) connection for an
        already-seated `player_id`, e.g. right after `Room.add_player`
        on the initial `JOIN_ROOM`. Flips the seat connected too.
        """
        self.bind(player_id, socket)
        room.set_player_connected(player_id, True)

    def disconnect(self, room: "Room", player_id: str) -> DisconnectResult:
        """Graceful disconnect: flip `is_connected` False and drop the
        (now-closed) socket binding, but do NOT free the seat or
        invalidate the session token -- that's what allows the player to
        reconnect later. Returns enough info to broadcast
        `PLAYER_DISCONNECTED`.
        """
        if player_id not in room.seats:
            return DisconnectResult(ok=False, reason="not_seated")

        self._sockets.pop(player_id, None)
        self._connected[player_id] = False
        room.set_player_connected(player_id, False)

        return DisconnectResult(ok=True, player_id=player_id, room_code=room.room_code)
