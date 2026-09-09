"""Tests for `GameSettings.rush_mode`'s concurrent, no-turns model.

Covers: rush_mode vs. normal turn-based mode as mutually exclusive
gating paths (any seated player can act at any time in rush mode's
`Phase.MAIN`, vs. normal mode's strict current-player gate), `END_TURN`
and client-invoked `ROLL_DICE` rejection, `apply_rush_auto_roll`'s
robber-mover rotation (cycling, skipping disconnected players, and the
"don't double-assign on a second 7" tradeoff), the per-player blocking
of only the assigned robber-mover (everyone else stays free), rush
mode's simultaneous `RushModeSetup` placement, and that
`special_build_phase` is inapplicable whenever `rush_mode` is on. Also
directly unit-tests `app.game.rules.rush_timer`'s pure scheduling logic
(no real wall-clock waits needed).

`app.game.board`'s geometry functions are real (Wave 1 has landed), but
this file still uses the same self-contained "linear chain" board helper
`test_rules_engine.py`/`test_special_build_phase.py` use, purely so
setup-placement distance/connectivity scenarios are easy to reason about
by hand -- no `conftest.py`, matching this package's existing convention.
"""

from __future__ import annotations

import pytest

from app.game import board as board_mod
from app.game import dev_cards, rules_engine
from app.game.actions import (
    BankTradeAction,
    BankTradePayload,
    BuildRoadAction,
    BuildRoadPayload,
    BuildSettlementAction,
    BuildSettlementPayload,
    BuyDevCardAction,
    EndTurnAction,
    MoveRobberAction,
    MoveRobberPayload,
    RollDiceAction,
)
from app.game.board import Board, EdgeId, HexCoord, HexTile, Terrain, VertexId
from app.game.players import DevCardType, PlayerState, ResourceType
from app.game.rules import rush_timer
from app.game.rules_engine import RuleViolation
from app.game.settings_schema import GameSettings
from app.game.state import Bank, GameState, Phase

# ---------------------------------------------------------------------
# Fixture-building helpers (self-contained -- no conftest.py)
# ---------------------------------------------------------------------


def _make_rush_state(num_players: int, **settings_kwargs) -> GameState:
    """A rush-mode `Phase.MAIN` state on a minimal 2-hex board (enough to
    exercise `MOVE_ROBBER` -- it needs a second hex to move the robber
    to -- without any buildings, so every robber move auto-resolves with
    zero steal candidates unless a test adds buildings itself).
    `discard_limit` defaults high so tests that don't care about discards
    don't incidentally trigger them via a 7.
    """
    hex_a = HexCoord(0, 0)
    hex_b = HexCoord(1, 0)
    board = Board(
        hexes={
            hex_a: HexTile(coord=hex_a, terrain=Terrain.DESERT, number_token=None),
            hex_b: HexTile(coord=hex_b, terrain=Terrain.FOREST, number_token=8),
        },
        ports=[],
        buildings={},
        roads={},
        robber_hex=hex_a,
    )
    player_ids = [f"p{i}" for i in range(num_players)]
    players = {
        pid: PlayerState(player_id=pid, nickname=pid, seat=i) for i, pid in enumerate(player_ids)
    }
    settings_kwargs.setdefault("player_count", num_players)
    settings_kwargs.setdefault("rush_mode", True)
    settings_kwargs.setdefault("discard_limit", 50)
    return GameState(
        room_code="TEST",
        settings=GameSettings(**settings_kwargs),
        phase=Phase.MAIN,
        turn_order=player_ids,
        board=board,
        bank=Bank(resources={r: 19 for r in ResourceType}, dev_card_pile=[]),
        players=players,
    )


def _resolve_robber(state: GameState, actor: str) -> None:
    """Move the robber (as `actor`, who must be `rush_pending_robber.actor`)
    to whichever of `_make_rush_state`'s two hexes it isn't currently on.
    With no buildings on the board this always auto-resolves (zero steal
    candidates), clearing `rush_pending_robber`.
    """
    hex_a, hex_b = HexCoord(0, 0), HexCoord(1, 0)
    target = hex_b if state.board.robber_hex == hex_a else hex_a
    rules_engine.validate_and_apply(state, actor, MoveRobberAction(payload=MoveRobberPayload(hex=target)))


def _fixed_dice(monkeypatch, rolls: list[int]) -> None:
    """Make `random.randint(1, 6)` return a fixed sequence (two calls per
    roll). Mirrors `test_rules_engine.py`'s identical helper.
    """
    it = iter(rolls)
    monkeypatch.setattr(rules_engine.random, "randint", lambda a, b: next(it))


def _make_chain(start_q: int, terrain_tokens: list[tuple[Terrain, int | None]]) -> dict:
    """A linear chain of hexes (axial q = start_q.., r = 0), one per
    `terrain_tokens` entry, with one vertex per hex-to-hex junction (plus
    the two end caps) and one edge per consecutive vertex pair. Copied
    from `test_rules_engine.py`'s identical helper -- see there for the
    full derivation rationale.
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


def _make_setup_state(num_players: int, geometry: dict) -> GameState:
    board = Board(
        hexes=geometry["hexes"], ports=[], buildings={}, roads={}, robber_hex=next(iter(geometry["hexes"]))
    )
    player_ids = [f"p{i}" for i in range(num_players)]
    players = {
        pid: PlayerState(player_id=pid, nickname=pid, seat=i) for i, pid in enumerate(player_ids)
    }
    return GameState(
        room_code="TEST",
        settings=GameSettings(player_count=num_players, rush_mode=True),
        phase=Phase.SETUP,
        turn_order=player_ids,
        board=board,
        bank=Bank(resources={r: 19 for r in ResourceType}, dev_card_pile=[]),
        players=players,
    )


# ---------------------------------------------------------------------
# Mutual exclusivity: rush_mode vs. normal turn-based gating
# ---------------------------------------------------------------------


def test_any_seated_player_can_bank_trade_at_any_time_in_rush_main():
    state = _make_rush_state(4)
    assert state.current_player_index == 0  # "p0" is nominally "current" -- irrelevant in rush mode

    for actor_id in ["p0", "p1", "p2", "p3"]:
        state.players[actor_id].hand[ResourceType.LUMBER] = 4
        rules_engine.validate_and_apply(
            state,
            actor_id,
            BankTradeAction(
                payload=BankTradePayload(offered={ResourceType.LUMBER: 4}, requested={ResourceType.BRICK: 1})
            ),
        )
        assert state.players[actor_id].hand[ResourceType.BRICK] == 1


def test_any_seated_player_can_buy_dev_card_at_any_time_in_rush_main():
    state = _make_rush_state(4)
    state.bank.dev_card_pile = [DevCardType.KNIGHT]
    player = state.players["p3"]
    player.hand[ResourceType.ORE] = 1
    player.hand[ResourceType.WOOL] = 1
    player.hand[ResourceType.GRAIN] = 1

    rules_engine.validate_and_apply(state, "p3", BuyDevCardAction())

    assert dev_cards.total_owned(player) == 1


def test_normal_mode_still_gates_build_trade_by_current_player():
    """Control case proving rush_mode and normal mode are genuinely
    different, mutually exclusive gating paths: the exact same action
    from a non-current player is rejected outside rush mode.
    """
    state = _make_rush_state(4, rush_mode=False)
    state.players["p2"].hand[ResourceType.LUMBER] = 4

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(
            state,
            "p2",
            BankTradeAction(
                payload=BankTradePayload(offered={ResourceType.LUMBER: 4}, requested={ResourceType.BRICK: 1})
            ),
        )
    assert exc.value.code == "not_your_turn"


def test_end_turn_rejected_in_rush_mode():
    state = _make_rush_state(4)

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p0", EndTurnAction())

    assert exc.value.code == "rush_mode_no_turns"
    assert state.phase == Phase.MAIN  # unchanged


def test_roll_dice_rejected_as_a_client_action_in_rush_mode():
    """ROLL_DICE is never legal in rush_mode's Phase.MAIN (only rolled
    automatically by `apply_rush_auto_roll`) -- rejected by the phase
    table itself before rules_engine's own rush-mode-specific check in
    `_validate_roll_dice` would even run, since rush mode never actually
    reaches `Phase.ROLL` where ROLL_DICE would otherwise be legal.
    """
    state = _make_rush_state(4)

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p0", RollDiceAction())

    assert exc.value.code == "illegal_phase"
    assert state.last_dice_roll is None


# ---------------------------------------------------------------------
# apply_rush_auto_roll: robber-mover rotation
# ---------------------------------------------------------------------


def test_rush_robber_rotation_cycles_through_seated_players(monkeypatch):
    state = _make_rush_state(4)
    _fixed_dice(monkeypatch, [3, 4] * 8)  # always a 7

    for expected in ["p0", "p1", "p2", "p3", "p0"]:
        rules_engine.apply_rush_auto_roll(state)
        assert state.rush_pending_robber is not None
        assert state.rush_pending_robber.actor == expected
        _resolve_robber(state, expected)
        assert state.rush_pending_robber is None


def test_rush_robber_rotation_skips_disconnected_players(monkeypatch):
    state = _make_rush_state(4)
    state.players["p2"].is_connected = False
    _fixed_dice(monkeypatch, [3, 4] * 8)

    for expected in ["p0", "p1", "p3", "p0"]:
        rules_engine.apply_rush_auto_roll(state)
        assert state.rush_pending_robber.actor == expected
        _resolve_robber(state, expected)


def test_rush_robber_no_double_assign_on_consecutive_sevens(monkeypatch):
    """A second 7 firing before the previous robber-move resolves must
    not create a second concurrent assignment for the one physical
    robber -- see `GameState.rush_robber_turn_index`'s documented
    "queue by skipping" tradeoff.
    """
    state = _make_rush_state(4)
    _fixed_dice(monkeypatch, [3, 4] * 8)

    rules_engine.apply_rush_auto_roll(state)
    assert state.rush_pending_robber.actor == "p0"

    # Second 7 fires while p0's move is still unresolved.
    rules_engine.apply_rush_auto_roll(state)
    assert state.rush_pending_robber.actor == "p0"  # unchanged, not reassigned

    _resolve_robber(state, "p0")

    # The skipped second 7 advanced the rotation pointer past p1, so the
    # next fresh assignment goes to p2, not p1.
    rules_engine.apply_rush_auto_roll(state)
    assert state.rush_pending_robber.actor == "p2"


def test_rush_auto_roll_sets_last_dice_roll_and_folds_dev_cards(monkeypatch):
    state = _make_rush_state(2)
    state.players["p0"].dev_cards_bought_this_turn[DevCardType.KNIGHT] = 1
    _fixed_dice(monkeypatch, [2, 3])  # total 5, non-7

    events = rules_engine.apply_rush_auto_roll(state)

    assert state.last_dice_roll == (2, 3)
    assert state.last_dice_roll_ts is not None
    # Dev cards bought "this turn" become playable at the next auto-roll,
    # since rush mode has no END_TURN to fold them at -- see
    # apply_rush_auto_roll's docstring.
    assert state.players["p0"].dev_cards[DevCardType.KNIGHT] == 1
    assert state.players["p0"].dev_cards_bought_this_turn[DevCardType.KNIGHT] == 0
    assert any(ev.type.value == "DICE_ROLLED" for ev in events)


# ---------------------------------------------------------------------
# Per-player blocking: only the assigned robber-mover is restricted
# ---------------------------------------------------------------------


def test_non_assigned_player_can_still_act_while_a_robber_move_is_pending(monkeypatch):
    state = _make_rush_state(4)
    _fixed_dice(monkeypatch, [3, 4])
    rules_engine.apply_rush_auto_roll(state)
    assert state.rush_pending_robber.actor == "p0"

    state.players["p1"].hand[ResourceType.LUMBER] = 4
    rules_engine.validate_and_apply(
        state,
        "p1",
        BankTradeAction(
            payload=BankTradePayload(offered={ResourceType.LUMBER: 4}, requested={ResourceType.BRICK: 1})
        ),
    )
    assert state.players["p1"].hand[ResourceType.BRICK] == 1
    # p0's robber obligation is untouched by p1 acting.
    assert state.rush_pending_robber.actor == "p0"


def test_assigned_player_is_blocked_until_they_resolve_move_robber(monkeypatch):
    state = _make_rush_state(4)
    _fixed_dice(monkeypatch, [3, 4])
    rules_engine.apply_rush_auto_roll(state)
    assert state.rush_pending_robber.actor == "p0"

    state.players["p0"].hand[ResourceType.LUMBER] = 4
    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(
            state,
            "p0",
            BankTradeAction(
                payload=BankTradePayload(offered={ResourceType.LUMBER: 4}, requested={ResourceType.BRICK: 1})
            ),
        )
    assert exc.value.code == "action_pending"
    assert state.players["p0"].hand[ResourceType.BRICK] == 0

    _resolve_robber(state, "p0")

    # Now free to act again.
    rules_engine.validate_and_apply(
        state,
        "p0",
        BankTradeAction(
            payload=BankTradePayload(offered={ResourceType.LUMBER: 4}, requested={ResourceType.BRICK: 1})
        ),
    )
    assert state.players["p0"].hand[ResourceType.BRICK] == 1


# ---------------------------------------------------------------------
# Simultaneous setup placement (RushModeSetup)
# ---------------------------------------------------------------------

#: 8-hex chain -> 9 vertices (v0..v8) / 8 edges (e0..e7), each vertex
#: adjacent only to its immediate chain neighbor(s) -- plenty of room for
#: two players' 2 settlements + 2 roads each without incidental distance
#: conflicts, chosen with a vertex gap between any two players' pieces.
_SETUP_TERRAIN_TOKENS: list[tuple[Terrain, int | None]] = [
    (Terrain.FOREST, 8),
    (Terrain.HILLS, 6),
    (Terrain.MOUNTAINS, 5),
    (Terrain.FIELDS, 9),
    (Terrain.PASTURE, 4),
    (Terrain.FOREST, 3),
    (Terrain.HILLS, 10),
    (Terrain.DESERT, None),
]


def test_rush_setup_allows_simultaneous_placement_in_any_relative_order(monkeypatch):
    geometry = _make_chain(0, _SETUP_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    e = geometry["edges"]

    state = _make_setup_state(2, geometry)

    # p0's first settlement, then (before p0 finishes) p1's first
    # settlement -- interleaved, not sequential.
    rules_engine.validate_and_apply(state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[0])))
    rules_engine.validate_and_apply(state, "p1", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[3])))

    # p0 completes their FULL setup allotment before p1 places anything else.
    rules_engine.validate_and_apply(state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[0])))
    rules_engine.validate_and_apply(state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[5])))
    rules_engine.validate_and_apply(state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[4])))

    assert state.phase == Phase.SETUP  # p1 hasn't finished yet
    assert state.last_dice_roll_ts is None

    # p1 was never blocked by p0's progress -- picks up right where they
    # left off.
    rules_engine.validate_and_apply(state, "p1", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[2])))
    rules_engine.validate_and_apply(state, "p1", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[7])))
    rules_engine.validate_and_apply(state, "p1", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[6])))

    assert state.phase == Phase.MAIN
    assert state.last_dice_roll_ts is not None
    assert len(state.board.buildings) == 4
    assert len(state.board.roads) == 4


def test_rush_setup_rejects_wrong_next_action_for_that_player(monkeypatch):
    geometry = _make_chain(0, _SETUP_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    e = geometry["edges"]

    state = _make_setup_state(2, geometry)

    # p0 owes a settlement first, not a road.
    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[0])))
    assert exc.value.code == "wrong_setup_action"


def test_rush_setup_rejects_placement_after_players_own_quota_complete(monkeypatch):
    geometry = _make_chain(0, _SETUP_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    e = geometry["edges"]

    state = _make_setup_state(2, geometry)
    rules_engine.validate_and_apply(state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[0])))
    rules_engine.validate_and_apply(state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[0])))
    rules_engine.validate_and_apply(state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[5])))
    rules_engine.validate_and_apply(state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[4])))

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(
            state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[7]))
        )
    assert exc.value.code == "setup_already_complete"


# ---------------------------------------------------------------------
# special_build_phase is inapplicable whenever rush_mode is on
# ---------------------------------------------------------------------


def test_effective_special_build_phase_false_when_rush_mode_on_even_if_explicitly_true():
    settings = GameSettings(rush_mode=True, special_build_phase=True, player_count=6)
    assert rules_engine._effective_special_build_phase(settings) is False

    # Sanity: the same explicit True DOES turn SBP on when rush_mode is off.
    settings_normal = GameSettings(rush_mode=False, special_build_phase=True, player_count=4)
    assert rules_engine._effective_special_build_phase(settings_normal) is True


def test_end_turn_rejected_regardless_of_special_build_phase_value_in_rush_mode():
    state = _make_rush_state(4, special_build_phase=True)

    with pytest.raises(RuleViolation) as exc:
        rules_engine.validate_and_apply(state, "p0", EndTurnAction())

    assert exc.value.code == "rush_mode_no_turns"
    assert state.special_build_queue == []
    assert state.phase == Phase.MAIN


# ---------------------------------------------------------------------
# app.game.rules.rush_timer: pure scheduling logic (no real wall-clock waits)
# ---------------------------------------------------------------------


def test_should_auto_roll_pure_logic():
    assert rush_timer.should_auto_roll(15, last_roll_ts=100.0, now_ts=114.9) is False
    assert rush_timer.should_auto_roll(15, last_roll_ts=100.0, now_ts=115.0) is True
    assert rush_timer.should_auto_roll(15, last_roll_ts=100.0, now_ts=500.0) is True
    assert rush_timer.should_auto_roll(None, last_roll_ts=0.0, now_ts=1_000_000.0) is False
    assert rush_timer.should_auto_roll(0, last_roll_ts=0.0, now_ts=1_000_000.0) is False


def test_seconds_until_next_roll_pure_logic():
    assert rush_timer.seconds_until_next_roll(15, last_roll_ts=100.0, now_ts=110.0) == pytest.approx(5.0)
    assert rush_timer.seconds_until_next_roll(15, last_roll_ts=100.0, now_ts=130.0) == 0.0
    assert rush_timer.seconds_until_next_roll(None, last_roll_ts=100.0, now_ts=110.0) is None
