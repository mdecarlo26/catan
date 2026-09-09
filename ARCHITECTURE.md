# Self-Hosted Catan Web Game (colonist.io-style)

## Context

The user wants to build and self-host a Settlers of Catan web game for friends, modeled on colonist.io's look/feel and frictionless room-based flow. Development happens on Windows; the game will run on a separate Linux machine. This is a from-scratch, greenfield build.

Confirmed scope: normal Catan rules as the baseline, 2–8 players (standard Catan officially only supports 3–4, extended to 5–6 via the official expansion; 2p and 7–8p require house-ruled defaults, addressed below), no user accounts (room code + nickname joining, like colonist.io), in-memory game state (acceptable to lose an in-progress game on server restart), and reconnect support so a dropped player can rejoin their seat. Host-configurable settings include rush mode (behavior intentionally left for later refinement), random/fixed board layout, victory point target, a custom "nuke mode" house rule, special build phase for 5+ players, and a discard-limit toggle.

## Tech Stack

- **Backend**: Python + FastAPI, WebSockets for realtime gameplay, REST only for room creation.
- **Frontend**: React + Vite, board rendered with PixiJS on HTML5 Canvas.
- **Deployment**: Docker Compose on the Linux host; a `docker-compose.dev.yml` runs the same containers locally on Windows via Docker Desktop.
- **Persistence**: in-memory only. No database.

## Repo Structure

```
catan_game/
  backend/
    app/
      main.py                        # FastAPI app, CORS, mounts REST + WS routers
      config.py                      # env-driven settings (origins, host/port, room TTL)
      api/
        rooms.py                     # POST /api/rooms -> create room, returns code + host token
        websocket.py                 # WS endpoint /ws/{room_code}?token=...
      core/
        room.py                      # Room: lifecycle, seats, settings, broadcast
        room_manager.py              # room_code -> Room, code generation, TTL sweep/eviction
        session.py                   # token issuance/validation (token -> player_id map)
        connection_manager.py        # player_id <-> active WebSocket binding, reconnect swap
      game/
        state.py                     # GameState: board, players, bank, phase, pending action, log
        board.py                     # Hex/Vertex/Edge models, axial coordinate system
        board_generator.py           # parametric layout generator, registry of named layouts
        players.py                   # PlayerState: hand, dev cards, buildings left, VP, flags
        actions.py                   # action type enum + payload schemas
        rules_engine.py              # validate(action) -> apply(action) -> events; central gate
        turn_state_machine.py        # Phase enum + allowed-actions-per-phase table
        dev_cards.py                 # deck composition + effects
        scoring.py                   # VP calc, longest-road/largest-army recompute
        settings_schema.py           # GameSettings pydantic model + toggle metadata registry
        rules/
          setup_strategies.py        # SnakeDraftSetup, RushModeSetup (stub, TBD rules)
          nuke_mode.py                # nuke precondition/effect
          robber_strategies.py       # normal vs. friendly-robber
          turn_timer.py               # stalled-turn auto-skip
          board_layouts/
            standard.py               # 3-4p, 19 hex
            expansion_5_6.py          # official 5-6p layout + special build phase
            extended_7_8.py           # generalized extra-ring layout (~42-46 hex)
            two_player.py             # 2p variants (default: standard board reused)
      serialization.py               # GameState -> per-player masked JSON view (hides opponents' hands)
      protocol/
        events.py                    # pydantic discriminated-union event models (both directions)
      tests/
        test_rules_engine.py
        test_board_generator.py
        test_scoring.py
    pyproject.toml
    Dockerfile
  frontend/
    src/
      main.tsx
      app/                          # routes: Home, CreateRoom, Lobby, Game, GameOver
      api/
        wsClient.ts                 # WS wrapper: connect, reconnect/backoff, seq-gap -> resync
        session.ts                  # localStorage: {room_code, player_id, token}
      state/
        gameStore.ts                # client-mirrored GameState (zustand)
      board/
        BoardCanvas.tsx             # PixiJS mount
        HexTile.ts / VertexNode.ts / EdgeSegment.ts
        boardInteraction.ts         # click/hover -> buildable vertex/edge picking
      components/
        Lobby/                      # settings form (driven by settings registry), player list, kick, start
        Hud/                        # resource tray, dev-card hand, VP counter, trade panel, turn log
      types/
        protocol.ts                 # TS types mirroring events.py
    package.json
    vite.config.ts
    Dockerfile
  docker-compose.yml                 # backend + frontend(nginx) for the Linux host
  docker-compose.dev.yml             # bind-mounts + hot reload, runs on Windows via Docker Desktop
  .env.example
```

## Core Game State Model

- **Coordinates**: axial/cube hex coordinates for tiles; a vertex id is the sorted tuple of its 2–3 adjacent hex coords, an edge id the sorted pair of its adjacent hex coords. Backend and frontend independently derive the same ids from the same hex layout data — no float geometry matching needed.
- **`GameState`**: room code, locked `GameSettings`, `phase` + turn order + current player index, last dice roll, `board` (hexes/vertices/edges/ports), `bank` (resource counts + dev card pile), `players: dict[player_id, PlayerState]` (hand, dev cards, buildings remaining, VP, knights played, longest-road/largest-army flags, `is_connected`, seat), `longest_road_holder`, `largest_army_holder`, an explicit `pending: PendingAction | None` (e.g. `AwaitingDiscard`, `AwaitingRobberPlacement`, `AwaitingSteal`, `AwaitingTradeResponse`) so legal next-actions are explicit rather than inferred, and a bounded action log for the turn log / resync.
- **Turn/phase state machine**: `Phase` enum (`LOBBY, SETUP, ROLL, ROBBER_DISCARD, ROBBER_MOVE, MAIN, GAME_OVER`) mapped to allowed action types. `rules_engine.validate_action()` checks this table first, then rule-specific validation — centralizes illegal-action rejection instead of duplicating it per handler.
- **Serialization**: `serialization.to_client_view(state, viewer_player_id)` produces a per-recipient masked view (opponents' hands and dev cards become counts only, bank dev-card pile is a count only). This is the single choke point that prevents hidden-information leaks and should be unit-tested early.

## WebSocket Protocol

Envelope: `{type, payload, seq, ts}`. Server assigns a monotonic `seq` per room; on a detected gap (e.g. after reconnect) the client requests a full `STATE_SNAPSHOT` rather than replaying deltas. Full masked-state snapshots per action are sent (not diffs) — simple and sufficient at friend-group scale.

- **Client → Server**: `JOIN_ROOM`, `LEAVE_ROOM`, `KICK_PLAYER` (host), `UPDATE_SETTINGS` (host), `START_GAME` (host), `ROLL_DICE`, `BUILD_SETTLEMENT`, `BUILD_ROAD`, `BUILD_CITY`, `BUY_DEV_CARD`, `PLAY_DEV_CARD`, `BANK_TRADE`, `PORT_TRADE`, `PROPOSE_TRADE`, `RESPOND_TRADE`, `MOVE_ROBBER`, `STEAL_RESOURCE`, `DISCARD_CARDS`, `PLAY_NUKE`, `END_TURN`, `CHAT_MESSAGE`.
- **Server → Client**: `ROOM_STATE`, `PLAYER_JOINED/LEFT/KICKED`, `PLAYER_DISCONNECTED/RECONNECTED`, `SETTINGS_UPDATED`, `GAME_STARTED`, `STATE_SNAPSHOT`, `DICE_ROLLED`, `RESOURCES_DISTRIBUTED`, `ROBBER_MOVED`, `RESOURCE_STOLEN`, `DISCARD_REQUIRED`, `TRADE_OFFERED/RESOLVED`, `DEV_CARD_COUNT_CHANGED`, `NUKE_DROPPED`, `LONGEST_ROAD_CHANGED`, `LARGEST_ARMY_CHANGED`, `TURN_TIMER_EXPIRED`, `GAME_OVER`, `ERROR{code, message}`.

All events are pydantic discriminated-union models in `protocol/events.py`, hand-mirrored in `frontend/src/types/protocol.ts`.

## Room, Lobby & Reconnect

No accounts. On `JOIN_ROOM`, the server issues a `player_id` (uuid4) and a separate random `session_token`, both stored client-side in localStorage. A plain in-memory `token -> player_id` map on the `Room` is sufficient (no JWT/signing needed since state is single-process and in-memory). Reconnecting opens `/ws/{room_code}?token=...`; `ConnectionManager` looks up the token, rebinds the new socket to the existing `player_id`/seat, closes any stale prior socket for that seat, flips `is_connected`, and broadcasts `PLAYER_RECONNECTED`. All game state is keyed by `player_id`, never by socket.

Lobby flow: host creates room (gets code/link + host token) → players join with nickname → host adjusts settings via the settings registry (broadcast live to all lobby UIs) → host can kick → host starts (requires 2–8 players present) → settings lock, board generated per player count + layout choice, phase `LOBBY → SETUP` → game proceeds → `GAME_OVER` on VP target reached → room lingers for a post-game summary, then is evicted by a TTL sweep (also evicts empty/abandoned rooms).

**Stalled turn handling**: a configurable `turn_timer_seconds` setting auto-ends a disconnected (or simply idle) player's turn after a timeout, so one dropped player doesn't freeze the game for everyone else.

## Settings / Toggle System

`settings_schema.py` defines `GameSettings` (pydantic) plus a small metadata registry per field (`key, type, default, description`) that drives the lobby settings UI generically, keeping future toggle additions cheap. Confirmed toggles for v1:

- `player_count` (2–8)
- `victory_points_target` (default 10)
- `board_layout`: named layout choice — a **selectable registry of layouts** per player-count bucket (e.g. `standard`, `expansion_5_6`, `extended_7_8`, plus room for future alternate layouts), with `random` generating a fresh shuffled board and `fixed` using a known-good preset. This keeps 2p/7-8p board generation pluggable rather than hardcoded to one shape.
- `rush_mode` (bool) — replaces the entire turn-based MAIN-phase model with a concurrent one: no "current player" (every seated player may build/trade/buy or play dev cards at any time, gated only by their own resources/legality), dice roll automatically every `rush_roll_interval_seconds` (system-driven, not a client `ROLL_DICE` action, which is rejected outright), and initial setup placement (`RushModeSetup` in `SETUP_STRATEGIES`) is simultaneous rather than snake-drafted. A 7 still requires discards (tracked per-player in `GameState.rush_pending_discard`, same shape as normal mode's `AwaitingDiscard`) and a robber move, but the robber-mover is a single player assigned by rotating through seated, connected players (`GameState.rush_robber_turn_index`) — only that one player is blocked until they resolve it (`GameState.rush_pending_robber`), never the whole game. Always makes `special_build_phase` inapplicable regardless of its own value (rush mode has no "end of turn" to append an extra build round to).
- `nuke_mode` (bool) — see below.
- `special_build_phase` (bool, default **on** when `player_count >= 5`, user-overridable) — extra build-only round between turns, per the official 5-6p expansion, to cut down on wait time in larger games.
- `discard_limit` (int, default 7) — configurable card-count threshold that triggers discard-on-7.

Each toggle is implemented as a strategy object selected once at game start from a registry (e.g. `SETUP_STRATEGIES[rush_mode]`, `BOARD_LAYOUT_STRATEGIES[(player_count_bucket, layout_choice)]`), used by `rules_engine` — avoiding scattered `if settings.x:` conditionals through the codebase.

## Nuke Mode

Custom house rule, exposed as a `PLAY_NUKE` action available during `MAIN` phase, validated like any other action in `rules_engine` (not routed through the dev-card system since it's resource-spend, not a card):

- **Precondition**: actor holds ≥2 of each of the 5 resource types (10 cards total).
- **Payload**: `target_player_id`, `target_vertex_id` (settlement/city to destroy), `target_edge_id` (road to destroy) — both must belong to the target player.
- **Effect**: the 10 cards return to the bank; the target vertex is cleared and **the destroyed piece returns to the victim's available-to-build supply** — they lose the board position and its VP but can rebuild it elsewhere later, subject to normal distance rules; the target edge is cleared similarly. Removing a road segment can break contiguous paths, so this triggers a full `scoring.recompute_longest_road(state)` across all players (not an incremental patch) — the holder may change or become vacant. Largest army is unaffected. If the destroyed vertex held a port, that trade rate is lost until rebuilt (port access is derived live from vertex ownership, not cached). The vacated vertex re-enters the pool of legal build spots.
- Emits a distinct `NUKE_DROPPED{actor, target, destroyed_vertex, destroyed_edge}` event for a dedicated frontend animation.

## Board Layout for 2p and 7-8p

- **2 players**: default is the standard 19-hex 4-player board reused as-is (matches colonist.io's approach) — no new geometry needed; trading leans more on bank/port trades with fewer players.
- **7-8 players**: default is a generalized `board_generator` that extends the official 5-6p expansion's ring pattern (`2-4p → 3 rings/19 hex`, `5-6p → 4 rings/30 hex official`, `7-8p → 5 rings/~42-46 hex`), scaling terrain ratios, number tokens, and port count proportionally from one parametric algorithm + lookup table rather than three hand-authored boards.
- **Extensibility**: `board_layouts/` is a registry, not a single hardcoded shape per player count — additional named layouts can be added later per player-count bucket and selected via the `board_layout` setting, without changing the generator's core interface.

## Phased Build Order

1. **Phase 1 — Core engine, no networking.** Full `game/` module for the standard 3-4 player game: board model + standard layout, complete `rules_engine` (build/trade/dev cards/robber+discard+steal/longest road/largest army/win check), `turn_state_machine`. Validate with pytest scenario tests — this is the highest-bug-risk area (VP counting, longest-road recompute, resource distribution). A minimal CLI or placeholder page is enough to sanity-check transitions; no lobby/WS/Pixi yet.
2. **Phase 2 — Multiplayer core.** FastAPI skeleton, `RoomManager`/`Room`/`ConnectionManager`, session tokens, full WS protocol + `serialization.py` masked views, wiring `rules_engine` into WS handlers. Real React lobby (create/join/settings/start) and real PixiJS `BoardCanvas` with interactive placement, resource tray, dev-card hand, trade UI, turn log. Target: fully playable 3-4p game end-to-end over WS on localhost, including verified reconnect (close tab, rejoin via stored token, resume seat).
3. **Phase 3 — Scaling & toggles.** 2p and 7-8p board generation, 5-6p special build phase, full toggle registry (rush mode's real behavior once defined, nuke mode, discard limit, turn timer), lobby UI for all toggles, animation polish (nuke, robber, dice).
4. **Phase 4 — Docker deploy.** Backend Dockerfile (uvicorn), frontend multi-stage Dockerfile (Vite build served via nginx), `docker-compose.yml` for the Linux host (+ reverse proxy for WS upgrade/TLS if desired), prod CORS/WS origin checks, health-check endpoint, graceful shutdown.

## Verification

- Phase 1: `pytest backend/app/tests` covering full game scenarios (settlement/road/city placement legality, resource distribution on each dice value, robber discard/steal, dev card effects, longest-road recompute after road removal, largest army, win condition) — run via `docker-compose run backend pytest` or natively with `uv run pytest` / `python -m pytest`.
- Phase 2: manual end-to-end test — run `docker-compose -f docker-compose.dev.yml up`, open multiple browser tabs/profiles as different players, play a full 3-4p game to completion including a mid-game tab-close-and-rejoin to confirm reconnect resumes the correct seat and state.
- Phase 3: repeat the Phase 2 manual test at 2p, 6p, and 8p with each toggle enabled individually (rush mode, nuke mode, special build phase, discard limit) to confirm no regressions.
- Phase 4: `docker-compose up` on the actual Linux host, confirm WebSocket connects through any reverse proxy, and that a server restart mid-game is handled gracefully (room/game lost, but server comes back healthy for new rooms).
