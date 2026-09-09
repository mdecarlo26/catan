"""`POST /api/rooms` -- create a room and seat the host.

Per `ARCHITECTURE.md`'s "Repo Structure"
(``rooms.py  # POST /api/rooms -> create room, returns code + host token``)
and "Room, Lobby & Reconnect" section.

Response shape matches `frontend/src/api/roomsApi.ts`'s assumption
(documented in that file's own module docstring) that the response is
directly usable as a `StoredSession`
(`frontend/src/api/session.ts`'s ``{room_code, player_id, token}``), so
`CreateRoom.tsx` can persist it via `saveSession()` immediately without
any reshaping. Request shape matches `roomsApi.ts`'s
`CreateRoomRequest`: ``{nickname, settings?}``, where `settings` (if
present) is a *partial* `GameSettings` object -- since every
`GameSettings` field already has a default, pydantic parses a partial
dict into a fully-populated `GameSettings` for free, so no separate
"partial settings" schema is needed here.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.core.registry import room_manager
from app.game.settings_schema import GameSettings

router = APIRouter(tags=["rooms"])


class CreateRoomRequest(BaseModel):
    nickname: str = Field(min_length=1, max_length=32)
    #: Optional partial `GameSettings`; omitted/absent fields fall back
    #: to `GameSettings`'s own defaults. `None` (the default) means "use
    #: an all-default `GameSettings`".
    settings: GameSettings | None = None


class CreateRoomResponse(BaseModel):
    room_code: str
    player_id: str
    token: str


@router.post("/api/rooms", response_model=CreateRoomResponse, status_code=201)
def create_room(payload: CreateRoomRequest) -> CreateRoomResponse:
    nickname = payload.nickname.strip()
    if not nickname:
        raise HTTPException(status_code=422, detail="nickname is required")

    host_player_id = str(uuid.uuid4())
    room, token = room_manager.create_room(
        host_player_id, nickname, settings=payload.settings
    )
    return CreateRoomResponse(room_code=room.room_code, player_id=host_player_id, token=token)
