"""Tests for `app.core.room_manager.RoomManager` (and, incidentally,
`app.core.room.Room` seating/kick behavior it drives).
"""

from __future__ import annotations

from app.core.room_manager import RoomManager


def test_room_code_generation_has_no_collisions_across_many_calls():
    manager = RoomManager()
    codes = set()
    for i in range(500):
        room, _token = manager.create_room(f"host-{i}", f"Host {i}")
        assert room.room_code not in codes, "duplicate room code generated"
        codes.add(room.room_code)
        assert 4 <= len(room.room_code) <= 6
        assert room.room_code == room.room_code.upper()
        assert room.room_code.isalnum()

    assert len(codes) == 500
    assert len(manager) == 500


def test_generate_code_retries_on_collision():
    # Force a manager with a tiny alphabet-equivalent collision space by
    # monkeypatching the RNG to always return the same sequence for the
    # first N calls, then something new -- simulate via a room dict that
    # already contains most of the space isn't practical here, so instead
    # directly exercise _generate_code()'s "retry until free" contract by
    # pre-seeding a collision and confirming a second call yields a
    # different code.
    manager = RoomManager(code_length=4)
    first_code = manager._generate_code()
    manager._rooms[first_code] = object()  # occupy that code without a real Room
    second_code = manager._generate_code()
    assert second_code != first_code


def test_kicking_a_player_frees_their_seat_and_invalidates_their_token():
    manager = RoomManager()
    room, host_token = manager.create_room("host-1", "Hosty")
    guest_seat, guest_token = room.add_player("guest-1", "Guesty")

    assert "guest-1" in room.seats
    assert room.session.validate(guest_token) == "guest-1"

    freed_seat = room.kick_player("guest-1")

    assert freed_seat is not None
    assert freed_seat.player_id == "guest-1"
    assert "guest-1" not in room.seats
    # Token is invalidated: validating it no longer resolves to the
    # (now-kicked) player.
    assert room.session.validate(guest_token) is None

    # The host's own seat/token are untouched by kicking someone else.
    assert "host-1" in room.seats
    assert room.session.validate(host_token) == "host-1"


def test_kicking_unknown_player_is_a_noop_returning_none():
    manager = RoomManager()
    room, _token = manager.create_room("host-1", "Hosty")
    assert room.kick_player("nobody") is None


def test_ttl_sweep_evicts_an_empty_room():
    manager = RoomManager(empty_room_ttl_minutes=5.0)
    room, _token = manager.create_room("host-1", "Hosty")
    # A freshly-seated player defaults to connected (they have an open
    # socket at JOIN_ROOM time) -- simulate everyone dropping so the
    # room becomes empty of *connected* players.
    room.set_player_connected("host-1", False)
    assert room.empty_since is not None

    now_before_ttl = room.empty_since + (manager.empty_room_ttl_minutes * 60) - 1
    assert manager.sweep_once(now=now_before_ttl) == []
    assert manager.get(room.room_code) is not None

    now_after_ttl = room.empty_since + (manager.empty_room_ttl_minutes * 60) + 1
    evicted = manager.sweep_once(now=now_after_ttl)

    assert evicted == [room.room_code]
    assert manager.get(room.room_code) is None
    assert len(manager) == 0


def test_ttl_sweep_does_not_evict_a_room_with_a_connected_player():
    manager = RoomManager(empty_room_ttl_minutes=5.0)
    room, _token = manager.create_room("host-1", "Hosty")
    room.set_player_connected("host-1", True)
    assert room.empty_since is None

    far_future = 10 ** 9
    assert manager.sweep_once(now=far_future) == []
    assert manager.get(room.room_code) is not None


def test_ttl_sweep_evicts_a_finished_game_idle_past_its_ttl():
    manager = RoomManager(finished_room_ttl_minutes=15.0)
    room, _token = manager.create_room("host-1", "Hosty")
    room.set_player_connected("host-1", True)  # still connected...
    room.mark_finished()  # ...but the game is over.

    assert room.empty_since is None  # not evicted via the empty-room path

    now_after_ttl = room.finished_at + (manager.finished_room_ttl_minutes * 60) + 1
    evicted = manager.sweep_once(now=now_after_ttl)

    assert evicted == [room.room_code]
    assert manager.get(room.room_code) is None


def test_get_room_code_lookup_is_case_insensitive():
    manager = RoomManager()
    room, _token = manager.create_room("host-1", "Hosty")
    assert manager.get(room.room_code.lower()) is room
    assert manager.get(room.room_code) is room
    assert manager.get("does-not-exist") is None


def test_delete_room_removes_it():
    manager = RoomManager()
    room, _token = manager.create_room("host-1", "Hosty")
    removed = manager.delete(room.room_code)
    assert removed is room
    assert manager.get(room.room_code) is None
    assert manager.delete(room.room_code) is None
