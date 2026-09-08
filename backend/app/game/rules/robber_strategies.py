"""Robber steal behavior.

Only `normal_steal` is wired into `rules_engine` today. `friendly_robber`
is one of the "additional toggles" floated in the plan discussion (a
robber that blocks a hex's production without ever stealing a card) but
it never made it into the frozen `GameSettings` schema
(`app.game.settings_schema`) -- there is no `friendly_robber` field to
switch on. It's implemented here as a documented, fully-working stub so
wiring it up later (once that settings field exists) is a one-line change
in `rules_engine._apply_move_robber`, not a redesign.
"""

from __future__ import annotations

import random

from app.game.board import PlayerId
from app.game.players import PlayerState, ResourceType


def normal_steal(
    players: dict[PlayerId, PlayerState],
    actor_id: PlayerId,
    victim_id: PlayerId,
    rng: random.Random | None = None,
) -> ResourceType | None:
    """Steal one uniformly-random resource card from `victim_id`'s hand
    and transfer it to `actor_id`.

    Returns the stolen resource type, or `None` if the victim has no
    cards to steal (a no-op).
    """
    victim = players[victim_id]
    pool = [resource for resource, count in victim.hand.items() for _ in range(count)]
    if not pool:
        return None

    chooser = rng or random
    stolen = chooser.choice(pool)

    victim.hand[stolen] -= 1
    actor = players[actor_id]
    actor.hand[stolen] = actor.hand.get(stolen, 0) + 1
    return stolen


def friendly_robber_steal_candidates(
    players: dict[PlayerId, PlayerState],
    vertex_owners: list[PlayerId],
) -> list[PlayerId]:
    """Friendly-robber variant of the candidate-gathering step normally
    done inline in `rules_engine._apply_move_robber`: moving the robber
    still blocks the hex's production (that part is unconditional board
    mutation, not this function's concern), but a friendly robber never
    yields a steal. Always returns an empty candidate list, so swapping
    this in for the normal-steal candidate list produces a no-op steal
    phase for free -- no separate "friendly" branch needed anywhere else.

    STUB: not selected anywhere in `rules_engine` yet, since there is no
    `GameSettings` field to choose it. `vertex_owners` (the player_ids
    with a building on the robber's new hex, as `rules_engine` already
    computes for `normal_steal`'s candidate list) is accepted so the
    eventual call site can swap strategies without changing its call
    shape, but it's intentionally unused here.
    """
    del players, vertex_owners  # unused: friendly robber never steals
    return []
