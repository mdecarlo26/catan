"""Rush-mode auto-roll timer: pure decision logic only.

Mirrors `app.game.rules.turn_timer`'s split between pure decision logic
(`should_force_end_turn` / `seconds_remaining`) and the asyncio scheduling
wrapper that actually calls it (`app.api.websocket`'s per-room background
task) -- this module is that same split for rush mode's system-driven
auto-roll: `should_auto_roll` is directly unit-testable with fake
timestamps (no real wall-clock sleeps needed), while the scheduling loop
that calls it and fires `app.game.rules_engine.apply_rush_auto_roll` lives
in `app.api.websocket`'s rush-roll background task.
"""

from __future__ import annotations


def should_auto_roll(
    rush_roll_interval_seconds: int | None,
    last_roll_ts: float,
    now_ts: float,
) -> bool:
    """True if at least `rush_roll_interval_seconds` have elapsed since
    `last_roll_ts` (the most recent roll, or the moment rush-mode
    `Phase.MAIN` began if no roll has happened yet -- see
    `GameState.last_dice_roll_ts`'s docstring) and a new auto-roll should
    fire now.

    `rush_roll_interval_seconds` of `None` or `<= 0` disables auto-rolling
    entirely (defensive only -- `GameSettings.rush_roll_interval_seconds`
    has a `ge=5` floor, so this should never actually happen in practice,
    but mirrors `turn_timer.should_force_end_turn`'s same defensive
    contract for its `turn_timer_seconds` parameter).
    """
    if rush_roll_interval_seconds is None or rush_roll_interval_seconds <= 0:
        return False
    return (now_ts - last_roll_ts) >= rush_roll_interval_seconds


def seconds_until_next_roll(
    rush_roll_interval_seconds: int | None,
    last_roll_ts: float,
    now_ts: float,
) -> float | None:
    """Seconds left before `should_auto_roll` would return `True`, for a
    HUD countdown. `None` when auto-rolling is disabled. Never negative --
    clamped to 0 once due.
    """
    if rush_roll_interval_seconds is None or rush_roll_interval_seconds <= 0:
        return None
    remaining = rush_roll_interval_seconds - (now_ts - last_roll_ts)
    return max(0.0, remaining)
