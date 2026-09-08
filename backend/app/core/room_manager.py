"""`RoomManager`: room_code -> Room registry, code generation, TTL sweep.

Per the plan's "Room, Lobby & Reconnect" section: rooms are looked up by
a short, human-shareable code (so a host can read it aloud / paste it
into a link). Rooms are evicted by a periodic TTL sweep once they've had
zero connected players for longer than `empty_room_ttl_minutes`, or once
a finished game has lingered (for the post-game summary) longer than
`finished_room_ttl_minutes` -- both configurable per the plan ("evicts
rooms with zero connected players for > N minutes ... make N
configurable").
"""

from __future__ import annotations

import asyncio
import logging
import random
import string
import time

from app.core.room import Room
from app.game.settings_schema import GameSettings

logger = logging.getLogger(__name__)

#: Uppercase letters + digits -- "4-6 uppercase alphanumeric,
#: human-shareable" per the task spec. Kept as the full alphanumeric set
#: (no ambiguous-character exclusion) since that's an easy follow-up
#: tweak and isn't specified by the plan.
CODE_ALPHABET = string.ascii_uppercase + string.digits

DEFAULT_CODE_LENGTH = 4
DEFAULT_EMPTY_ROOM_TTL_MINUTES = 10.0
DEFAULT_FINISHED_ROOM_TTL_MINUTES = 30.0
DEFAULT_SWEEP_INTERVAL_SECONDS = 60.0


class RoomManager:
    def __init__(
        self,
        *,
        code_length: int = DEFAULT_CODE_LENGTH,
        empty_room_ttl_minutes: float = DEFAULT_EMPTY_ROOM_TTL_MINUTES,
        finished_room_ttl_minutes: float = DEFAULT_FINISHED_ROOM_TTL_MINUTES,
        rng: random.Random | None = None,
    ) -> None:
        if not (4 <= code_length <= 6):
            raise ValueError("code_length must be between 4 and 6")
        self._code_length = code_length
        self.empty_room_ttl_minutes = empty_room_ttl_minutes
        self.finished_room_ttl_minutes = finished_room_ttl_minutes
        self._rng = rng or random.Random()
        self._rooms: dict[str, Room] = {}

    # -- code generation -------------------------------------------------

    def _generate_code(self) -> str:
        """Generate a room code with no collision against currently-live
        rooms. Astronomically unlikely to loop more than once or twice
        at this alphabet size (36^4 = 1.68M+ combinations), but retries
        explicitly rather than assuming.
        """
        while True:
            code = "".join(self._rng.choices(CODE_ALPHABET, k=self._code_length))
            if code not in self._rooms:
                return code

    # -- CRUD --------------------------------------------------------------

    def create_room(
        self,
        host_player_id: str,
        host_nickname: str,
        *,
        settings: GameSettings | None = None,
    ) -> tuple[Room, str]:
        """Create a new room, seat the host (seat 0, `is_host=True`), and
        return `(room, host_session_token)`.
        """
        code = self._generate_code()
        room = Room(room_code=code, host_player_id=host_player_id, settings=settings)
        self._rooms[code] = room
        _, token = room.add_player(host_player_id, host_nickname, is_host=True)
        return room, token

    def get(self, room_code: str) -> Room | None:
        return self._rooms.get(room_code.upper())

    def delete(self, room_code: str) -> Room | None:
        return self._rooms.pop(room_code.upper(), None)

    def all_rooms(self) -> list[Room]:
        return list(self._rooms.values())

    def __len__(self) -> int:
        return len(self._rooms)

    # -- TTL sweep -----------------------------------------------------

    def _should_evict(self, room: Room, now: float) -> bool:
        empty_since = room.empty_since
        if empty_since is not None:
            idle_minutes = (now - empty_since) / 60.0
            if idle_minutes > self.empty_room_ttl_minutes:
                return True

        if room.is_finished and room.finished_at is not None:
            idle_minutes = (now - room.finished_at) / 60.0
            if idle_minutes > self.finished_room_ttl_minutes:
                return True

        return False

    def sweep_once(self, *, now: float | None = None) -> list[str]:
        """Run one TTL sweep pass synchronously and return the list of
        evicted room codes. Exposed separately from `sweep_forever` so
        tests (and any manual/administrative trigger) don't need to run
        an actual event loop.
        """
        now = now if now is not None else time.monotonic()
        evicted: list[str] = []
        for code, room in list(self._rooms.items()):
            if self._should_evict(room, now):
                del self._rooms[code]
                evicted.append(code)
        if evicted:
            logger.info("TTL sweep evicted %d room(s): %s", len(evicted), evicted)
        return evicted

    async def sweep_forever(
        self, *, interval_seconds: float = DEFAULT_SWEEP_INTERVAL_SECONDS
    ) -> None:
        """Periodic TTL sweep coroutine -- run as a background task by
        Wave 2's `app.main` startup. Loops until cancelled.
        """
        while True:
            await asyncio.sleep(interval_seconds)
            try:
                self.sweep_once()
            except Exception:  # pragma: no cover - defensive; never kill the loop
                logger.exception("room TTL sweep pass failed")
