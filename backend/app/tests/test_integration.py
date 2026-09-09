"""Full end-to-end integration test: REST room creation, 4 real WS
clients, JOIN_ROOM handshake, host settings + START_GAME, driving the
real `rules_engine` through SETUP and a few MAIN-phase turns purely over
the wire, plus a mid-game disconnect/reconnect.

Uses FastAPI's `TestClient` (Starlette under the hood), which runs the
real ASGI app -- `app.main.app` -- in-process. Board-placement choices
are computed by reading the *live* server-side `GameState` directly via
`app.core.registry.room_manager` (legitimate in an in-process test: it's
the same objects `app.api.websocket` mutates) using the real board
geometry helpers in `app.game.board`, so this exercises the real,
merged board generator/geometry -- no monkeypatching. Every actual game
action, however, goes over the wire as a real WS message, through the
real `rules_engine` dispatch in `app.api.websocket`.
"""

from __future__ import annotations

from starlette.testclient import TestClient

from app.core.registry import connection_manager, room_manager
from app.game.board import edge_endpoints, get_adjacent_edges, get_adjacent_vertices, vertex_neighbors
from app.game.board import Board, Terrain
from app.game.state import Phase
from app.main import app


# ---------------------------------------------------------------------
# Board-geometry helpers for driving SETUP placement (mirrors the same
# logic app.game.rules_engine uses internally, built on the real,
# public app.game.board geometry functions).
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


def _pick_affordable_road_edge(board: Board, player_id: str):
    """Any free edge connected to `player_id`'s existing road/building
    network, for a post-setup BUILD_ROAD.
    """
    owned_vertices = {v for v, b in board.buildings.items() if b.player_id == player_id}
    owned_road_vertices: set = set()
    for edge_id, owner in board.roads.items():
        if owner == player_id:
            v1, v2 = edge_endpoints(edge_id)
            owned_road_vertices.add(v1)
            owned_road_vertices.add(v2)

    candidate_vertices = owned_vertices | owned_road_vertices
    seen_edges = set()
    for v in sorted(candidate_vertices):
        for hex_coord in v:
            for edge_id in get_adjacent_edges(hex_coord):
                if edge_id in seen_edges or edge_id in board.roads:
                    continue
                seen_edges.add(edge_id)
                if v in edge_endpoints(edge_id):
                    return edge_id
    raise AssertionError(f"no free connected edge for {player_id}")


# ---------------------------------------------------------------------
# WS message helpers
# ---------------------------------------------------------------------


def _drain_until(ws, type_name: str, limit: int = 25) -> list[dict]:
    """Receive messages until (and including) one whose `type` matches
    `type_name`. Returns every message seen along the way.
    """
    messages = []
    for _ in range(limit):
        msg = ws.receive_json()
        messages.append(msg)
        if msg["type"] == type_name:
            return messages
    raise AssertionError(f"never saw a {type_name!r} message; got: {messages}")


def _types(messages: list[dict]) -> list[str]:
    return [m["type"] for m in messages]


# ---------------------------------------------------------------------
# The test
# ---------------------------------------------------------------------


def test_full_game_over_websockets_with_reconnect():
    client = TestClient(app)

    # -- REST: create the room, seating the host -----------------------
    resp = client.post("/api/rooms", json={"nickname": "Alice"})
    assert resp.status_code == 201
    session = resp.json()
    room_code = session["room_code"]
    host_id = session["player_id"]
    host_token = session["token"]

    with client.websocket_connect(f"/ws/{room_code}?token={host_token}") as host_ws:
        # Reconnect-path catch-up (the host's very first connection):
        # private ROOM_STATE, then a PLAYER_RECONNECTED broadcast to
        # themselves (the only connected socket so far).
        catch_up = host_ws.receive_json()
        assert catch_up["type"] == "ROOM_STATE"
        assert catch_up["payload"]["phase"] == "lobby"
        reconnected = host_ws.receive_json()
        assert reconnected["type"] == "PLAYER_RECONNECTED"
        assert reconnected["payload"]["player_id"] == host_id

        # -- 3 guests join fresh (no token yet) -------------------------
        guest_nicknames = ["Bob", "Carol", "Dave"]
        guest_sessions: dict[str, dict] = {}
        guest_ws_ctxs = []
        guest_ws = []

        for nickname in guest_nicknames:
            ctx = client.websocket_connect(f"/ws/{room_code}")
            ws = ctx.__enter__()
            guest_ws_ctxs.append(ctx)
            guest_ws.append(ws)

            ws.send_json({"type": "JOIN_ROOM", "payload": {"nickname": nickname}})

            established = ws.receive_json()
            assert established["type"] == "SESSION_ESTABLISHED"
            assert established["payload"]["room_code"] == room_code
            guest_sessions[nickname] = established["payload"]

            joined_self = ws.receive_json()
            assert joined_self["type"] == "PLAYER_JOINED"
            room_state_self = ws.receive_json()
            assert room_state_self["type"] == "ROOM_STATE"

            # Every previously-connected client (host + earlier guests)
            # sees the same PLAYER_JOINED + ROOM_STATE broadcast, but
            # not the new player's private SESSION_ESTABLISHED.
            for other in [host_ws, *guest_ws[:-1]]:
                joined = other.receive_json()
                assert joined["type"] == "PLAYER_JOINED"
                assert joined["payload"]["player"]["nickname"] == nickname
                room_state = other.receive_json()
                assert room_state["type"] == "ROOM_STATE"

        bob_id = guest_sessions["Bob"]["player_id"]
        carol_id = guest_sessions["Carol"]["player_id"]
        dave_id = guest_sessions["Dave"]["player_id"]
        all_ws = [host_ws, *guest_ws]
        player_ids = [host_id, bob_id, carol_id, dave_id]

        room = room_manager.get(room_code)
        assert room is not None
        assert len(room.seats) == 4

        # -- host updates settings (fixed board for determinism) -------
        current_settings = room.settings.model_dump(mode="json")
        current_settings["board_layout"] = "fixed"
        current_settings["turn_timer_seconds"] = 0  # disable the stalled-turn timer for this test
        host_ws.send_json(
            {"type": "UPDATE_SETTINGS", "payload": {"settings": current_settings}}
        )
        for ws in all_ws:
            updated = ws.receive_json()
            assert updated["type"] == "SETTINGS_UPDATED"
            assert updated["payload"]["settings"]["board_layout"] == "fixed"

        # A non-host trying to start the game must be rejected.
        guest_ws[0].send_json({"type": "START_GAME", "payload": {}})
        rejected = guest_ws[0].receive_json()
        assert rejected["type"] == "ERROR"
        assert rejected["payload"]["code"] == "not_host"

        # -- host starts the game ---------------------------------------
        host_ws.send_json({"type": "START_GAME", "payload": {}})
        for ws in all_ws:
            started = ws.receive_json()
            assert started["type"] == "GAME_STARTED"
            assert set(started["payload"]["turn_order"]) == set(player_ids)
            snapshot = ws.receive_json()
            assert snapshot["type"] == "STATE_SNAPSHOT"
            assert snapshot["payload"]["state"]["phase"] == "setup"

        state = room.game_state
        assert state is not None
        assert state.phase == Phase.SETUP
        turn_order = list(state.turn_order)
        ws_by_player = dict(zip(player_ids, all_ws))

        # -- drive the full SETUP phase (2 settlements + 2 roads/player) -
        draft_order = turn_order + list(reversed(turn_order))
        for pid in draft_order:
            ws = ws_by_player[pid]

            vertex_id = _pick_setup_vertex(state.board)
            ws.send_json(
                {
                    "type": "BUILD_SETTLEMENT",
                    "payload": {"vertex_id": [list(h) for h in vertex_id]},
                }
            )
            for other in all_ws:
                _drain_until(other, "STATE_SNAPSHOT")
            assert vertex_id in state.board.buildings
            assert state.board.buildings[vertex_id].player_id == pid

            edge_id = _pick_edge_for_vertex(state.board, vertex_id)
            ws.send_json(
                {
                    "type": "BUILD_ROAD",
                    "payload": {"edge_id": [list(edge_id[0]), list(edge_id[1])]},
                }
            )
            for other in all_ws:
                _drain_until(other, "STATE_SNAPSHOT")
            assert state.board.roads.get(edge_id) == pid

        assert state.phase == Phase.ROLL
        assert state.current_player_index == 0

        # Deterministically top up everyone's hand with enough brick +
        # lumber to afford a road during the MAIN-phase loop below,
        # rather than depending on which resource hexes a "fixed" board
        # happens to have placed next to each player's setup vertices
        # (a real concern -- direct state mutation here is test
        # scaffolding, not something exercised over the wire; every
        # actual game action below still goes through a real WS message
        # and the real rules_engine).
        from app.game.players import ResourceType

        for pid in turn_order:
            hand = state.players[pid].hand
            hand[ResourceType.BRICK] = hand.get(ResourceType.BRICK, 0) + 2
            hand[ResourceType.LUMBER] = hand.get(ResourceType.LUMBER, 0) + 2

        # -- a few MAIN-phase turns: roll, build, end turn ---------------
        import app.game.rules_engine as rules_engine_module

        original_randint = rules_engine_module.random.randint

        def _fixed_randint(a, b):
            # Force every die to 3 (total 6) so ROLL_DICE always produces
            # resources and never triggers the discard/robber branch --
            # keeping this test focused on the MAIN-phase build/end-turn
            # loop rather than the (already covered elsewhere) robber
            # flow.
            return 3

        rules_engine_module.random.randint = _fixed_randint
        try:
            for _ in range(4):
                current_pid = turn_order[state.current_player_index]
                ws = ws_by_player[current_pid]

                ws.send_json({"type": "ROLL_DICE", "payload": {}})
                round_msgs = [_types(_drain_until(other, "STATE_SNAPSHOT")) for other in all_ws]
                assert "DICE_ROLLED" in round_msgs[0]
                assert state.phase == Phase.MAIN
                assert state.last_dice_roll == (3, 3)

                edge_id = _pick_affordable_road_edge(state.board, current_pid)
                ws.send_json(
                    {
                        "type": "BUILD_ROAD",
                        "payload": {"edge_id": [list(edge_id[0]), list(edge_id[1])]},
                    }
                )
                for other in all_ws:
                    _drain_until(other, "STATE_SNAPSHOT")
                assert state.board.roads.get(edge_id) == current_pid

                ws.send_json({"type": "END_TURN", "payload": {}})
                for other in all_ws:
                    _drain_until(other, "STATE_SNAPSHOT")
                assert state.phase == Phase.ROLL
        finally:
            rules_engine_module.random.randint = original_randint

        # -- hidden info stays masked in a real, live snapshot ----------
        current_pid = turn_order[state.current_player_index]
        current_ws = ws_by_player[current_pid]
        current_ws.send_json({"type": "ROLL_DICE", "payload": {}})
        for other in all_ws:
            batch = _drain_until(other, "STATE_SNAPSHOT")
            snapshot = batch[-1]["payload"]["state"]
            viewer = snapshot["viewer_player_id"]
            for pid, pview in snapshot["players"].items():
                if pid == viewer:
                    assert pview["hand"] is not None
                else:
                    assert pview["hand"] is None
                    assert pview["dev_cards"] is None

        # -- mid-game disconnect + reconnect for Bob ---------------------
        bob_ws = ws_by_player[bob_id]
        bob_index = all_ws.index(bob_ws)
        guest_ws_ctxs[bob_index - 1].__exit__(None, None, None)  # closes bob's socket

        remaining = [ws for ws in all_ws if ws is not bob_ws]
        saw_disconnect = False
        for other in remaining:
            msgs = _drain_until(other, "PLAYER_DISCONNECTED")
            if any(m["type"] == "PLAYER_DISCONNECTED" and m["payload"]["player_id"] == bob_id for m in msgs):
                saw_disconnect = True
        assert saw_disconnect
        assert connection_manager.is_connected(bob_id) is False
        assert room.seats[bob_id].is_connected is False

        bob_hand_before = dict(state.players[bob_id].hand)

        with client.websocket_connect(f"/ws/{room_code}?token={guest_sessions['Bob']['token']}") as bob_reconnect_ws:
            catch_up = bob_reconnect_ws.receive_json()
            assert catch_up["type"] == "STATE_SNAPSHOT"
            assert catch_up["payload"]["state"]["viewer_player_id"] == bob_id
            # Reconnect resumes the same seat with the same (unmasked,
            # since it's their own) hand contents.
            resumed_hand = catch_up["payload"]["state"]["players"][bob_id]["hand"]
            assert resumed_hand is not None
            assert {k: v for k, v in resumed_hand.items() if v} == {
                k.value: v for k, v in bob_hand_before.items() if v
            }

            reconnected_evt = bob_reconnect_ws.receive_json()
            assert reconnected_evt["type"] == "PLAYER_RECONNECTED"
            assert reconnected_evt["payload"]["player_id"] == bob_id

            for other in remaining:
                evt = other.receive_json()
                assert evt["type"] == "PLAYER_RECONNECTED"
                assert evt["payload"]["player_id"] == bob_id

            assert connection_manager.is_connected(bob_id) is True
            assert room.seats[bob_id].is_connected is True

        for ctx in guest_ws_ctxs:
            try:
                ctx.__exit__(None, None, None)
            except Exception:
                pass
