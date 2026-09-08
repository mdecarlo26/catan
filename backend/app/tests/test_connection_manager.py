"""Tests for `app.core.connection_manager.ConnectionManager`.

Uses a lightweight fake socket (records sent messages / close calls)
rather than a real WebSocket transport -- there's no real ASGI route
wired up yet (that's Wave 2's `app.api.websocket` work); this module is
written against a minimal duck-typed socket interface on purpose.
"""

from __future__ import annotations

from app.core.connection_manager import ConnectionManager
from app.core.room_manager import RoomManager


class FakeSocket:
    """A minimal stand-in for a WebSocket connection: records what was
    sent to it and whether/how many times it was closed.
    """

    def __init__(self, name: str = "socket") -> None:
        self.name = name
        self.sent: list[str] = []
        self.closed = False
        self.close_calls = 0

    def send(self, data: str) -> None:
        self.sent.append(data)

    def close(self) -> None:
        self.closed = True
        self.close_calls += 1

    def __repr__(self) -> str:  # pragma: no cover - debugging aid only
        return f"FakeSocket({self.name!r})"


def _make_room_with_guest():
    manager = RoomManager()
    room, host_token = manager.create_room("host-1", "Hosty")
    guest_seat, guest_token = room.add_player("guest-1", "Guesty")
    return manager, room, guest_seat, guest_token


def test_reconnect_with_valid_token_rebinds_same_player_and_seat():
    _manager, room, guest_seat, guest_token = _make_room_with_guest()
    cm = ConnectionManager()

    first_socket = FakeSocket("first")
    cm.connect(room, "guest-1", first_socket)
    assert cm.get_socket("guest-1") is first_socket
    assert cm.is_connected("guest-1") is True

    # Simulate a drop: the socket goes away, is_connected flips false,
    # but the seat/token remain (that's the whole point of reconnect).
    cm.disconnect(room, "guest-1")
    assert cm.is_connected("guest-1") is False
    assert room.seats["guest-1"].is_connected is False

    new_socket = FakeSocket("second")
    result = cm.reconnect(room, guest_token, new_socket)

    assert result.ok is True
    assert result.player_id == "guest-1"
    assert result.room_code == room.room_code
    assert result.seat_number == guest_seat.seat
    assert result.nickname == "Guesty"
    assert result.is_host is False

    assert cm.get_socket("guest-1") is new_socket
    assert cm.is_connected("guest-1") is True
    assert room.seats["guest-1"].is_connected is True


def test_reconnect_with_invalid_or_unknown_token_is_rejected():
    _manager, room, _guest_seat, _guest_token = _make_room_with_guest()
    cm = ConnectionManager()

    result = cm.reconnect(room, "this-token-does-not-exist", FakeSocket())

    assert result.ok is False
    assert result.reason == "invalid_token"
    assert result.player_id is None
    # Nothing got bound as a side effect of the failed attempt.
    assert cm.get_socket("guest-1") is None
    assert cm.is_connected("guest-1") is False


def test_reconnect_after_kick_is_rejected_even_with_the_old_token():
    _manager, room, _guest_seat, guest_token = _make_room_with_guest()
    cm = ConnectionManager()

    room.kick_player("guest-1")

    result = cm.reconnect(room, guest_token, FakeSocket())

    assert result.ok is False
    assert result.reason == "invalid_token"


def test_second_connection_for_already_connected_player_closes_stale_socket():
    _manager, room, _guest_seat, _guest_token = _make_room_with_guest()
    cm = ConnectionManager()

    stale_socket = FakeSocket("stale")
    cm.connect(room, "guest-1", stale_socket)
    assert stale_socket.closed is False

    fresh_socket = FakeSocket("fresh")
    cm.connect(room, "guest-1", fresh_socket)

    assert stale_socket.closed is True
    assert stale_socket.close_calls == 1
    assert fresh_socket.closed is False
    assert cm.get_socket("guest-1") is fresh_socket
    assert cm.is_connected("guest-1") is True


def test_binding_the_same_socket_object_again_does_not_close_it():
    _manager, room, _guest_seat, _guest_token = _make_room_with_guest()
    cm = ConnectionManager()

    socket = FakeSocket()
    cm.connect(room, "guest-1", socket)
    replaced = cm.bind("guest-1", socket)

    assert replaced is False
    assert socket.closed is False
    assert cm.get_socket("guest-1") is socket


def test_disconnect_preserves_seat_and_token_for_later_reconnect():
    manager, room, _guest_seat, guest_token = _make_room_with_guest()
    cm = ConnectionManager()
    cm.connect(room, "guest-1", FakeSocket())

    result = cm.disconnect(room, "guest-1")

    assert result.ok is True
    assert result.player_id == "guest-1"
    # Seat and token both survive a graceful disconnect.
    assert "guest-1" in room.seats
    assert room.session.validate(guest_token) == "guest-1"
    assert cm.get_socket("guest-1") is None
    assert cm.is_connected("guest-1") is False


def test_disconnect_of_unseated_player_reports_failure():
    _manager, room, _guest_seat, _guest_token = _make_room_with_guest()
    cm = ConnectionManager()

    result = cm.disconnect(room, "nobody")

    assert result.ok is False
    assert result.reason == "not_seated"


def test_room_broadcast_sends_to_all_connected_sockets_only():
    _manager, room, _guest_seat, _guest_token = _make_room_with_guest()
    cm = ConnectionManager()

    host_socket = FakeSocket("host")
    cm.connect(room, "host-1", host_socket)
    # guest-1 never connects a socket -- broadcast should just skip them.

    from app.protocol.events import (
        EventType,
        PlayerJoinedEvent,
        PlayerJoinedPayload,
        PlayerSummary,
    )

    event = PlayerJoinedEvent(
        seq=room.next_seq(),
        ts=0.0,
        payload=PlayerJoinedPayload(
            player=PlayerSummary(
                player_id="guest-1",
                nickname="Guesty",
                seat=1,
                is_connected=False,
                is_host=False,
            )
        ),
    )
    assert event.type == EventType.PLAYER_JOINED

    room.broadcast(event, cm)

    assert len(host_socket.sent) == 1
    assert "guest-1" in host_socket.sent[0]
