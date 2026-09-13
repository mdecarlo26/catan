"""Live-flow integration test for `settings.blackjack_mode`: real REST room
creation, 2 real WS clients, a full SETUP phase, a forced 7 roll, the
robber move resolving, a blackjack round being offered/bet/played, and
play returning to Phase.MAIN -- all driven over the wire through the real
`app.api.websocket` handler and `app.game.rules_engine`, mirroring
`test_integration.py`'s methodology (FastAPI `TestClient`, in-process,
reading server-side `GameState` directly to pick legal moves and to
assert on things not worth re-deriving from wire snapshots).
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.core.registry import room_manager
from app.game.board import Board, Terrain, edge_endpoints, get_adjacent_edges, get_adjacent_vertices, vertex_neighbors
from app.game.players import ResourceType
from app.game.state import Phase
from app.main import app


# ---------------------------------------------------------------------
# Board-geometry helpers for driving SETUP placement (duplicated from
# test_integration.py rather than imported, per this test package's
# self-contained-per-file convention).
# ---------------------------------------------------------------------


def _all_land_vertices(board: Board) -> set:
    verts = set()
    for coord, tile in board.hexes.items():
        if tile.terrain == Terrain.SEA:
            continue
        for candidate in get_adjacent_vertices(coord):
            trimmed = tuple(sorted(c for c in candidate if c in board.hexes))
            if len(trimmed) >= 2:
                verts.add(trimmed)
    return verts


def _pick_setup_vertex(board: Board):
    for v in sorted(_all_land_vertices(board)):
        if v in board.buildings:
            continue
        if any(n in board.buildings for n in vertex_neighbors(v)):
            continue
        return v
    raise AssertionError("no free setup vertex left on this board")


def _pick_edge_for_vertex(board: Board, vertex_id):
    candidates = set()
    for hex_coord in vertex_id:
        candidates.update(get_adjacent_edges(hex_coord))
    for edge_id in sorted(candidates):
        if vertex_id in edge_endpoints(edge_id) and edge_id not in board.roads:
            return edge_id
    raise AssertionError(f"no free edge incident to {vertex_id}")


def _drain_until(ws, type_name: str, limit: int = 40) -> list[dict]:
    messages = []
    for _ in range(limit):
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == type_name:
            return messages
    raise AssertionError(f"never saw a {type_name!r} message; got: {messages}")


def _types(messages: list[dict]) -> list[str]:
    return [m["type"] for m in messages]


def test_seven_roll_robber_then_blackjack_round_back_to_main():
    client = TestClient(app)

    resp = client.post("/api/rooms", json={"nickname": "Host"})
    assert resp.status_code == 201
    session = resp.json()
    room_code = session["room_code"]
    host_id = session["player_id"]
    host_token = session["token"]

    with client.websocket_connect(f"/ws/{room_code}?token={host_token}") as host_ws:
        _drain_until(host_ws, "PLAYER_RECONNECTED")

        with client.websocket_connect(f"/ws/{room_code}") as guest_ws:
            guest_ws.send_json({"type": "JOIN_ROOM", "payload": {"nickname": "Guest"}})
            established = guest_ws.receive_json()
            assert established["type"] == "SESSION_ESTABLISHED"
            guest_id = established["payload"]["player_id"]
            guest_ws.receive_json()  # PLAYER_JOINED (self)
            guest_ws.receive_json()  # ROOM_STATE (self)
            _drain_until(host_ws, "ROOM_STATE")

            all_ws = [host_ws, guest_ws]
            player_ids = [host_id, guest_id]

            room = room_manager.get(room_code)
            assert room is not None

            # Fixed board for determinism; blackjack_mode on; disable the
            # stalled-turn timer so it never fires mid-test (its own
            # BLACKJACK_ROUND handling is covered directly in
            # test_blackjack.py, not needed here); discard_limit high so
            # the forced 7 never routes through ROBBER_DISCARD, keeping
            # this test focused on the robber -> blackjack hand-off.
            # player_count is pinned to the 2 real humans actually seated
            # here -- otherwise START_GAME's bot auto-fill (see
            # app.api.websocket._handle_start_game) would seat 2 bots to
            # reach the default player_count of 4, which this test's
            # exact-2-player assertions (e.g. a single eligible bettor)
            # don't expect.
            current_settings = room.settings.model_dump(mode="json")
            current_settings["board_layout"] = "fixed"
            current_settings["blackjack_mode"] = True
            current_settings["turn_timer_seconds"] = 0
            current_settings["discard_limit"] = 50
            current_settings["player_count"] = 2
            host_ws.send_json({"type": "UPDATE_SETTINGS", "payload": {"settings": current_settings}})
            for ws in all_ws:
                updated = ws.receive_json()
                assert updated["type"] == "SETTINGS_UPDATED"

            host_ws.send_json({"type": "START_GAME", "payload": {}})
            for ws in all_ws:
                started = ws.receive_json()
                assert started["type"] == "GAME_STARTED"
                snapshot = ws.receive_json()
                assert snapshot["type"] == "STATE_SNAPSHOT"

            state = room.game_state
            assert state is not None
            assert state.settings.blackjack_mode is True
            turn_order = list(state.turn_order)
            ws_by_player = dict(zip(player_ids, all_ws))

            # -- drive SETUP (2 settlements + 2 roads per player) --------
            draft_order = turn_order + list(reversed(turn_order))
            for pid in draft_order:
                ws = ws_by_player[pid]
                vertex_id = _pick_setup_vertex(state.board)
                ws.send_json(
                    {"type": "BUILD_SETTLEMENT", "payload": {"vertex_id": [list(h) for h in vertex_id]}}
                )
                for other in all_ws:
                    _drain_until(other, "STATE_SNAPSHOT")

                edge_id = _pick_edge_for_vertex(state.board, vertex_id)
                ws.send_json(
                    {"type": "BUILD_ROAD", "payload": {"edge_id": [list(edge_id[0]), list(edge_id[1])]}}
                )
                for other in all_ws:
                    _drain_until(other, "STATE_SNAPSHOT")

            assert state.phase == Phase.ROLL
            roller_id = turn_order[state.current_player_index]
            roller_ws = ws_by_player[roller_id]
            other_id = guest_id if roller_id == host_id else host_id
            other_ws = ws_by_player[other_id]

            # Guarantee the eventual bettor (whoever isn't rolling) holds
            # a resource to bet -- test scaffolding, not a real action,
            # mirroring test_integration.py's identical "top up hands"
            # direct-state-mutation trick.
            state.players[other_id].hand[ResourceType.LUMBER] = 3

            # -- force a 7 roll -------------------------------------------
            import app.game.rules_engine as rules_engine_module

            original_randint = rules_engine_module.random.randint
            rolls = iter([3, 4])  # 3 + 4 = 7

            def _seven_randint(a, b):
                return next(rolls, 4)

            rules_engine_module.random.randint = _seven_randint
            try:
                roller_ws.send_json({"type": "ROLL_DICE", "payload": {}})
                for ws in all_ws:
                    batch = _drain_until(ws, "STATE_SNAPSHOT")
                assert "DICE_ROLLED" in _types(batch)
            finally:
                rules_engine_module.random.randint = original_randint

            assert state.last_dice_roll == (3, 4)
            assert state.phase == Phase.ROBBER_MOVE
            assert state.pending is not None and state.pending.kind == "awaiting_robber_placement"
            assert state.pending.actor == roller_id

            # -- move the robber to any other non-sea hex ------------------
            target_hex = next(
                coord
                for coord, tile in state.board.hexes.items()
                if coord != state.board.robber_hex and tile.terrain.value != "sea"
            )
            roller_ws.send_json({"type": "MOVE_ROBBER", "payload": {"hex": list(target_hex)}})
            for ws in all_ws:
                _drain_until(ws, "STATE_SNAPSHOT")

            # A multi-candidate steal (unlikely with only 2 players/2
            # settlements each, but handled for robustness) must resolve
            # before blackjack can open.
            if state.pending is not None and state.pending.kind == "awaiting_steal":
                victim = state.pending.candidate_targets[0]
                roller_ws.send_json({"type": "STEAL_RESOURCE", "payload": {"target_player_id": victim}})
                for ws in all_ws:
                    _drain_until(ws, "STATE_SNAPSHOT")

            # -- blackjack round should now be open for betting -----------
            assert state.phase == Phase.BLACKJACK_ROUND
            assert state.blackjack_round is not None
            assert state.blackjack_round.dealer_id == roller_id
            assert state.blackjack_round.responses_pending == [other_id]

            # No cards have even been dealt yet (nobody's bet), so the
            # dealer's hole card is trivially still hidden.
            assert state.blackjack_round.dealer_hole_card_revealed is False

            # -- the eligible bettor bets 2 lumber -------------------------
            other_ws.send_json(
                {"type": "BLACKJACK_PLACE_BET", "payload": {"resources": {"lumber": 2}}}
            )
            bet_batch = []
            for ws in all_ws:
                bet_batch = _drain_until(ws, "STATE_SNAPSHOT")
            assert "BLACKJACK_BET_PLACED" in _types(bet_batch)

            assert state.blackjack_round is not None
            assert state.blackjack_round.status == "bettor_turn"
            assert state.blackjack_round.bettor_queue == [other_id]
            assert len(state.blackjack_round.dealer_hand) == 2
            assert len(state.blackjack_round.participants[other_id].hand) == 2

            # Dealer's hole card is still hidden from every recipient's
            # own masked snapshot at this point.
            snapshot_state = bet_batch[-1]["payload"]["state"]
            assert snapshot_state["blackjack_round"]["dealer_hole_card"] is None
            assert snapshot_state["blackjack_round"]["dealer_hole_card_revealed"] is False
            assert snapshot_state["blackjack_round"]["dealer_up_card"] is not None

            # -- bettor stands; dealer auto-plays and the round resolves --
            other_ws.send_json({"type": "BLACKJACK_STAND", "payload": {}})
            resolve_batch = []
            for ws in all_ws:
                resolve_batch = _drain_until(ws, "STATE_SNAPSHOT")
            resolve_types = _types(resolve_batch)
            assert "BLACKJACK_HAND_UPDATED" in resolve_types
            assert "BLACKJACK_DEALER_REVEALED" in resolve_types
            assert "BLACKJACK_ROUND_RESOLVED" in resolve_types

            assert state.phase == Phase.MAIN
            assert state.blackjack_round is None

            final_snapshot = resolve_batch[-1]["payload"]["state"]
            assert final_snapshot["blackjack_round"] is None
            assert final_snapshot["phase"] == "main"
