"""Environment-driven application settings.

Reads the ``CATAN_*`` variables documented in ``.env.example`` (consumed
by Docker Compose / a real deployment's environment, or a local ``.env``
loaded by whatever process launches uvicorn) plus a few additional knobs
this integration pass needs (finished-room TTL, sweep interval, WS ping
interval, turn-timer poll interval) that aren't in ``.env.example`` but
follow the same ``CATAN_`` naming convention and all have sane defaults,
so nothing needs to be set for local/dev use.

Deliberately a plain stdlib ``os.environ`` read rather than
``pydantic-settings`` (not a dependency of this project -- see
``pyproject.toml``) to avoid adding a new dependency for what is a very
small settings surface.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

#: Origins the frontend dev server / prod deployment is served from, per
#: .env.example's own default.
_DEFAULT_ALLOWED_ORIGINS = "http://localhost,http://localhost:5173"


def _split_origins(raw: str) -> list[str]:
    return [origin.strip() for origin in raw.split(",") if origin.strip()]


@dataclass(frozen=True)
class Settings:
    #: Host/port uvicorn binds to. Only consumed by a `__main__`-style
    #: local run (`python -m app.main`); under `uvicorn app.main:app` or
    #: Docker's CMD these are typically passed on the command line
    #: instead, but are exposed here too for a single source of truth.
    host: str = "0.0.0.0"
    port: int = 8000

    #: Allowed CORS origins for the REST API and the WS handshake's
    #: Origin check (FastAPI's CORSMiddleware also governs the WS
    #: upgrade request's preflight-equivalent checks for browsers that
    #: enforce it).
    allowed_origins: list[str] = field(
        default_factory=lambda: _split_origins(_DEFAULT_ALLOWED_ORIGINS)
    )

    #: Minutes an empty/abandoned room (zero connected players) is kept
    #: before the TTL sweep evicts it. See `app.core.room_manager`.
    room_ttl_minutes: float = 60.0
    #: Minutes a finished (GAME_OVER) room lingers, for the post-game
    #: summary, before the TTL sweep evicts it.
    finished_room_ttl_minutes: float = 30.0
    #: How often the TTL sweep runs.
    room_sweep_interval_seconds: float = 60.0

    #: How often a per-room stalled-turn check runs (see
    #: `app.api.websocket`'s turn-timer background task, which wraps
    #: `app.game.rules.turn_timer.should_force_end_turn`).
    turn_timer_check_interval_seconds: float = 5.0

    #: Reserved for a future WS keepalive ping loop; not yet wired to
    #: anything (Starlette/uvicorn already send protocol-level pings),
    #: kept here so the env var name is stable once that lands.
    ws_ping_interval_seconds: float = 20.0


def load_settings(env: "os._Environ[str] | dict[str, str] | None" = None) -> Settings:
    """Build a `Settings` from `env` (defaults to the real process
    environment). Exposed as a function -- rather than only a
    module-level singleton -- so tests can construct a `Settings` from an
    arbitrary mapping without mutating `os.environ`.
    """
    src = env if env is not None else os.environ
    return Settings(
        host=src.get("CATAN_HOST", "0.0.0.0"),
        port=int(src.get("CATAN_PORT", "8000")),
        allowed_origins=_split_origins(
            src.get("CATAN_ALLOWED_ORIGINS", _DEFAULT_ALLOWED_ORIGINS)
        ),
        room_ttl_minutes=float(src.get("CATAN_ROOM_TTL_MINUTES", "60")),
        finished_room_ttl_minutes=float(
            src.get("CATAN_FINISHED_ROOM_TTL_MINUTES", "30")
        ),
        room_sweep_interval_seconds=float(
            src.get("CATAN_ROOM_SWEEP_INTERVAL_SECONDS", "60")
        ),
        turn_timer_check_interval_seconds=float(
            src.get("CATAN_TURN_TIMER_CHECK_INTERVAL_SECONDS", "5")
        ),
        ws_ping_interval_seconds=float(
            src.get("CATAN_WS_PING_INTERVAL_SECONDS", "20")
        ),
    )


#: Process-wide settings singleton, loaded once at import time from the
#: real environment. `app.core.registry`, `app.main`, and
#: `app.api.websocket` all import this.
settings = load_settings()
