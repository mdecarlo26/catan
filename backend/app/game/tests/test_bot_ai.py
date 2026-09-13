"""Tests for the bot-player feature: `app.game.rules.bot_ai`'s decision
logic plus `app.game.rules_engine`'s bot-draining integration
(`drain_bot_actions` / `validate_and_apply`).

Unit-style tests (1-8 below) build a `GameState` directly against a real
generated ("fixed", for determinism) board via `app.game.board_generator`
-- unlike `test_rules_engine.py`'s monkeypatched linear-chain geometry,
the real board geometry (`app.game.board`) is fully implemented, so
there's no need to fake it here; bot placement heuristics need real
vertex/hex adjacency to be meaningful anyway.

The final test is a live-flow, full end-to-end check (mirroring
`app.tests.test_blackjack_live`/`test_integration`'s methodology: a real
FastAPI `TestClient`, one real WS client for the host, reading
server-side `GameState` directly to drive/assert) confirming bot
auto-fill on `START_GAME` and a full multi-turn game playing itself out
via bots with only the host occasionally acting.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.core.registry import room_manager
from app.game import board as board_mod
from app.game import board_generator, dev_cards, rules_engine
from app.game.actions import ActionType
from app.game.board import BuildingType, Terrain, VertexBuilding
from app.game.players import PlayerState, ResourceType
from app.game.rules import bot_ai
from app.game.settings_schema import GameSettings
from app.game.state import (
    AwaitingDiscard,
    AwaitingRobberPlacement,
    AwaitingSteal,
    AwaitingTradeResponse,
    Bank,
    BlackjackRoundState,
    GameState,
    Phase,
)
from app.main import app


# ---------------------------------------------------------------------
# Shared fixture-building helpers
# ---------------------------------------------------------------------


def _make_state(
    player_count: int = 4,
    bot_seats: tuple[int, ...] = (),
    phase: Phase = Phase.SETUP,
    dev_card_pile: list | None = None,
    **settings_kwargs,
) -> GameState:
    settings_kwargs.setdefault("player_count", player_count)
    settings_kwargs.setdefault("board_layout", "fixed")
    settings = GameSettings(**settings_kwargs)
    board = board_generator.generate_board_for_settings(settings)

    player_ids = [f"p{i}" for i in range(player_count)]
    bot_ids = {player_ids[i] for i in bot_seats}
    players = {
        pid: PlayerState(player_id=pid, nickname=pid, seat=i, is_bot=pid in bot_ids)
        for i, pid in enumerate(player_ids)
    }
    bank = Bank(
        resources={r: 19 for r in ResourceType},
        dev_card_pile=dev_card_pile if dev_card_pile is not None else [],
    )
    return GameState(
        room_code="TEST",
        settings=settings,
        phase=phase,
        turn_order=player_ids,
        board=board,
        bank=bank,
        players=players,
    )


# ---------------------------------------------------------------------
# 1. Setup placement heuristic
# ---------------------------------------------------------------------


def test_setup_placement_picks_legal_and_best_scored_spot():
    state = _make_state(player_count=4, bot_seats=(0,))

    decision = bot_ai.decide_bot_action(state)
    assert decision is not None
    actor_id, action = decision
    assert actor_id == "p0"
    assert action.type == ActionType.BUILD_SETTLEMENT

    vertex_id = action.payload.vertex_id
    board = state.board

    # Legal: free, and no neighboring vertex already has a building
    # (trivially true here -- nothing's built yet -- but assert the
    # real legality predicate anyway).
    assert bot_ai._vertex_free_and_far_enough(board, vertex_id)

    # "Best-scored among legal spots": recompute the true max independently.
    all_candidates = [
        v for v in bot_ai._all_vertices(board) if bot_ai._vertex_free_and_far_enough(board, v)
    ]
    best_score = max(bot_ai._vertex_score(board, v) for v in all_candidates)
    assert bot_ai._vertex_score(board, vertex_id) == best_score

    # Applying it through the real rules_engine path must succeed (no
    # bypassing validate()/apply() -- bot decisions are exactly as legal
    # as a human's).
    rules_engine.validate_and_apply(state, actor_id, action)
    assert board.buildings[vertex_id].player_id == "p0"


# ---------------------------------------------------------------------
# 2. MAIN-phase turn: roll, build what's affordable, end turn.
# ---------------------------------------------------------------------


def test_bot_main_turn_rolls_builds_affordable_road_and_ends_turn(monkeypatch):
    state = _make_state(player_count=4, bot_seats=(0,), phase=Phase.ROLL)
    state.current_player_index = 0

    board = state.board
    # Give p0 an existing settlement (so it has a road connection point)
    # but no roads yet.
    vertex_id = next(iter(bot_ai._all_vertices(board)))
    board.buildings[vertex_id] = VertexBuilding(player_id="p0", building_type=BuildingType.SETTLEMENT)

    p0 = state.players["p0"]
    p0.hand[ResourceType.BRICK] = 1
    p0.hand[ResourceType.LUMBER] = 1
    # Everything else stays at 0 -- exactly enough for one road, nothing
    # else (no settlement/city/dev-card funds), so the bot's build
    # priority resolves to "road, then nothing else, then end turn".

    # Avoid rolling a 7 (keep this test focused on build-then-end-turn,
    # not the robber sequence).
    monkeypatch.setattr(rules_engine.random, "randint", lambda a, b: 3)

    events = rules_engine.drain_bot_actions(state)
    assert events  # DICE_ROLLED etc. were produced

    # The bot must have built exactly one road (all it could afford) and
    # ended its turn, advancing to the next (human) player without
    # hanging anywhere.
    p0_roads = [edge_id for edge_id, owner in board.roads.items() if owner == "p0"]
    assert len(p0_roads) == 1
    assert state.phase == Phase.ROLL
    assert state.turn_order[state.current_player_index] == "p1"
    # The road cost was fully spent; nothing else was affordable, so the
    # bot ended its turn rather than hanging in MAIN forever.
    assert p0.hand[ResourceType.BRICK] == 0


# ---------------------------------------------------------------------
# 3. Discard on a 7.
# ---------------------------------------------------------------------


def test_bot_discards_reasonably_and_hands_off_to_robber_move():
    state = _make_state(player_count=4, bot_seats=(0,), phase=Phase.ROBBER_DISCARD)
    # A human (p1) is "the roller" who triggered this discard requirement
    # -- keeps this test focused on the discard step alone.
    state.current_player_index = 1

    p0 = state.players["p0"]
    p0.hand[ResourceType.BRICK] = 5
    p0.hand[ResourceType.LUMBER] = 1
    p0.hand[ResourceType.ORE] = 1
    p0.hand[ResourceType.GRAIN] = 1
    p0.hand[ResourceType.WOOL] = 1
    assert dev_cards.hand_total(p0.hand) == 9

    state.pending = AwaitingDiscard(required_counts={"p0": 4})

    events = rules_engine.drain_bot_actions(state)
    assert events

    # Most-abundant-first: brick (5) discarded down to 1, everything else
    # untouched.
    assert p0.hand[ResourceType.BRICK] == 1
    assert p0.hand[ResourceType.LUMBER] == 1
    assert p0.hand[ResourceType.ORE] == 1
    assert p0.hand[ResourceType.GRAIN] == 1
    assert p0.hand[ResourceType.WOOL] == 1
    assert dev_cards.hand_total(p0.hand) == 5

    # p0's obligation clears; the discard requirement was the only one
    # owed, so play hands off to the (human) roller's robber move.
    assert isinstance(state.pending, AwaitingRobberPlacement)
    assert state.pending.actor == "p1"
    assert state.phase == Phase.ROBBER_MOVE


# ---------------------------------------------------------------------
# 4. Robber move + steal.
# ---------------------------------------------------------------------


def test_bot_moves_robber_to_opponent_hex_and_steals_from_richest(monkeypatch):
    state = _make_state(player_count=4, bot_seats=(0,), phase=Phase.ROBBER_MOVE)
    # A different (human) player is nominally "current" here -- p0's only
    # involvement is the robber obligation itself (e.g. as if it played a
    # Knight card), keeping this test focused on just the robber
    # move/steal step rather than cascading into p0's own MAIN turn too.
    state.current_player_index = 1
    board = state.board

    candidate_hex = next(
        coord
        for coord, tile in board.hexes.items()
        if tile.terrain != Terrain.SEA
        and len(
            [
                v
                for v in board_mod.get_adjacent_vertices(coord)
                if len(tuple(sorted(c for c in v if c in board.hexes))) >= 2
            ]
        )
        >= 2
    )
    trimmed_vertices = [
        tuple(sorted(c for c in v if c in board.hexes))
        for v in board_mod.get_adjacent_vertices(candidate_hex)
    ]
    trimmed_vertices = [v for v in trimmed_vertices if len(v) >= 2]
    v_rich, v_poor = trimmed_vertices[0], trimmed_vertices[1]
    assert v_rich != v_poor

    board.buildings[v_rich] = VertexBuilding(player_id="p1", building_type=BuildingType.SETTLEMENT)
    board.buildings[v_poor] = VertexBuilding(player_id="p2", building_type=BuildingType.SETTLEMENT)
    board.robber_hex = next(c for c in board.hexes if c != candidate_hex)

    state.players["p1"].hand[ResourceType.BRICK] = 5
    state.players["p2"].hand[ResourceType.WOOL] = 2

    state.pending = AwaitingRobberPlacement(actor="p0", reason="dice_roll")

    # `v_rich`/`v_poor` are 2-3 hex vertices, so more than one hex on the
    # board may legitimately "touch an opponent" here (any hex adjacent to
    # either vertex). Make the tie-break deterministic (first candidate,
    # sorted) so this test can assert on one specific outcome rather than
    # a set of equally-legal ones -- the underlying pick-from-a-legal-pool
    # logic itself is exercised for real by every other (unpatched) test
    # in this module.
    monkeypatch.setattr(bot_ai.random, "choice", lambda seq: sorted(seq)[0])
    expected_hex = bot_ai._choose_robber_hex(state, "p0")

    events = rules_engine.drain_bot_actions(state)
    assert events

    assert board.robber_hex == expected_hex
    assert state.pending is None
    assert state.phase == Phase.MAIN

    # Whichever of p1/p2 actually touches the chosen hex was robbed; if
    # both do, the richer-handed one (p1, 5 cards) was picked.
    candidates_on_chosen_hex = bot_ai._hex_opponent_owners(board, "p0", expected_hex)
    assert candidates_on_chosen_hex  # the heuristic must have picked an opponent-adjacent hex
    expected_victim = "p1" if "p1" in candidates_on_chosen_hex else "p2"
    if len(candidates_on_chosen_hex) > 1:
        expected_victim = "p1"  # p1 (5 cards) is richer than p2 (2 cards)

    victim_hand = state.players[expected_victim].hand
    other_id = "p2" if expected_victim == "p1" else "p1"
    other_hand = state.players[other_id].hand
    starting_total = 5 if expected_victim == "p1" else 2
    assert dev_cards.hand_total(victim_hand) == starting_total - 1
    assert dev_cards.hand_total(other_hand) == (2 if other_id == "p2" else 5)
    assert dev_cards.hand_total(state.players["p0"].hand) == 1


# ---------------------------------------------------------------------
# 5. Trade auto-decline.
# ---------------------------------------------------------------------


def test_bot_auto_declines_proposed_trade_and_it_resolves():
    state = _make_state(player_count=4, bot_seats=(0,), phase=Phase.MAIN)
    state.current_player_index = 1  # p1 (human) is the current/proposing player

    state.pending = AwaitingTradeResponse(
        trade_id="trade-1",
        proposer="p1",
        offered={ResourceType.LUMBER: 2},
        requested={ResourceType.BRICK: 1},
        responses_pending=["p0"],
    )

    events = rules_engine.drain_bot_actions(state)
    assert events

    assert state.pending is None  # resolved, not left hanging


# ---------------------------------------------------------------------
# 6. Blackjack auto-decline.
# ---------------------------------------------------------------------


def test_bot_auto_declines_blackjack_round_and_it_resolves():
    state = _make_state(player_count=4, bot_seats=(0,), phase=Phase.BLACKJACK_ROUND, blackjack_mode=True)
    # Keep p0 (the declining bot) distinct from "current player" so this
    # test stays focused on the blackjack decline itself, rather than
    # cascading into p0's own MAIN turn once the round closes back out.
    state.current_player_index = 1
    state.blackjack_round = BlackjackRoundState(dealer_id="p1", responses_pending=["p0"])

    events = rules_engine.drain_bot_actions(state)
    assert events

    # Nobody bet -> the round aborts back to Phase.MAIN with no cards dealt.
    assert state.phase == Phase.MAIN
    assert state.blackjack_round is None


# ---------------------------------------------------------------------
# 7. SPECIAL_BUILD: build-if-useful then pass.
# ---------------------------------------------------------------------


def test_bot_special_build_turn_builds_then_passes():
    state = _make_state(player_count=5, bot_seats=(0,), phase=Phase.SPECIAL_BUILD)
    board = state.board

    vertex_id = next(iter(bot_ai._all_vertices(board)))
    board.buildings[vertex_id] = VertexBuilding(player_id="p0", building_type=BuildingType.SETTLEMENT)

    p0 = state.players["p0"]
    p0.hand[ResourceType.BRICK] = 1
    p0.hand[ResourceType.LUMBER] = 1

    state.special_build_queue = ["p0", "p1"]
    state.current_player_index = 4  # irrelevant to SPECIAL_BUILD's queue-driven turn

    rules_engine.drain_bot_actions(state)

    p0_roads = [edge_id for edge_id, owner in board.roads.items() if owner == "p0"]
    assert len(p0_roads) == 1
    assert state.phase == Phase.SPECIAL_BUILD  # queue not yet empty (p1 still owed a turn)
    assert state.special_build_queue == ["p1"]


# ---------------------------------------------------------------------
# 8. Explicit rush-mode inertness.
# ---------------------------------------------------------------------


def test_bot_never_acts_in_rush_mode():
    state = _make_state(player_count=4, bot_seats=(0,), phase=Phase.MAIN, rush_mode=True)
    state.current_player_index = 0  # rush mode doesn't really use this, but set for clarity

    state.rush_pending_discard = AwaitingDiscard(required_counts={"p0": 2})
    state.rush_pending_robber = AwaitingRobberPlacement(actor="p0", reason="dice_roll")
    state.players["p0"].hand[ResourceType.BRICK] = 5

    assert bot_ai.decide_bot_action(state) is None

    events = rules_engine.drain_bot_actions(state)
    assert events == []
    # Nothing at all changed -- the bot did not resolve its own pending
    # rush-mode discard or robber obligation, per the documented scope
    # limitation (not a bug).
    assert state.rush_pending_discard.required_counts == {"p0": 2}
    assert state.rush_pending_robber is not None
    assert state.rush_pending_robber.actor == "p0"
    assert state.players["p0"].hand[ResourceType.BRICK] == 5


# ---------------------------------------------------------------------
# 9. Live-flow: bot auto-fill on START_GAME + a full multi-turn game
#    driven mostly by bots, with only the host occasionally acting.
# ---------------------------------------------------------------------


def _all_land_vertices(board):
    verts = set()
    for coord, tile in board.hexes.items():
        if tile.terrain == Terrain.SEA:
            continue
        for candidate in board_mod.get_adjacent_vertices(coord):
            trimmed = tuple(sorted(c for c in candidate if c in board.hexes))
            if len(trimmed) >= 2:
                verts.add(trimmed)
    return verts


def _pick_setup_vertex(board):
    for v in sorted(_all_land_vertices(board)):
        if v in board.buildings:
            continue
        if any(n in board.buildings for n in board_mod.vertex_neighbors(v)):
            continue
        return v
    raise AssertionError("no free setup vertex left on this board")


def _pick_edge_for_vertex(board, vertex_id):
    candidates = set()
    for hex_coord in vertex_id:
        candidates.update(board_mod.get_adjacent_edges(hex_coord))
    for edge_id in sorted(candidates):
        if vertex_id in board_mod.edge_endpoints(edge_id) and edge_id not in board.roads:
            return edge_id
    raise AssertionError(f"no free edge incident to {vertex_id}")


def _pick_host_discard(hand, owed: int):
    resources = {r: 0 for r in ResourceType}
    pool = dict(hand)
    remaining = owed
    while remaining > 0:
        resource = max(pool, key=lambda r: pool.get(r, 0))
        if pool.get(resource, 0) <= 0:
            break
        pool[resource] -= 1
        resources[resource] += 1
        remaining -= 1
    return resources


def _drain_until(ws, type_name: str, limit: int = 200) -> list[dict]:
    messages = []
    for _ in range(limit):
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == type_name:
            return messages
    raise AssertionError(f"never saw a {type_name!r} message; got: {messages}")


def test_bot_auto_fill_and_full_game_playthrough_with_bots():
    client = TestClient(app)

    resp = client.post("/api/rooms", json={"nickname": "Host"})
    assert resp.status_code == 201
    session = resp.json()
    room_code = session["room_code"]
    host_id = session["player_id"]
    host_token = session["token"]

    with client.websocket_connect(f"/ws/{room_code}?token={host_token}") as host_ws:
        _drain_until(host_ws, "PLAYER_RECONNECTED")

        room = room_manager.get(room_code)
        assert room is not None
        assert len(room.seats) == 1

        # Fixed board for determinism; explicit player_count=4 (matches
        # the default, spelled out for clarity); disable the stalled-turn
        # timer so it never interferes with this test's own manual pacing.
        current_settings = room.settings.model_dump(mode="json")
        current_settings["board_layout"] = "fixed"
        current_settings["player_count"] = 4
        current_settings["turn_timer_seconds"] = 0
        host_ws.send_json({"type": "UPDATE_SETTINGS", "payload": {"settings": current_settings}})
        updated = host_ws.receive_json()
        assert updated["type"] == "SETTINGS_UPDATED"

        # -- START_GAME: only 1 human seated, settings.player_count == 4 --
        host_ws.send_json({"type": "START_GAME", "payload": {}})
        started = host_ws.receive_json()
        assert started["type"] == "GAME_STARTED"
        assert len(started["payload"]["turn_order"]) == 4
        _drain_until(host_ws, "STATE_SNAPSHOT")

        assert len(room.seats) == 4
        bot_seats = [seat for seat in room.seats.values() if seat.is_bot]
        assert len(bot_seats) == 3
        assert room.seats[host_id].is_bot is False
        for seat in bot_seats:
            # Bots always report connected -- never look "dropped" to the
            # reconnect/turn-timer machinery.
            assert seat.is_connected is True

        state = room.game_state
        assert state is not None
        for pid, player in state.players.items():
            assert player.is_bot == room.seats[pid].is_bot

        # -- drive the host's own SETUP placements (2 settlement+road
        # pairs); every other player's setup placement (12 actions across
        # 3 bots) is handled automatically by the bot-draining loop, in
        # between these 4 sends. --------------------------------------
        for _ in range(2):
            vertex_id = _pick_setup_vertex(state.board)
            host_ws.send_json(
                {"type": "BUILD_SETTLEMENT", "payload": {"vertex_id": [list(h) for h in vertex_id]}}
            )
            _drain_until(host_ws, "STATE_SNAPSHOT")
            assert state.board.buildings[vertex_id].player_id == host_id

            edge_id = _pick_edge_for_vertex(state.board, vertex_id)
            host_ws.send_json(
                {"type": "BUILD_ROAD", "payload": {"edge_id": [list(edge_id[0]), list(edge_id[1])]}}
            )
            _drain_until(host_ws, "STATE_SNAPSHOT")
            assert state.board.roads.get(edge_id) == host_id

        # SETUP fully resolved for all 4 players (all 3 bots handled
        # entirely by the drain loop) -- the game is now waiting on the
        # host to roll (per SnakeDraftSetup.on_setup_complete, the first
        # forward-round player rolls first, which is the host: seat 0).
        assert state.phase == Phase.ROLL
        assert state.turn_order[state.current_player_index] == host_id

        # -- drive many rounds, letting bots play themselves out between
        # the host's own turns; only the host ever needs to act here. ---
        host_end_turns = 0
        for _ in range(400):
            if state.phase == Phase.GAME_OVER:
                break

            if state.phase == Phase.ROBBER_DISCARD:
                assert isinstance(state.pending, AwaitingDiscard)
                owed = state.pending.required_counts.get(host_id)
                assert owed is not None, "a bot's own discard should have self-resolved already"
                resources = _pick_host_discard(state.players[host_id].hand, owed)
                host_ws.send_json(
                    {
                        "type": "DISCARD_CARDS",
                        "payload": {"resources": {r.value: a for r, a in resources.items()}},
                    }
                )
                _drain_until(host_ws, "STATE_SNAPSHOT")
                continue

            if state.phase == Phase.ROBBER_MOVE:
                pending = state.pending
                if isinstance(pending, AwaitingRobberPlacement):
                    assert pending.actor == host_id, "a bot's own robber move should have self-resolved"
                    target = next(c for c in state.board.hexes if c != state.board.robber_hex)
                    host_ws.send_json({"type": "MOVE_ROBBER", "payload": {"hex": list(target)}})
                else:
                    assert isinstance(pending, AwaitingSteal)
                    assert pending.actor == host_id
                    victim = pending.candidate_targets[0]
                    host_ws.send_json(
                        {"type": "STEAL_RESOURCE", "payload": {"target_player_id": victim}}
                    )
                _drain_until(host_ws, "STATE_SNAPSHOT")
                continue

            if state.phase == Phase.ROLL:
                assert state.turn_order[state.current_player_index] == host_id
                host_ws.send_json({"type": "ROLL_DICE", "payload": {}})
                _drain_until(host_ws, "STATE_SNAPSHOT")
                continue

            if state.phase == Phase.MAIN:
                assert state.turn_order[state.current_player_index] == host_id
                host_ws.send_json({"type": "END_TURN", "payload": {}})
                _drain_until(host_ws, "STATE_SNAPSHOT")
                host_end_turns += 1
                continue

            raise AssertionError(f"unexpected phase blocking host progress: {state.phase}")

        # Confidence check: either the game reached a natural conclusion,
        # or the human+bots played deep into the game (many full rounds,
        # each bot turn interleaved automatically) without ever stalling.
        assert state.phase == Phase.GAME_OVER or host_end_turns >= 15
