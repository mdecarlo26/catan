"""FastAPI application entrypoint.

Mounts the REST room-creation endpoint (`app.api.rooms`) and the game
WebSocket endpoint (`app.api.websocket`), configures CORS from
`app.config.settings.allowed_origins`, exposes a `/health` check (per
the plan's Phase 4 deploy verification), and runs
`app.core.registry.room_manager`'s TTL sweep as a background task for
the lifetime of the app.

Run locally with:

    uvicorn app.main:app --host $CATAN_HOST --port $CATAN_PORT --reload
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import AsyncIterator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.rooms import router as rooms_router
from app.api.websocket import router as websocket_router
from app.config import settings
from app.core.registry import room_manager


@contextlib.asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    sweep_task = asyncio.create_task(
        room_manager.sweep_forever(interval_seconds=settings.room_sweep_interval_seconds)
    )
    try:
        yield
    finally:
        sweep_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await sweep_task


app = FastAPI(title="Catan Backend", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.allowed_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(rooms_router)
app.include_router(websocket_router)


@app.get("/health")
def health() -> dict[str, object]:
    return {"status": "ok", "rooms": len(room_manager)}
