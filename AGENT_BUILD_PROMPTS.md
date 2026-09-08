# Multi-Agent Build Prompts

Wave-based breakdown for building this project with a team of agents in parallel, respecting the actual dependency chain (rules engine needs board geometry signatures, integration needs everything merged first, etc.). See `ARCHITECTURE.md` for the full design these prompts implement.

## Orchestration notes

- The repo isn't a git repo yet at the start, so **Wave 0 must run first, alone, and must `git init`** — everything after it depends on isolated worktrees being possible.
- For Wave 1, launch each agent in an isolated git worktree so 6-7 agents can genuinely write files at the same time without clobbering each other. Merge each worktree branch back sequentially as agents finish (file scopes below are chosen to be disjoint, so merges should be close to conflict-free).
- Wave 2 (integration) needs the full merged picture, so it runs in the main working directory, not isolated — it's inherently more serial by nature.
- Every prompt tells the agent to read `ARCHITECTURE.md` first for full context.
- After Wave 2 lands and a real game is playable end-to-end at 3-4 players, Phase 3 (2p/7-8p boards, special build phase, rush mode's real rules, remaining toggles) and Phase 4 (real Docker validation on the Linux box) become their own parallelizable wave against a now-stable core — worth generating fresh prompts for at that point rather than upfront, since they'll need to reference whatever Wave 2 actually produced.

---

## Wave 0 — Contracts & Scaffold (1 agent, blocking, runs alone)

```
Read the plan at C:\catan_game\ARCHITECTURE.md in full before doing anything.

Set up the Catan game monorepo skeleton at C:\catan_game exactly per that plan's repo structure. Do this in order:

1. `git init` the repo (this is required so later work can happen in parallel git worktrees without collisions).
2. Create the full directory tree from the plan's "Repo Structure" section (backend/app/..., frontend/src/...), with empty __init__.py / index barrel files where needed so the tree imports cleanly.
3. Write ONLY the shared contract/type files below with COMPLETE, final type definitions and full docstrings on non-obvious fields — but NO business logic or implementations. These are the interfaces every other agent will build against in parallel, so correctness and completeness here matters more than anywhere else in the project:
   - backend/app/game/board.py — HexCoord (axial), VertexId, EdgeId types/aliases, Terrain enum, PortType enum, HexTile / Port pydantic models. Also declare (but leave as `raise NotImplementedError`) the function SIGNATURES other agents will call: `get_adjacent_vertices(hex: HexCoord) -> list[VertexId]`, `get_adjacent_edges(hex: HexCoord) -> list[EdgeId]`, `vertex_neighbors(v: VertexId) -> list[VertexId]`, `edge_endpoints(e: EdgeId) -> tuple[VertexId, VertexId]`.
   - backend/app/game/players.py — PlayerState pydantic model per the plan.
   - backend/app/game/state.py — GameState pydantic model, Phase enum, PendingAction discriminated union, per the plan.
   - backend/app/game/actions.py — action type enum + payload schema for every Client->Server action in the plan's WS protocol section.
   - backend/app/game/settings_schema.py — GameSettings pydantic model with the toggle registry (player_count, victory_points_target, board_layout, rush_mode, nuke_mode, special_build_phase, discard_limit) exactly as described in the plan.
   - backend/app/protocol/events.py — discriminated-union pydantic models for every Server->Client and Client->Server event named in the plan.
   - frontend/src/types/protocol.ts — hand-mirrored TypeScript types matching events.py field-for-field.
4. pyproject.toml (Python 3.12+, fastapi, uvicorn[standard], websockets, pydantic v2, pytest) with a passing smoke test. package.json (React, Vite, TypeScript, PixiJS, zustand) with a passing `npm run build` on the empty shell.
5. Minimal docker-compose.yml / docker-compose.dev.yml stubs (Wave 1's Docker agent will flesh these out for real — just make sure they parse).
6. Commit everything.

Do not implement game rules, board generation logic, or the actual geometry functions — signatures and types only. Report back the exact contract each downstream agent should treat as frozen.
```

---

## Wave 1 — Parallel build (6-7 agents, isolated worktrees, run simultaneously)

### 1. Board & Board Generator
```
Read C:\catan_game\ARCHITECTURE.md and backend/app/game/board.py (already scaffolded with frozen type/signature contracts — do not change its public signatures, only implement the bodies).

Implement backend/app/game/board.py's geometry functions (adjacency/derivation from axial hex coordinates) and backend/app/game/board_generator.py plus backend/app/game/rules/board_layouts/{standard,expansion_5_6,extended_7_8,two_player}.py per the plan's "Board Layout for 2p and 7-8p" section: a parametric ring-based generator driven by a lookup table (3 rings/19 hex for 2-4p, 4 rings/30 hex official for 5-6p, 5 rings/~42-46 hex for 7-8p), with board_layouts/ as an extensible registry keyed by (player_count_bucket, layout_name), not a single hardcoded shape. Write backend/app/game/tests/test_board_generator.py covering hex/vertex/edge id derivation and correct hex/port counts per player count bucket. Do not touch any file outside backend/app/game/board.py, board_generator.py, or rules/board_layouts/.
```

### 2. Rules Engine & Scoring
```
Read C:\catan_game\ARCHITECTURE.md and the frozen contracts in backend/app/game/{state,players,actions,settings_schema}.py and the board.py function SIGNATURES (board.py's own implementation is being built in parallel by another agent — code against the signatures, not the internals).

Implement backend/app/game/rules_engine.py (validate(action) -> apply(action) -> events, phase-table check first per the plan), turn_state_machine.py, dev_cards.py, scoring.py (VP calc, longest-road/largest-army recompute), and rules/{setup_strategies,nuke_mode,robber_strategies,turn_timer}.py. Cover: settlement/road/city placement legality, resource distribution per dice roll, bank/port trades, dev card buy/play effects, robber move+discard+steal, longest road/largest army tracking, win condition at victory_points_target. Implement nuke_mode.py exactly per the plan's "Nuke Mode" section (10-card cost, destroyed piece returns to victim's supply, full longest-road recompute, port loss derived live). Implement setup_strategies.py as SnakeDraftSetup (real) and RushModeSetup (explicit stub raising a clear "TBD" marker, per the plan). Write backend/app/game/tests/test_rules_engine.py and test_scoring.py with real game scenarios. Do not touch board.py's internals, protocol/events.py, or anything under backend/app/core/ or backend/app/api/.
```

### 3. Room / Session / Connection Manager
```
Read C:\catan_game\ARCHITECTURE.md and the frozen protocol/events.py and settings_schema.py contracts.

Implement backend/app/core/{room,room_manager,session,connection_manager}.py per the plan's "Room, Lobby & Reconnect" section: room code generation + collision check, seat table, host flag, in-memory token->player_id map, WebSocket rebind-on-reconnect (closing any stale prior socket for the same player_id), PLAYER_RECONNECTED broadcast, TTL-based room eviction sweep. This module should not depend on rules_engine internals — it only needs to know GameState exists as an opaque object it stores per room and broadcasts serialized views of (the serialization function itself is built in Wave 2). Do not touch backend/app/game/ or backend/app/api/.
```

### 4. Frontend App Shell & WS Client
```
Read C:\catan_game\ARCHITECTURE.md and frontend/src/types/protocol.ts (frozen contract).

Implement frontend/src/main.tsx, app/ route skeleton (Home, CreateRoom, Lobby, Game, GameOver — can be placeholder screens for now), api/wsClient.ts (connect, auto-reconnect/backoff, seq-gap detection -> request STATE_SNAPSHOT), api/session.ts (localStorage room_code/player_id/token per the plan), and state/gameStore.ts (zustand store mirroring GameState client-side per protocol.ts). Do not implement the actual board canvas or lobby/HUD visuals — those are separate agents; stub the routes they'll fill in. Do not touch frontend/src/board/ or frontend/src/components/.
```

### 5. Frontend Board Rendering
```
Read C:\catan_game\ARCHITECTURE.md and frontend/src/types/protocol.ts, and backend/app/game/board.py's coordinate-system doc comments (axial hex coords; vertex id = sorted tuple of adjacent hex coords; edge id = sorted pair of adjacent hex coords) — mirror this same derivation logic in TypeScript so frontend and backend agree on ids independently.

Implement frontend/src/board/{BoardCanvas.tsx, HexTile.ts, VertexNode.ts, EdgeSegment.ts, boardInteraction.ts} using PixiJS: render hexes/ports from a board payload shape matching protocol.ts, click/hover picking for buildable vertices/edges, basic piece rendering (settlement/city/road) per player color. Work against a hardcoded/mock board JSON fixture for now (Wave 2 wires it to live data) so this doesn't block on the backend. Do not touch frontend/src/state/ or api/.
```

### 6. Frontend Lobby & HUD Components
```
Read C:\catan_game\ARCHITECTURE.md, frontend/src/types/protocol.ts, and the settings_schema.py toggle registry shape (player_count, victory_points_target, board_layout, rush_mode, nuke_mode, special_build_phase, discard_limit).

Implement frontend/src/components/Lobby/ (settings form generically driven by the settings registry shape, player list, kick button, start button) and components/Hud/ (resource tray, dev-card hand, VP counter, trade panel, turn log). Build against mock data/props for now — Wave 2 wires these to the live gameStore. Do not touch frontend/src/board/, state/, or api/.
```

### 7. Docker / Deploy (can start immediately, no dependencies)
```
Read C:\catan_game\ARCHITECTURE.md's "Phase 4 — Docker deploy" section.

Write backend/Dockerfile (uvicorn), frontend/Dockerfile (multi-stage: vite build -> nginx), finalize docker-compose.yml (for the Linux host) and docker-compose.dev.yml (bind-mounts + hot reload, runs on Windows via Docker Desktop), .env.example, and an nginx config that proxies WebSocket upgrade headers correctly to the backend. These files don't depend on any application code being finished — just get the container/network topology right against the directory structure in the plan. Do not touch backend/app/ or frontend/src/.
```

---

## Wave 2 — Integration (2 agents, sequential-ish, runs in the merged main worktree)

### 8. Backend Integration
```
All Wave 1 backend agents (board, rules engine, room/session) have merged into main. Read their actual implementations (not just the old contracts) in backend/app/game/ and backend/app/core/.

Implement backend/app/main.py, api/rooms.py, api/websocket.py, and serialization.py (the per-player masked view per the plan — this is the single choke point for hidden info, unit-test it explicitly). Wire rules_engine.validate/apply into the WS action handlers, room_manager/connection_manager into the WS lifecycle, and reconcile any signature drift between what the board/rules-engine/room agents actually built vs. the original frozen contracts. Write end-to-end backend integration tests: full game playthrough via simulated WS messages for a 4-player game, plus a reconnect scenario.
```

### 9. Frontend-Backend Wiring
```
All Wave 1 frontend agents (shell, board, lobby/hud) have merged into main, and Backend Integration (agent 8) is done and running locally. Read the actual finalized protocol events and REST/WS endpoints.

Wire gameStore.ts to wsClient.ts against the real backend, replace the mock data feeding BoardCanvas/Lobby/Hud with live store state and dispatch real actions on user interaction, and implement the reconnect UX (detect dropped connection, auto-attempt rejoin with stored token, show reconnecting state). Then manually verify: `docker-compose -f docker-compose.dev.yml up`, open 4 browser tabs, play a full game to completion including one tab-close-and-rejoin mid-game.
```
