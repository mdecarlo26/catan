"""Process-wide singletons shared by the REST (`app.api.rooms`) and
WebSocket (`app.api.websocket`) API layers, plus `app.main`'s startup
TTL sweep.

A single `RoomManager`/`ConnectionManager` pair must be shared across
every request handler and the WS endpoint -- each module importing its
own instance would silently fragment room/connection state. Constructed
once here, at import time, from `app.config.settings`.
"""

from __future__ import annotations

from app.config import settings
from app.core.connection_manager import ConnectionManager
from app.core.room_manager import RoomManager

room_manager = RoomManager(
    empty_room_ttl_minutes=settings.room_ttl_minutes,
    finished_room_ttl_minutes=settings.finished_room_ttl_minutes,
)

connection_manager = ConnectionManager()
