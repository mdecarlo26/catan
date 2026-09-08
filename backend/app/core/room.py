"""`Room`: one lobby/game's lifecycle, seat table, settings, and broadcast.

Per the plan's "Room, Lobby & Reconnect" section. A `Room` owns:

- the seat table (`player_id -> Seat`, i.e. nickname/seat number/host
  flag/connectedness),
- a `SessionStore` (its own `token -> player_id` namespace -- see
  `app.core.session`),
- the live, host-editable `GameSettings` pre-start, locked immutable at
  `START_GAME`,
- an opaque `game_state` slot. Wave 2's backend integration is
  responsible for constructing and storing the real
  `app.game.state.GameState` there once the game starts; this module
  intentionally does not import or depend on `app.game.*` internals so
  the room/lobby/reconnect layer can be built and tested independently
  of the rules engine.

`Room.broadcast()` sends an already-built `app.protocol.events.ServerEvent`
to every seat's currently-bound socket via a `ConnectionManager`. It does
not itself decide *when* to broadcast or assign `seq`/`ts` -- callers
(Wave 2's `app.api.websocket` handlers) build the event (using
`Room.next_seq()` for the monotonic per-room sequence number) and pass it
in.
"""

from __future__ import annotations

import inspect
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from app.core.session import SessionStore
from app.game.settings_schema import GameSettings

if TYPE_CHECKING:
    from app.core.connection_manager import ConnectionManager
    from app.protocol.events import ServerEvent


class RoomError(Exception):
    """Base class for `Room` usage errors (e.g. mutating locked settings)."""


class SettingsLockedError(RoomError):
    """Raised when `update_settings()` is called after the game started."""


class SeatTakenError(RoomError):
    """Raised if `add_player()` is somehow called twice for one player_id
    without an intervening `remove_player()`.
    """


@dataclass
class Seat:
    """One seated player's lobby-facing info. Mirrors the public fields
    of `app.protocol.events.PlayerSummary` (plus nothing hidden -- there
    is no secret info at the lobby level).
    """

    player_id: str
    nickname: str
    seat: int
    is_host: bool
    is_connected: bool = True


def _send(socket: object, data: str) -> None:
    """Best-effort send of `data` (already-serialized JSON text) to
    `socket`, tolerating both the synchronous fake sockets used in this
    package's tests (a plain `send`/`send_text` method) and a real
    `starlette.websockets.WebSocket`, whose `send_text` is a coroutine
    function. There is no real ASGI transport wired up yet (that is Wave
    2's `app.api.websocket` work) -- when there is a running event loop
    we schedule the send as a task; otherwise (e.g. a synchronous test
    calling `Room.broadcast` directly) a plain sync fake just runs
    inline and a bare, unscheduled coroutine is simply dropped, since
    there is nothing safe to block on outside a loop.
    """
    send_fn = getattr(socket, "send_text", None) or getattr(socket, "send", None)
    if send_fn is None:
        return
    result = send_fn(data)
    if inspect.isawaitable(result):
        try:
            loop = None
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            loop.create_task(result)  # fire-and-forget; real usage awaits


class Room:
    def __init__(
        self,
        room_code: str,
        host_player_id: str,
        *,
        settings: GameSettings | None = None,
    ) -> None:
        self.room_code = room_code
        self.host_player_id = host_player_id
        self.settings: GameSettings = settings if settings is not None else GameSettings()
        self.settings_locked: bool = False

        self.seats: dict[str, Seat] = {}
        self.session = SessionStore()

        #: Set by Wave 2's backend integration once `START_GAME` fires.
        #: Deliberately untyped beyond `object | None` -- this module
        #: must not depend on `app.game.state.GameState` or any other
        #: `app.game.*` internals.
        self.game_state: object | None = None

        #: Flipped by Wave 2 when the (opaque) game_state reaches
        #: GAME_OVER, so the TTL sweep (`app.core.room_manager`) can
        #: evict finished-but-lingering rooms without needing to inspect
        #: `game_state` itself.
        self.is_finished: bool = False
        self.finished_at: float | None = None

        self.created_at: float = time.monotonic()
        #: Timestamp (monotonic) since which this room has had zero
        #: connected players, or `None` while at least one seat is
        #: connected. Consulted by the TTL sweep.
        self._empty_since: float | None = None
        self._refresh_empty_since()

        self._next_seq_counter: int = 1

    # -- seating -----------------------------------------------------

    def _next_free_seat_number(self) -> int:
        used = {seat.seat for seat in self.seats.values()}
        n = 0
        while n in used:
            n += 1
        return n

    def add_player(self, player_id: str, nickname: str, *, is_host: bool = False) -> tuple[Seat, str]:
        """Seat a new player, issue their session token. Returns
        `(seat, token)`. Raises `SeatTakenError` if `player_id` is
        already seated (callers should `remove_player` first, e.g. for a
        rejoin-as-new-player edge case -- normal reconnect goes through
        `ConnectionManager.reconnect`, not this method).
        """
        if player_id in self.seats:
            raise SeatTakenError(f"player {player_id!r} is already seated")
        seat = Seat(
            player_id=player_id,
            nickname=nickname,
            seat=self._next_free_seat_number(),
            is_host=is_host,
        )
        self.seats[player_id] = seat
        token = self.session.issue(player_id)
        self._refresh_empty_since()
        return seat, token

    def remove_player(self, player_id: str) -> Seat | None:
        """Free `player_id`'s seat and invalidate their session token.
        Used for both a voluntary `LEAVE_ROOM` and a host `KICK_PLAYER`
        -- both permanently vacate the seat (unlike a plain disconnect,
        which preserves the seat/token for reconnect). Returns the freed
        `Seat`, or `None` if the player wasn't seated.
        """
        seat = self.seats.pop(player_id, None)
        if seat is not None:
            self.session.invalidate_player(player_id)
            self._refresh_empty_since()
        return seat

    #: `KICK_PLAYER` is, at the room-state level, identical to
    #: `LEAVE_ROOM` -- the seat is freed and the token invalidated
    #: either way. The distinction (which event gets broadcast,
    #: host-only authorization) is Wave 2's `app.api.websocket` concern.
    kick_player = remove_player

    def set_player_connected(self, player_id: str, connected: bool) -> None:
        """Flip a seated player's `is_connected` flag, e.g. from
        `ConnectionManager` on connect/disconnect/reconnect. No-op if
        the player isn't seated (already left/kicked).
        """
        seat = self.seats.get(player_id)
        if seat is not None:
            seat.is_connected = connected
        self._refresh_empty_since()

    # -- settings ------------------------------------------------------

    def update_settings(self, new_settings: GameSettings) -> GameSettings:
        """Replace the room's settings wholesale (matches
        `UpdateSettingsPayload`, which always carries the complete
        desired `GameSettings`, not a partial patch). Raises
        `SettingsLockedError` once the game has started.
        """
        if self.settings_locked:
            raise SettingsLockedError("cannot update settings after START_GAME")
        self.settings = new_settings
        return self.settings

    def lock_settings(self) -> None:
        """Freeze settings at `START_GAME` time; further
        `update_settings()` calls raise until the room is torn down.
        """
        self.settings_locked = True

    def mark_finished(self) -> None:
        """Called by Wave 2 when the (opaque) game_state reaches
        GAME_OVER, so the TTL sweep can evict this room after it lingers
        for a post-game summary without needing to inspect `game_state`.
        """
        self.is_finished = True
        self.finished_at = time.monotonic()

    # -- TTL bookkeeping -------------------------------------------------

    def _refresh_empty_since(self) -> None:
        if self.has_connected_players():
            self._empty_since = None
        elif self._empty_since is None:
            self._empty_since = time.monotonic()

    @property
    def empty_since(self) -> float | None:
        """`time.monotonic()` timestamp since which this room has had no
        connected players, or `None` if at least one seat is currently
        connected. A room with zero seats at all counts as empty too.
        """
        return self._empty_since

    def has_connected_players(self) -> bool:
        return any(seat.is_connected for seat in self.seats.values())

    def is_empty(self) -> bool:
        return len(self.seats) == 0

    # -- sequencing / broadcast -----------------------------------------

    def next_seq(self) -> int:
        """Return the next monotonically increasing `seq` for this
        room's event stream, per the WS protocol envelope
        (`{type, payload, seq, ts}`).
        """
        seq = self._next_seq_counter
        self._next_seq_counter += 1
        return seq

    def broadcast(self, event: "ServerEvent", connection_manager: "ConnectionManager") -> None:
        """Send `event` to every seated player's currently-bound socket,
        via `connection_manager`. Players with no active socket (never
        connected yet, or currently disconnected) are silently skipped
        -- they'll catch up via `STATE_SNAPSHOT` on reconnect.
        """
        data = event.model_dump_json()
        for player_id in list(self.seats.keys()):
            socket = connection_manager.get_socket(player_id)
            if socket is None:
                continue
            _send(socket, data)
