"""Tests for the official 5-6p expansion's Special Build Phase (SBP), per
`app.game.state.Phase.SPECIAL_BUILD` / `GameState.special_build_queue` and
the END_TURN handling in `app.game.rules_engine._apply_end_turn`.

Covers: the effective-setting gate (`None` -> `player_count >= 5`, plus
explicit True/False overrides at any player count), the SBP queue walking
every other player exactly once in turn order and then resuming the real
next-player full turn, the restricted action set (no ROLL_DICE, no
PLAY_DEV_CARD) during a player's own SBP turn, and rejection of a player
who isn't currently up in the queue.

`app.game.board`'s geometry functions raise `NotImplementedError` in this
worktree (see `test_rules_engine.py`'s module docstring for why), so the
one test here that actually builds something during SBP monkeypatches
them with the same small linear "chain" board helper used there.
"""

from __future__ import annotations

import pytest

from app.game import board as board_mod
from app.game import rules_engine
from app.game.actions import (
    BuildRoadAction,
    BuildRoadPayload,
    EndTurnAction,
    PlayDevCardAction,
    PlayDevCardPayload,
    RollDiceAction,
)
from app.game.board import (
    Board,
    EdgeId,
    HexCoord,
    HexTile,
    Terrain,
    VertexId,
)
from app.game.players import DevCardType, PlayerState, ResourceType
from app.game.rules_engine import RuleViolation
from app.game.settings_schema import GameSettings
from app.game.state import Bank, GameState, Phase

# ---------------------------------------------------------------------
# Fixture-building helpers (self-contained -- no conftest.py, matching
# test_rules_engine.py's / test_scoring.py's own local copies)
# ---------------------------------------------------------------------


def _dummy_board() -> Board:
    """A minimal, geometry-free board: enough to build a `GameState` for
    tests that only exercise END_TURN / ROLL_DICE / PLAY_DEV_CARD phase
    transitions and never call board-geometry functions.
    """
    hex_coord = HexCoord(0, 0)
    return Board(
        hexes={hex_coord: HexTile(coord=hex_coord, terrain=Terrain.DESERT, number_token=None)},
        ports=[],
        buildings={},
        roads={},
        robber_hex=hex_coord,
    )


def _make_state(num_players: int, **settings_kwargs) -> GameState:
    player_ids = [f"p{i}" for i in range(num_players)]
    players = {
        pid: PlayerState(player_id=pid, nickname=pid, seat=i) for i, pid in enumerate(player_ids)
    }
    settings_kwargs.setdefault("player_count", num_players)
    return GameState(
        room_code="TEST",
        settings=GameSettings(**settings_kwargs),
        phase=Phase.MAIN,
        turn_order=player_ids,
        current_player_index=0,
        board=_dummy_board(),
        bank=Bank(resources={r: 19 for r in ResourceType}, dev_card_pile=[]),
        players=players,
    )


def _make_chain(start_q: int, terrain_tokens: list[tuple[Terrain, int | None]]) -> dict:
    """A linear chain of hexes (axial q = start_q.., r = 0), one per
    `terrain_tokens` entry, with one vertex per hex-to-hex junction (plus
    the two end caps) and one edge per consecutive vertex pair. See
    `test_rules_engine.py`'s identical helper for the full derivation
    rationale.
    """
    length = len(terrain_tokens)
    hex_coords = [HexCoord(start_q + i, 0) for i in range(length)]
    dummy_cap = HexCoord(start_q + length, 0)
    edge_hexes = hex_coords + [dummy_cap]

    vertices: list[VertexId] = [(hex_coords[0],)]
    for i in range(length - 1):
        vertices.append((hex_coords[i], hex_coords[i + 1]))
    vertices.append((hex_coords[-1],))

    edges: list[EdgeId] = [(edge_hexes[i], edge_hexes[i + 1]) for i in range(length)]

    vertex_neighbors: dict[VertexId, list[VertexId]] = {v: [] for v in vertices}
    for i in range(length):
        vertex_neighbors[vertices[i]].append(vertices[i + 1])
        vertex_neighbors[vertices[i + 1]].append(vertices[i])

    edge_endpoints: dict[EdgeId, tuple[VertexId, VertexId]] = {
        edges[i]: (vertices[i], vertices[i + 1]) for i in range(length)
    }

    adjacent_edges: dict[HexCoord, list[EdgeId]] = {h: [] for h in edge_hexes}
    for i, e in enumerate(edges):
        adjacent_edges[edge_hexes[i]].append(e)
        adjacent_edges[edge_hexes[i + 1]].append(e)

    adjacent_vertices: dict[HexCoord, list[VertexId]] = {h: [] for h in hex_coords}
    for v in vertices:
        for h in v:
            adjacent_vertices.setdefault(h, [])
            if v not in adjacent_vertices[h]:
                adjacent_vertices[h].append(v)

    hexes = {
        h: HexTile(coord=h, terrain=terrain, number_token=token)
        for h, (terrain, token) in zip(hex_coords, terrain_tokens)
    }

    return {
        "hexes": hexes,
        "vertices": vertices,
        "edges": edges,
        "vertex_neighbors": vertex_neighbors,
        "edge_endpoints": edge_endpoints,
        "adjacent_edges": adjacent_edges,
        "adjacent_vertices": adjacent_vertices,
    }


def _patch_geometry(monkeypatch, geometry: dict) -> None:
    monkeypatch.setattr(
        board_mod, "vertex_neighbors", lambda v: geometry["vertex_neighbors"].get(v, [])
    )
    monkeypatch.setattr(board_mod, "edge_endpoints", lambda e: geometry["edge_endpoints"][e])
    monkeypatch.setattr(
        board_mod, "get_adjacent_edges", lambda h: geometry["adjacent_edges"].get(h, [])
    )
    monkeypatch.setattr(
        board_mod, "get_adjacent_vertices", lambda h: geometry["adjacent_vertices"].get(h, [])
    )


# ---------------------------------------------------------------------
# Effective-setting gate
# ---------------------------------------------------------------------


def test_sbp_skipped_when_effective_setting_off():
    """4 players, `special_build_phase` unset -> effective off: END_TURN
    advances the turn immediately, no SBP round.
    """
    state = _make_state(num_players=4)
    assert state.settings.special_build_phase is None

    rules_engine.validate_and_apply(state, "p0", EndTurnAction())

    assert state.phase == Phase.ROLL
    assert state.current_player_index == 1
    assert state.special_build_queue == []


def test_sbp_triggers_by_default_at_5_plus_players():
    """5 players, `special_build_phase` unset -> effective on by default."""
    state = _make_state(num_players=5)
    assert state.settings.special_build_phase is None

    rules_engine.validate_and_apply(state, "p0", EndTurnAction())

    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p1", "p2", "p3", "p4"]
    # current_player_index deliberately unchanged until the SBP round
    # finishes -- see GameState.special_build_queue's docstring.
    assert state.current_player_index == 0


def test_explicit_true_enables_sbp_at_4_players():
    """An explicit `True` override enables SBP even below the 5-player
    default threshold.
    """
    state = _make_state(num_players=4, special_build_phase=True)

    rules_engine.validate_and_apply(state, "p0", EndTurnAction())

    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p1", "p2", "p3"]


def test_explicit_false_disables_sbp_at_6_players():
    """An explicit `False` override disables SBP even at 6 players, where
    the unset default would have turned it on.
    """
    state = _make_state(num_players=6, special_build_phase=False)

    rules_engine.validate_and_apply(state, "p0", EndTurnAction())

    assert state.phase == Phase.ROLL
    assert state.current_player_index == 1
    assert state.special_build_queue == []


# ---------------------------------------------------------------------
# Queue walking / turn order
# ---------------------------------------------------------------------


def test_sbp_queue_visits_every_other_player_once_then_resumes_rotation():
    """At 4 players with SBP explicitly on: p0 ends their turn, then
    p1/p2/p3 each take (or pass on) exactly one SBP mini-turn in turn
    order, and the real next full turn is p1's (the player who would
    have gone next anyway -- the SBP round doesn't re-skip anyone).
    """
    state = _make_state(num_players=4, special_build_phase=True)

    rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p1", "p2", "p3"]

    # A player who isn't up yet (p2, p3) can't jump the queue.
    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p2", EndTurnAction())
    assert exc.value.code == "not_your_turn"
    with pytest.raises(RuleViolation):
        rules_engine.validate_and_apply(state, "p3", EndTurnAction())
    # Queue is untouched by the rejected attempts.
    assert state.special_build_queue == ["p1", "p2", "p3"]

    rules_engine.validate_and_apply(state, "p1", EndTurnAction())
    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p2", "p3"]

    rules_engine.validate_and_apply(state, "p2", EndTurnAction())
    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p3"]

    rules_engine.validate_and_apply(state, "p3", EndTurnAction())
    assert state.phase == Phase.ROLL
    assert state.special_build_queue == []
    assert state.current_player_index == 1
    assert state.turn_order[state.current_player_index] == "p1"


# ---------------------------------------------------------------------
# Restricted action set during a player's own SBP turn
# ---------------------------------------------------------------------


def test_roll_dice_rejected_during_own_sbp_turn():
    state = _make_state(num_players=4, special_build_phase=True)
    rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert state.special_build_queue[0] == "p1"

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p1", RollDiceAction())
    assert exc.value.code == "illegal_phase"
    # Nothing should have advanced.
    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p1", "p2", "p3"]


def test_play_dev_card_rejected_during_own_sbp_turn():
    state = _make_state(num_players=4, special_build_phase=True)
    # Give p1 an owned (not just-bought) Knight so this would otherwise
    # be a legal PLAY_DEV_CARD if not for the phase restriction.
    state.players["p1"].dev_cards[DevCardType.KNIGHT] = 1

    rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert state.special_build_queue[0] == "p1"

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(
            state, "p1", PlayDevCardAction(payload=PlayDevCardPayload(card_type=DevCardType.KNIGHT))
        )
    assert exc.value.code == "illegal_phase"
    assert state.phase == Phase.SPECIAL_BUILD
    assert state.players["p1"].dev_cards[DevCardType.KNIGHT] == 1


def test_action_rejected_from_player_not_up_in_sbp_queue():
    state = _make_state(num_players=4, special_build_phase=True)
    rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert state.special_build_queue == ["p1", "p2", "p3"]

    # p2 is in the queue but not up yet -- rejected regardless of the
    # specific action (build, in this case).
    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(
            state,
            "p2",
            BuildRoadAction(payload=BuildRoadPayload(edge_id=(HexCoord(0, 0), HexCoord(1, 0)))),
        )
    assert exc.value.code == "not_your_turn"

    # p0, who already had (and finished) their normal turn, also isn't
    # eligible to act during the SBP round they triggered.
    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert exc.value.code == "not_your_turn"


# ---------------------------------------------------------------------
# Positive path: the SBP actor really can trade/build with resources
# already in hand.
# ---------------------------------------------------------------------


def test_sbp_actor_can_build_with_resources_in_hand(monkeypatch):
    terrain_tokens: list[tuple[Terrain, int | None]] = [
        (Terrain.FOREST, 8),
        (Terrain.HILLS, 6),
        (Terrain.DESERT, None),
    ]
    geometry = _make_chain(0, terrain_tokens)
    _patch_geometry(monkeypatch, geometry)

    board = Board(
        hexes=geometry["hexes"],
        ports=[],
        buildings={},
        roads={},
        robber_hex=next(h for h, t in geometry["hexes"].items() if t.terrain == Terrain.DESERT),
    )

    player_ids = ["p0", "p1", "p2", "p3", "p4"]
    players = {
        pid: PlayerState(player_id=pid, nickname=pid, seat=i) for i, pid in enumerate(player_ids)
    }
    # p1 (next up in the SBP queue after p0) already has a road at the
    # first edge, connecting them to the second edge they're about to
    # build for free... they still have to pay for it (SBP builds cost
    # normal resources, no discount) -- give them enough lumber/brick.
    first_edge, second_edge = geometry["edges"][0], geometry["edges"][1]
    board.roads[first_edge] = "p1"
    players["p1"].hand[ResourceType.LUMBER] = 1
    players["p1"].hand[ResourceType.BRICK] = 1

    state = GameState(
        room_code="TEST",
        settings=GameSettings(player_count=5, special_build_phase=True),
        phase=Phase.MAIN,
        turn_order=player_ids,
        current_player_index=0,
        board=board,
        bank=Bank(resources={r: 19 for r in ResourceType}, dev_card_pile=[]),
        players=players,
    )

    rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p1", "p2", "p3", "p4"]

    rules_engine.validate_and_apply(
        state, "p1", BuildRoadAction(payload=BuildRoadPayload(edge_id=second_edge))
    )

    assert board.roads[second_edge] == "p1"
    assert players["p1"].hand[ResourceType.LUMBER] == 0
    assert players["p1"].hand[ResourceType.BRICK] == 0
    assert players["p1"].roads_remaining == 14
    # p1's own SBP turn isn't over yet -- they still have to submit
    # END_TURN to pass the baton to p2.
    assert state.phase == Phase.SPECIAL_BUILD
    assert state.special_build_queue == ["p1", "p2", "p3", "p4"]

    rules_engine.validate_and_apply(state, "p1", EndTurnAction())
    assert state.special_build_queue == ["p2", "p3", "p4"]
