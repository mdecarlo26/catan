"""Stalled-turn auto-skip: pure decision logic only.

Per the task's scope, actual scheduling/wiring (a periodic task that
calls this and, on a `True` result, synthesizes an `END_TURN` through
`rules_engine`, broadcasting `TURN_TIMER_EXPIRED`) is Wave 2 backend
integration work -- out of scope here.

Note: `app.game.settings_schema.GameSettings` does not (yet) have a
`turn_timer_seconds` field, even though `ARCHITECTURE.md`'s "Room, Lobby
& Reconnect" section describes the behavior ("a configurable
`turn_timer_seconds` setting auto-ends a disconnected... player's turn").
The frozen settings schema this module was built against simply doesn't
expose it yet. So these functions take the timeout as an explicit
parameter rather than reading `state.settings.turn_timer_seconds`; once
that field is added to `GameSettings`, a Wave 2 caller can pass
`state.settings.turn_timer_seconds` straight through without this module
changing at all.
"""

from __future__ import annotations


def should_force_end_turn(
    turn_timer_seconds: int | None,
    last_action_ts: float,
    now_ts: float,
) -> bool:
    """True if the current turn has been idle for at least
    `turn_timer_seconds` and should be force-ended.

    `turn_timer_seconds` of `None` or `<= 0` means the timer is disabled
    (never force-ends a turn). Idle time is `now_ts - last_action_ts`,
    both unix timestamps in seconds (matching
    `app.game.state.ActionLogEntry.ts`'s convention).
    """
    if turn_timer_seconds is None or turn_timer_seconds <= 0:
        return False
    return (now_ts - last_action_ts) >= turn_timer_seconds


def seconds_remaining(
    turn_timer_seconds: int | None,
    last_action_ts: float,
    now_ts: float,
) -> float | None:
    """Seconds left before `should_force_end_turn` would return `True`,
    for a HUD countdown. `None` when the timer is disabled. Never
    negative -- clamped to 0 once expired.
    """
    if turn_timer_seconds is None or turn_timer_seconds <= 0:
        return None
    remaining = turn_timer_seconds - (now_ts - last_action_ts)
    return max(0.0, remaining)
