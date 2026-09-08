"""Tests for `app.game.rules_engine` covering real game scenarios: setup
placement through a full turn, resource distribution, trading, dev
cards, the robber (move/steal/discard), Nuke Mode, and the win condition.

`app.game.board`'s geometry functions raise `NotImplementedError` in this
worktree (the real implementation is Wave 1 work on a parallel branch),
so every test monkeypatches them with a small, internally-consistent
linear "chain" board via `_make_chain`/`_patch_geometry` below -- see
`test_scoring.py`'s module docstring for the same approach.
"""

from __future__ import annotations

import pytest

from app.game import board as board_mod
from app.game import rules_engine
from app.game.actions import (
    BankTradeAction,
    BankTradePayload,
    BuildCityAction,
    BuildCityPayload,
    BuildRoadAction,
    BuildRoadPayload,
    BuildSettlementAction,
    BuildSettlementPayload,
    BuyDevCardAction,
    DiscardCardsAction,
    DiscardCardsPayload,
    EndTurnAction,
    MoveRobberAction,
    MoveRobberPayload,
    PlayDevCardAction,
    PlayDevCardPayload,
    PlayNukeAction,
    PlayNukePayload,
    PortTradeAction,
    PortTradePayload,
    ProposeTradeAction,
    ProposeTradePayload,
    RespondTradeAction,
    RespondTradePayload,
    RollDiceAction,
    StealResourceAction,
    StealResourcePayload,
)
from app.game.board import (
    Board,
    BuildingType,
    EdgeId,
    HexCoord,
    HexTile,
    Port,
    PortType,
    Terrain,
    VertexBuilding,
    VertexId,
)
from app.game.players import DevCardType, PlayerState, ResourceType
from app.game.rules_engine import RuleViolation
from app.game.settings_schema import GameSettings
from app.game.state import (
    AwaitingDiscard,
    AwaitingSteal,
    Bank,
    GameState,
    Phase,
)

# ---------------------------------------------------------------------
# Shared fixture-building helpers (self-contained per the task's scope --
# no conftest.py)
# ---------------------------------------------------------------------


def _make_chain(start_q: int, terrain_tokens: list[tuple[Terrain, int | None]]) -> dict:
    """A linear chain of hexes (axial q = start_q.., r = 0), one per
    `terrain_tokens` entry, with one vertex per hex-to-hex junction (plus
    the two end caps) and one edge per consecutive vertex pair. See
    `test_scoring.py`'s identical helper for the full derivation
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


#: Standard scenario chain: lumber(8), brick(6), ore(5), grain(9), wool(4),
#: desert (robber start, no token).
STANDARD_TERRAIN_TOKENS: list[tuple[Terrain, int | None]] = [
    (Terrain.FOREST, 8),
    (Terrain.HILLS, 6),
    (Terrain.MOUNTAINS, 5),
    (Terrain.FIELDS, 9),
    (Terrain.PASTURE, 4),
    (Terrain.DESERT, None),
]


def _make_state(
    geometry: dict,
    num_players: int = 2,
    ports: list[Port] | None = None,
    bank_resources: int = 19,
    dev_card_pile: list[DevCardType] | None = None,
    **settings_kwargs,
) -> GameState:
    board = Board(
        hexes=geometry["hexes"],
        ports=ports or [],
        buildings={},
        roads={},
        robber_hex=next(h for h, tile in geometry["hexes"].items() if tile.terrain == Terrain.DESERT)
        if any(tile.terrain == Terrain.DESERT for tile in geometry["hexes"].values())
        else next(iter(geometry["hexes"])),
    )
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
        board=board,
        bank=Bank(
            resources={r: bank_resources for r in ResourceType},
            dev_card_pile=dev_card_pile or [],
        ),
        players=players,
    )


def _fixed_dice(monkeypatch, rolls: list[int]) -> None:
    """Make `random.randint(1, 6)` return a fixed sequence of values (two
    calls per `ROLL_DICE`, i.e. `len(rolls)` values covers `len(rolls)//2`
    rolls).
    """
    it = iter(rolls)
    monkeypatch.setattr(rules_engine.random, "randint", lambda a, b: next(it))


# ---------------------------------------------------------------------
# Full simulated turn sequence: setup -> roll -> distribute -> build ->
# buy dev card -> end turn.
# ---------------------------------------------------------------------


def test_full_setup_and_turn_sequence(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    e = geometry["edges"]

    state = _make_state(
        geometry,
        num_players=2,
        dev_card_pile=[DevCardType.MONOPOLY, DevCardType.KNIGHT],
    )
    state.phase = Phase.SETUP

    # --- Setup: snake draft order for 2 players is [p0, p1, p1, p0]. ---

    # p0's first settlement + road.
    rules_engine.validate_and_apply(
        state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[0]))
    )
    rules_engine.validate_and_apply(
        state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[0]))
    )
    assert state.players["p0"].victory_points == 1
    assert sum(state.players["p0"].hand.values()) == 0  # no resources for the 1st settlement

    # p1's first settlement + road.
    rules_engine.validate_and_apply(
        state, "p1", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[2]))
    )
    rules_engine.validate_and_apply(
        state, "p1", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[2]))
    )

    # p1's second settlement + road -- grants resources for hexes
    # adjacent to v[4] = (fields/grain, pasture/wool).
    rules_engine.validate_and_apply(
        state, "p1", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[4]))
    )
    assert state.players["p1"].hand[ResourceType.GRAIN] == 1
    assert state.players["p1"].hand[ResourceType.WOOL] == 1
    rules_engine.validate_and_apply(
        state, "p1", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[4]))
    )

    # p0's second settlement + road -- v[6] only touches the desert, so
    # no resources are granted.
    rules_engine.validate_and_apply(
        state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[6]))
    )
    assert sum(state.players["p0"].hand.values()) == 0
    rules_engine.validate_and_apply(
        state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[5]))
    )

    # Setup is complete: phase -> ROLL, p0 goes first.
    assert state.phase == Phase.ROLL
    assert state.current_player_index == 0
    assert state.players["p0"].victory_points == 2
    assert state.players["p1"].victory_points == 2

    # --- p0's turn: roll (non-7), get resources, build, buy dev card. ---

    _fixed_dice(monkeypatch, [4, 4])  # total 8 -> forest (lumber) hex
    events = rules_engine.validate_and_apply(state, "p0", RollDiceAction())
    assert state.phase == Phase.MAIN
    assert state.last_dice_roll == (4, 4)
    dice_event = next(e for e in events if e.type.value == "DICE_ROLLED")
    assert dice_event.payload.total == 8
    # v[0] (p0's settlement) touches the forest/8 hex -> 1 lumber.
    assert state.players["p0"].hand[ResourceType.LUMBER] == 1

    # Manually top up the brick p0 needs for a road (e.g. from an earlier
    # trade not otherwise modeled in this test) so BUILD_ROAD can be
    # exercised as part of the same turn sequence.
    state.players["p0"].hand[ResourceType.BRICK] += 1
    rules_engine.validate_and_apply(
        state, "p0", BuildRoadAction(payload=BuildRoadPayload(edge_id=e[1]))
    )
    assert state.board.roads[e[1]] == "p0"
    assert state.players["p0"].hand[ResourceType.LUMBER] == 0
    assert state.players["p0"].hand[ResourceType.BRICK] == 0

    # Buy a dev card (top up the cost directly, as above).
    state.players["p0"].hand[ResourceType.ORE] += 1
    state.players["p0"].hand[ResourceType.WOOL] += 1
    state.players["p0"].hand[ResourceType.GRAIN] += 1
    rules_engine.validate_and_apply(state, "p0", BuyDevCardAction())
    assert state.players["p0"].dev_cards_bought_this_turn[DevCardType.KNIGHT] == 1
    assert state.bank.dev_card_pile == [DevCardType.MONOPOLY]

    # Can't play a card bought this turn.
    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate(
            state, "p0", PlayDevCardAction(payload=PlayDevCardPayload(card_type=DevCardType.KNIGHT))
        )
    assert exc_info.value.code == "card_not_owned"

    # End turn: bought card folds into the playable hand, turn advances.
    rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert state.players["p0"].dev_cards[DevCardType.KNIGHT] == 1
    assert state.players["p0"].dev_cards_bought_this_turn[DevCardType.KNIGHT] == 0
    assert state.current_player_index == 1
    assert state.phase == Phase.ROLL


# ---------------------------------------------------------------------
# Phase-table gate (checked before any rule-specific validation)
# ---------------------------------------------------------------------


def test_phase_table_rejects_illegal_action_before_rule_checks(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.phase = Phase.ROLL  # only ROLL_DICE is legal here

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate(
            state,
            "p0",
            BuildRoadAction(payload=BuildRoadPayload(edge_id=geometry["edges"][0])),
        )
    assert exc_info.value.code == "illegal_phase"


# ---------------------------------------------------------------------
# Placement legality: distance rule + connectivity
# ---------------------------------------------------------------------


def test_settlement_distance_rule_violation(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    state = _make_state(geometry, num_players=2)
    state.board.buildings[v[1]] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    state.players["p0"].hand.update({r: 5 for r in ResourceType})
    state.board.roads[geometry["edges"][0]] = "p0"  # so connectivity alone wouldn't block it

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[0]))
        )
    assert exc_info.value.code == "distance_rule"


def test_build_city_upgrades_settlement_and_returns_settlement_piece(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    state = _make_state(geometry, num_players=2)
    state.board.buildings[v[0]] = VertexBuilding(player_id="p0", building_type=BuildingType.SETTLEMENT)
    state.players["p0"].settlements_remaining = 4  # one already placed
    state.players["p0"].hand.update({ResourceType.ORE: 3, ResourceType.GRAIN: 2})

    rules_engine.validate_and_apply(
        state, "p0", BuildCityAction(payload=BuildCityPayload(vertex_id=v[0]))
    )

    assert state.board.buildings[v[0]].building_type == BuildingType.CITY
    assert state.players["p0"].cities_remaining == 3
    assert state.players["p0"].settlements_remaining == 5  # piece returned to supply
    assert state.players["p0"].hand[ResourceType.ORE] == 0
    assert state.players["p0"].hand[ResourceType.GRAIN] == 0
    assert state.players["p0"].victory_points == 2


def test_build_city_rejects_upgrading_others_settlement(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    state = _make_state(geometry, num_players=2)
    state.board.buildings[v[0]] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    state.players["p0"].hand.update({ResourceType.ORE: 3, ResourceType.GRAIN: 2})

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state, "p0", BuildCityAction(payload=BuildCityPayload(vertex_id=v[0]))
        )
    assert exc_info.value.code == "invalid_target"


def test_settlement_requires_connectivity_in_main_phase(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    state = _make_state(geometry, num_players=2)
    state.players["p0"].hand.update({r: 5 for r in ResourceType})

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[3]))
        )
    assert exc_info.value.code == "not_connected"


# ---------------------------------------------------------------------
# Bank trade (4:1) and port trade (2:1 / 3:1)
# ---------------------------------------------------------------------


def test_bank_trade_four_to_one(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.players["p0"].hand[ResourceType.LUMBER] = 4

    rules_engine.validate_and_apply(
        state,
        "p0",
        BankTradeAction(
            payload=BankTradePayload(
                offered={ResourceType.LUMBER: 4}, requested={ResourceType.ORE: 1}
            )
        ),
    )

    assert state.players["p0"].hand[ResourceType.LUMBER] == 0
    assert state.players["p0"].hand[ResourceType.ORE] == 1
    assert state.bank.resources[ResourceType.LUMBER] == 23
    assert state.bank.resources[ResourceType.ORE] == 18


def test_bank_trade_rejects_wrong_ratio(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.players["p0"].hand[ResourceType.LUMBER] = 3

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state,
            "p0",
            BankTradeAction(
                payload=BankTradePayload(
                    offered={ResourceType.LUMBER: 3}, requested={ResourceType.ORE: 1}
                )
            ),
        )
    assert exc_info.value.code == "invalid_ratio"


def test_port_trade_uses_best_owned_port_rate(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    port = Port(port_type=PortType.WOOL, vertices=(v[0], v[1]))
    state = _make_state(geometry, num_players=2, ports=[port])
    state.board.buildings[v[0]] = VertexBuilding(player_id="p0", building_type=BuildingType.SETTLEMENT)
    state.players["p0"].hand[ResourceType.WOOL] = 2

    rules_engine.validate_and_apply(
        state,
        "p0",
        PortTradeAction(
            payload=PortTradePayload(
                offered={ResourceType.WOOL: 2}, requested={ResourceType.ORE: 1}
            )
        ),
    )

    assert state.players["p0"].hand[ResourceType.WOOL] == 0
    assert state.players["p0"].hand[ResourceType.ORE] == 1


def test_port_trade_rejects_without_port_access(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    port = Port(port_type=PortType.WOOL, vertices=(v[0], v[1]))
    state = _make_state(geometry, num_players=2, ports=[port])
    # p0 does NOT own a building at v[0]/v[1] -> falls back to the 4:1
    # bank rate, so offering only 2 wool is an invalid ratio.
    state.players["p0"].hand[ResourceType.WOOL] = 2

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state,
            "p0",
            PortTradeAction(
                payload=PortTradePayload(
                    offered={ResourceType.WOOL: 2}, requested={ResourceType.ORE: 1}
                )
            ),
        )
    assert exc_info.value.code == "invalid_ratio"


# ---------------------------------------------------------------------
# Propose / respond trade
# ---------------------------------------------------------------------


def test_propose_and_accept_trade(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.players["p0"].hand[ResourceType.LUMBER] = 1
    state.players["p1"].hand[ResourceType.ORE] = 1

    rules_engine.validate_and_apply(
        state,
        "p0",
        ProposeTradeAction(
            payload=ProposeTradePayload(
                offered={ResourceType.LUMBER: 1}, requested={ResourceType.ORE: 1}
            )
        ),
    )
    assert state.pending is not None
    trade_id = state.pending.trade_id

    events = rules_engine.validate_and_apply(
        state,
        "p1",
        RespondTradeAction(payload=RespondTradePayload(trade_id=trade_id, accept=True)),
    )

    assert state.pending is None
    assert state.players["p0"].hand[ResourceType.LUMBER] == 0
    assert state.players["p0"].hand[ResourceType.ORE] == 1
    assert state.players["p1"].hand[ResourceType.ORE] == 0
    assert state.players["p1"].hand[ResourceType.LUMBER] == 1
    resolved = next(e for e in events if e.type.value == "TRADE_RESOLVED")
    assert resolved.payload.status == "accepted"


def test_propose_trade_declined_by_all_targets(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.players["p0"].hand[ResourceType.LUMBER] = 1

    rules_engine.validate_and_apply(
        state,
        "p0",
        ProposeTradeAction(
            payload=ProposeTradePayload(
                offered={ResourceType.LUMBER: 1}, requested={ResourceType.ORE: 1}
            )
        ),
    )
    trade_id = state.pending.trade_id

    events = rules_engine.validate_and_apply(
        state,
        "p1",
        RespondTradeAction(payload=RespondTradePayload(trade_id=trade_id, accept=False)),
    )

    assert state.pending is None
    resolved = next(e for e in events if e.type.value == "TRADE_RESOLVED")
    assert resolved.payload.status == "declined"
    # Nothing changed hands.
    assert state.players["p0"].hand[ResourceType.LUMBER] == 1


def test_end_turn_cancels_pending_trade(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.players["p0"].hand[ResourceType.LUMBER] = 1

    rules_engine.validate_and_apply(
        state,
        "p0",
        ProposeTradeAction(
            payload=ProposeTradePayload(
                offered={ResourceType.LUMBER: 1}, requested={ResourceType.ORE: 1}
            )
        ),
    )
    assert state.pending is not None

    events = rules_engine.validate_and_apply(state, "p0", EndTurnAction())
    assert state.pending is None
    resolved = next(e for e in events if e.type.value == "TRADE_RESOLVED")
    assert resolved.payload.status == "cancelled"


# ---------------------------------------------------------------------
# Robber: move + steal (single and multiple candidates)
# ---------------------------------------------------------------------


def test_robber_move_auto_resolves_single_candidate(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    state = _make_state(geometry, num_players=2)
    forest_hex = geometry["hexes"][HexCoord(0, 0)].coord
    state.board.buildings[v[0]] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    state.players["p1"].hand[ResourceType.LUMBER] = 1  # p1's only card, forces the steal outcome
    state.board.robber_hex = HexCoord(5, 0)  # currently on the desert
    state.pending = None
    from app.game.state import AwaitingRobberPlacement

    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingRobberPlacement(actor="p0", reason="dice_roll")

    events = rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=forest_hex))
    )

    assert state.board.robber_hex == forest_hex
    assert state.pending is None
    assert state.phase == Phase.MAIN
    assert state.players["p0"].hand[ResourceType.LUMBER] == 1
    assert state.players["p1"].hand[ResourceType.LUMBER] == 0
    steal_event = next(e for e in events if e.type.value == "RESOURCE_STOLEN")
    assert steal_event.payload.victim == "p1"
    assert steal_event.payload.resource == ResourceType.LUMBER


def test_robber_move_with_multiple_candidates_awaits_steal_choice(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    state = _make_state(geometry, num_players=3)
    forest_hex = HexCoord(0, 0)
    state.board.buildings[v[0]] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    state.board.buildings[v[1]] = VertexBuilding(player_id="p2", building_type=BuildingType.SETTLEMENT)
    state.players["p1"].hand[ResourceType.LUMBER] = 1
    state.players["p2"].hand[ResourceType.BRICK] = 1
    state.board.robber_hex = HexCoord(5, 0)

    from app.game.state import AwaitingRobberPlacement

    state.phase = Phase.ROBBER_MOVE
    state.pending = AwaitingRobberPlacement(actor="p0", reason="dice_roll")

    rules_engine.validate_and_apply(
        state, "p0", MoveRobberAction(payload=MoveRobberPayload(hex=forest_hex))
    )

    assert isinstance(state.pending, AwaitingSteal)
    assert set(state.pending.candidate_targets) == {"p1", "p2"}
    assert state.phase == Phase.ROBBER_MOVE

    events = rules_engine.validate_and_apply(
        state, "p0", StealResourceAction(payload=StealResourcePayload(target_player_id="p2"))
    )

    assert state.pending is None
    assert state.phase == Phase.MAIN
    assert state.players["p0"].hand[ResourceType.BRICK] == 1
    assert state.players["p2"].hand[ResourceType.BRICK] == 0
    steal_event = next(e for e in events if e.type.value == "RESOURCE_STOLEN")
    assert steal_event.payload.victim == "p2"


# ---------------------------------------------------------------------
# 7-roll triggers discards for players over the limit
# ---------------------------------------------------------------------


def test_seven_roll_triggers_discards_for_players_over_limit(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=3, discard_limit=4)
    state.turn_order = ["p0", "p1", "p2"]
    state.phase = Phase.ROLL
    state.players["p0"].hand[ResourceType.LUMBER] = 7  # owes 3
    state.players["p1"].hand[ResourceType.BRICK] = 9  # owes 4
    state.players["p2"].hand[ResourceType.ORE] = 2  # under the limit, owes nothing

    _fixed_dice(monkeypatch, [3, 4])  # total 7
    events = rules_engine.validate_and_apply(state, "p0", RollDiceAction())

    assert state.phase == Phase.ROBBER_DISCARD
    assert isinstance(state.pending, AwaitingDiscard)
    assert state.pending.required_counts == {"p0": 3, "p1": 4}
    discard_event = next(e for e in events if e.type.value == "DISCARD_REQUIRED")
    assert discard_event.payload.required_counts == {"p0": 3, "p1": 4}

    # p0 discards their owed 3.
    rules_engine.validate_and_apply(
        state,
        "p0",
        DiscardCardsAction(payload=DiscardCardsPayload(resources={ResourceType.LUMBER: 3})),
    )
    assert "p0" not in state.pending.required_counts
    assert state.phase == Phase.ROBBER_DISCARD  # p1 still owes

    # Wrong discard count is rejected.
    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state,
            "p1",
            DiscardCardsAction(payload=DiscardCardsPayload(resources={ResourceType.BRICK: 1})),
        )
    assert exc_info.value.code == "wrong_discard_count"

    # p1 discards their owed 4 -> discards resolved, robber placement owed.
    rules_engine.validate_and_apply(
        state,
        "p1",
        DiscardCardsAction(payload=DiscardCardsPayload(resources={ResourceType.BRICK: 4})),
    )
    assert state.phase == Phase.ROBBER_MOVE
    assert state.pending.kind == "awaiting_robber_placement"
    assert state.pending.actor == "p0"  # the player who rolled the 7
    assert state.players["p0"].hand[ResourceType.LUMBER] == 4
    assert state.players["p1"].hand[ResourceType.BRICK] == 5
    assert state.bank.resources[ResourceType.LUMBER] == 19 + 3
    assert state.bank.resources[ResourceType.BRICK] == 19 + 4


# ---------------------------------------------------------------------
# Dev cards: knight -> largest army + robber placement
# ---------------------------------------------------------------------


def test_knight_card_updates_largest_army_and_triggers_robber_move(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.players["p0"].dev_cards[DevCardType.KNIGHT] = 3
    state.players["p0"].knights_played = 2  # this play will be their 3rd

    events = rules_engine.validate_and_apply(
        state, "p0", PlayDevCardAction(payload=PlayDevCardPayload(card_type=DevCardType.KNIGHT))
    )

    assert state.players["p0"].knights_played == 3
    assert state.largest_army_holder == "p0"
    assert state.phase == Phase.ROBBER_MOVE
    assert state.pending.kind == "awaiting_robber_placement"
    assert state.pending.reason == "knight_card"
    army_event = next(e for e in events if e.type.value == "LARGEST_ARMY_CHANGED")
    assert army_event.payload.new_holder == "p0"


def test_monopoly_card_transfers_resource_from_all_others(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=3)
    state.players["p0"].dev_cards[DevCardType.MONOPOLY] = 1
    state.players["p1"].hand[ResourceType.ORE] = 2
    state.players["p2"].hand[ResourceType.ORE] = 1

    rules_engine.validate_and_apply(
        state,
        "p0",
        PlayDevCardAction(
            payload=PlayDevCardPayload(
                card_type=DevCardType.MONOPOLY, monopoly_resource=ResourceType.ORE
            )
        ),
    )

    assert state.players["p0"].hand[ResourceType.ORE] == 3
    assert state.players["p1"].hand[ResourceType.ORE] == 0
    assert state.players["p2"].hand[ResourceType.ORE] == 0


# ---------------------------------------------------------------------
# Nuke Mode
# ---------------------------------------------------------------------


def test_nuke_destroys_piece_returns_supply_and_recomputes_longest_road(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    e = geometry["edges"]
    state = _make_state(geometry, num_players=2, nuke_mode=True)

    # p1 has a length-5 road (e[0..4]) and a settlement at v[6],
    # unrelated to that road.
    for edge_id in e[:5]:
        state.board.roads[edge_id] = "p1"
    state.board.buildings[v[6]] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    state.players["p1"].settlements_remaining = 4
    state.players["p1"].roads_remaining = 10
    import app.game.scoring as scoring

    scoring.recompute_longest_road(state)
    assert state.longest_road_holder == "p1"

    # p0 has a full nuke hand (>=2 of each resource).
    state.players["p0"].hand.update({r: 3 for r in ResourceType})

    events = rules_engine.validate_and_apply(
        state,
        "p0",
        PlayNukeAction(
            payload=PlayNukePayload(
                target_player_id="p1", target_vertex_id=v[6], target_edge_id=e[2]
            )
        ),
    )

    # 10 cards spent to the bank.
    for resource in ResourceType:
        assert state.players["p0"].hand[resource] == 1
        assert state.bank.resources[resource] == 19 + 2

    # Vertex cleared, piece returned to supply.
    assert v[6] not in state.board.buildings
    assert state.players["p1"].settlements_remaining == 5

    # Edge cleared, piece returned to supply.
    assert e[2] not in state.board.roads
    assert state.players["p1"].roads_remaining == 11

    # Removing e[2] splits p1's road into two length-2 segments -- below
    # the length-5 minimum -- so the title goes vacant.
    assert state.longest_road_holder is None
    assert state.players["p1"].has_longest_road is False

    nuke_event = next(evt for evt in events if evt.type.value == "NUKE_DROPPED")
    assert nuke_event.payload.actor == "p0"
    assert nuke_event.payload.target == "p1"
    assert nuke_event.payload.destroyed_vertex == v[6]
    assert nuke_event.payload.destroyed_edge == e[2]
    road_event = next(evt for evt in events if evt.type.value == "LONGEST_ROAD_CHANGED")
    assert road_event.payload.previous_holder == "p1"
    assert road_event.payload.new_holder is None


def test_nuke_rejected_without_precondition_hand(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    e = geometry["edges"]
    state = _make_state(geometry, num_players=2, nuke_mode=True)
    state.board.buildings[v[0]] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    state.board.roads[e[0]] = "p1"
    state.players["p0"].hand[ResourceType.LUMBER] = 2  # not enough of every type

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state,
            "p0",
            PlayNukeAction(
                payload=PlayNukePayload(
                    target_player_id="p1", target_vertex_id=v[0], target_edge_id=e[0]
                )
            ),
        )
    assert exc_info.value.code == "insufficient_resources"


def test_nuke_rejected_when_mode_disabled(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    e = geometry["edges"]
    state = _make_state(geometry, num_players=2, nuke_mode=False)
    state.board.buildings[v[0]] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    state.board.roads[e[0]] = "p1"
    state.players["p0"].hand.update({r: 3 for r in ResourceType})

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate_and_apply(
            state,
            "p0",
            PlayNukeAction(
                payload=PlayNukePayload(
                    target_player_id="p1", target_vertex_id=v[0], target_edge_id=e[0]
                )
            ),
        )
    assert exc_info.value.code == "nuke_disabled"


# ---------------------------------------------------------------------
# Win condition
# ---------------------------------------------------------------------


def test_win_condition_triggers_game_over(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    v = geometry["vertices"]
    e = geometry["edges"]
    state = _make_state(geometry, num_players=2, victory_points_target=3)

    # p0 already has a city (2 VP) and a road reaching v[2].
    state.board.buildings[v[0]] = VertexBuilding(player_id="p0", building_type=BuildingType.CITY)
    state.board.roads[e[1]] = "p0"  # connects v[1]-v[2]
    state.players["p0"].cities_remaining = 3
    state.players["p0"].hand.update(
        {
            ResourceType.LUMBER: 1,
            ResourceType.BRICK: 1,
            ResourceType.WOOL: 1,
            ResourceType.GRAIN: 1,
        }
    )
    import app.game.scoring as scoring

    scoring.recompute_victory_points(state)
    assert state.players["p0"].victory_points == 2

    events = rules_engine.validate_and_apply(
        state, "p0", BuildSettlementAction(payload=BuildSettlementPayload(vertex_id=v[2]))
    )

    assert state.players["p0"].victory_points == 3
    assert state.phase == Phase.GAME_OVER
    game_over_event = next(evt for evt in events if evt.type.value == "GAME_OVER")
    assert game_over_event.payload.winner == "p0"
    assert game_over_event.payload.final_scores["p0"] == 3


def test_actions_rejected_once_game_is_over(monkeypatch):
    geometry = _make_chain(0, STANDARD_TERRAIN_TOKENS)
    _patch_geometry(monkeypatch, geometry)
    state = _make_state(geometry, num_players=2)
    state.phase = Phase.GAME_OVER

    with pytest.raises(RuleViolation) as exc_info:
        rules_engine.validate(state, "p0", RollDiceAction())
    assert exc_info.value.code == "illegal_phase"
