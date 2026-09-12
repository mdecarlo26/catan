import { useEffect, useMemo, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { wsClient } from "../api/wsClient";
import { loadSessionForRoom } from "../api/session";
import { bindGameStoreToWsClient, isNukeEligibleHand, useGameStore } from "../state/gameStore";
import { BoardCanvas, buildBoardGraph, legalCityVertexIds, legalRoadEdgeIds, legalSettlementVertexIds } from "../board";
import {
  BlackjackBetPanel,
  BlackjackHands,
  BlackjackToast,
  DevCardHand,
  DiceRoll,
  NukeButton,
  NukeToast,
  ResourceTray,
  TradePanel,
  TurnLog,
  VpCounter,
  RESOURCE_ICON,
  RESOURCE_LABEL,
  RESOURCE_ORDER,
} from "../components/Hud";
import type { BlackjackBetStep, BlackjackToastItem, NukeToastItem, PortAccess, TurnLogEntry } from "../components/Hud";
import type {
  BankTradePayload,
  DevCardType,
  EdgeId,
  PlayerId,
  PlayerSummary,
  PortTradePayload,
  ProposeTradePayload,
  ResourceHand,
  ResourceType,
  RespondTradePayload,
  VertexId,
  WireBoardView,
} from "../types/protocol";
import { hexKey, vertexIdKey } from "../board/hexMath";

type BuildMode = "settlement" | "road" | "city" | "road_building" | null;

/** Steps of the PLAY_NUKE target-selection flow: pick the victim, then one
 * of their settlements/cities, then one of their roads, then confirm. */
type NukeStep = "idle" | "pick_player" | "pick_vertex" | "pick_edge" | "confirm";

/** The forward-then-reverse 2N-long setup placement sequence, mirroring
 * backend/app/game/rules/setup_strategies.py's SnakeDraftSetup.draft_order. */
function draftOrder(turnOrder: readonly PlayerId[]): PlayerId[] {
  return [...turnOrder, ...[...turnOrder].reverse()];
}

interface SetupExpectation {
  playerId: PlayerId;
  action: "settlement" | "road";
}

/** Mirrors SnakeDraftSetup.get_next_setup_action -- whose turn it is to
 * place during SETUP, and whether they owe a settlement or the paired
 * road, derived purely from counts already on the board. `null` if setup
 * looks complete, rush_mode is on (real behavior TBD server-side too), or
 * the view isn't in SETUP. */
function computeSetupExpectation(
  phase: string | undefined,
  turnOrder: readonly PlayerId[] | undefined,
  board: WireBoardView | undefined,
  rushMode: boolean | undefined
): SetupExpectation | null {
  if (phase !== "setup" || !turnOrder || !board || rushMode) return null;
  const order = draftOrder(turnOrder);
  const settlementsBuilt = board.buildings.filter((b) => b.building_type === "settlement").length;
  const roadsBuilt = board.roads.length;
  if (settlementsBuilt === roadsBuilt) {
    if (settlementsBuilt >= order.length) return null;
    return { playerId: order[settlementsBuilt], action: "settlement" };
  }
  return { playerId: order[roadsBuilt], action: "road" };
}

/** Rush mode's simultaneous-setup equivalent of `computeSetupExpectation`:
 * mirrors backend/app/game/rules/setup_strategies.py's
 * `RushModeSetup.next_expected_action_for_player` -- what THIS player
 * personally owes next (settlement, road, or null once they've placed
 * their full 2+2 quota), independent of every other player's progress. */
function computeRushSetupAction(
  board: WireBoardView | undefined,
  playerId: PlayerId | null
): "settlement" | "road" | null {
  if (!board || !playerId) return null;
  const settlements = board.buildings.filter(
    (b) => b.building_type === "settlement" && b.player_id === playerId
  ).length;
  const roads = board.roads.filter((r) => r.player_id === playerId).length;
  if (settlements >= 2 && roads >= 2) return null;
  return settlements === roads ? "settlement" : "road";
}

function computePortAccess(board: WireBoardView, playerId: PlayerId): PortAccess[] {
  const owned = new Set(
    board.buildings.filter((b) => b.player_id === playerId).map((b) => vertexIdKey(b.vertex_id))
  );
  const result: PortAccess[] = [];
  for (const port of board.ports) {
    const ownsPort = port.vertices.some((v) => owned.has(vertexIdKey(v)));
    if (!ownsPort) continue;
    result.push({ port_type: port.port_type, rate: port.port_type === "generic" ? 3 : 2 });
  }
  return result;
}

/** Minimal +/- resource picker, bounded by `hand`, used for the discard UI. */
function ResourceStepper({
  hand,
  selection,
  onChange,
}: {
  hand: ResourceHand;
  selection: ResourceHand;
  onChange: (next: ResourceHand) => void;
}) {
  return (
    <div style={{ display: "flex", gap: 12 }}>
      {RESOURCE_ORDER.map((type) => {
        const owned = hand[type] ?? 0;
        const count = selection[type] ?? 0;
        return (
          <div key={type} title={RESOURCE_LABEL[type]}>
            <div>
              {RESOURCE_ICON[type]} {count}/{owned}
            </div>
            <button
              type="button"
              disabled={count <= 0}
              onClick={() => onChange({ ...selection, [type]: count - 1 })}
            >
              -
            </button>
            <button
              type="button"
              disabled={count >= owned}
              onClick={() => onChange({ ...selection, [type]: count + 1 })}
            >
              +
            </button>
          </div>
        );
      })}
    </div>
  );
}

function DiscardPanel({
  hand,
  required,
  onSubmit,
}: {
  hand: ResourceHand;
  required: number;
  onSubmit: (resources: ResourceHand) => void;
}) {
  const [selection, setSelection] = useState<ResourceHand>({});
  const total = RESOURCE_ORDER.reduce((sum, type) => sum + (selection[type] ?? 0), 0);
  return (
    <div role="alert" style={{ border: "2px solid firebrick", padding: 8, margin: "8px 0" }}>
      <p>
        You must discard {required} cards ({total}/{required} selected).
      </p>
      <ResourceStepper hand={hand} selection={selection} onChange={setSelection} />
      <button type="button" disabled={total !== required} onClick={() => onSubmit(selection)}>
        Discard
      </button>
    </div>
  );
}

/**
 * The PLAY_NUKE target-selection flow's inline panel: which sub-step it's
 * on drives which prompt/controls show. Vertex/edge picking itself
 * happens on the board (BoardCanvas, driven by the legalVertexIds/
 * legalEdgeIds computed in the component below) -- this panel is just the
 * player-pick list, running commentary, and confirm/cancel controls.
 */
function NukeFlowPanel({
  step,
  otherPlayers,
  targetPlayerId,
  onPickPlayer,
  onConfirm,
  onCancel,
}: {
  step: NukeStep;
  otherPlayers: readonly PlayerSummary[];
  targetPlayerId: PlayerId | null;
  onPickPlayer: (playerId: PlayerId) => void;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  if (step === "idle") return null;
  const targetName = targetPlayerId
    ? otherPlayers.find((p) => p.player_id === targetPlayerId)?.nickname ?? targetPlayerId
    : null;

  return (
    <div style={{ border: "2px solid #a83232", background: "#2a1414", color: "#ffe9e2", padding: 8, margin: "8px 0" }}>
      <p style={{ margin: "0 0 4px", fontWeight: 700 }}>{"\u{1F4A3}"} Drop Nuke</p>

      {step === "pick_player" && (
        <>
          <p>Choose a target player:</p>
          {otherPlayers.map((p) => (
            <button key={p.player_id} type="button" onClick={() => onPickPlayer(p.player_id)}>
              {p.nickname}
            </button>
          ))}
        </>
      )}

      {step === "pick_vertex" && <p>Click one of {targetName}'s settlements/cities on the board.</p>}

      {step === "pick_edge" && <p>Now click one of {targetName}'s roads on the board.</p>}

      {step === "confirm" && (
        <>
          <p>
            Destroy {targetName}'s selected settlement/city and road? This spends 2 of each resource (10 cards).
          </p>
          <button type="button" onClick={onConfirm}>
            Confirm Nuke
          </button>
        </>
      )}

      <button type="button" onClick={onCancel}>
        Cancel
      </button>
    </div>
  );
}

const PLAYABLE_DEV_CARDS: readonly DevCardType[] = [
  "knight",
  "road_building",
  "year_of_plenty",
  "monopoly",
];

/**
 * Main game route: mounts the real BoardCanvas + HUD driven by live
 * gameStore state, dispatches real gameplay actions via wsClient, and
 * handles the LOBBY -> SETUP -> ... -> GAME_OVER redirect chain. Also
 * carries its own stored-session reconnect fallback (mirroring Lobby.tsx)
 * for the case where this route is loaded directly (a refresh mid-game),
 * not just navigated to from Lobby.
 */
export default function Game() {
  const { roomCode = "" } = useParams<{ roomCode: string }>();
  const navigate = useNavigate();

  const view = useGameStore((state) => state.view);
  const connectionStatus = useGameStore((state) => state.connectionStatus);
  const resyncPending = useGameStore((state) => state.resyncPending);
  const storeMyPlayerId = useGameStore((state) => state.myPlayerId);
  const discardRequired = useGameStore((state) => state.discardRequired);
  const tradeOffers = useGameStore((state) => state.tradeOffers);
  const log = useGameStore((state) => state.log);
  const nukeEvent = useGameStore((state) => state.nukeEvent);
  const diceRollEvent = useGameStore((state) => state.diceRollEvent);
  const blackjackResolvedEvent = useGameStore((state) => state.blackjackResolvedEvent);

  const [buildMode, setBuildMode] = useState<BuildMode>(null);
  const [roadBuildingEdges, setRoadBuildingEdges] = useState<EdgeId[]>([]);
  const [devCardPrompt, setDevCardPrompt] = useState<"monopoly" | "year_of_plenty" | null>(null);
  const [monopolyChoice, setMonopolyChoice] = useState<ResourceType>("brick");
  const [yopChoice, setYopChoice] = useState<[ResourceType, ResourceType]>(["brick", "brick"]);

  const [nukeStep, setNukeStep] = useState<NukeStep>("idle");
  const [nukeTargetPlayer, setNukeTargetPlayer] = useState<PlayerId | null>(null);
  const [nukeTargetVertex, setNukeTargetVertex] = useState<VertexId | null>(null);
  const [nukeTargetEdge, setNukeTargetEdge] = useState<EdgeId | null>(null);
  const [nukeToasts, setNukeToasts] = useState<NukeToastItem[]>([]);
  const nukeToastTimers = useRef<number[]>([]);

  // Blackjack-on-7 bet-placement flow: mirrors the nuke flow's local
  // step-state-machine pattern exactly. `blackjackStructurePick` is set
  // once the bettor clicks one of their own vertices/edges on the board
  // during the "pick_structure" step (see legalVertexIds/legalEdgeIds
  // below and handleVertexClick/handleEdgeClick), then confirmed or
  // cancelled before actually sending BLACKJACK_PLACE_BET.
  const [blackjackBetStep, setBlackjackBetStep] = useState<BlackjackBetStep>("idle");
  const [blackjackStructurePick, setBlackjackStructurePick] = useState<
    { vertex_id: VertexId } | { edge_id: EdgeId } | null
  >(null);
  const [blackjackToasts, setBlackjackToasts] = useState<BlackjackToastItem[]>([]);
  const blackjackToastTimers = useRef<number[]>([]);

  useEffect(() => {
    return () => {
      nukeToastTimers.current.forEach((t) => window.clearTimeout(t));
      blackjackToastTimers.current.forEach((t) => window.clearTimeout(t));
    };
  }, []);

  // Reset the local bet-placement step once the round leaves "betting"
  // for this viewer (they responded, the round closed, or a reconnect/
  // resync landed) -- avoids a stale "pick_structure"/"confirm_structure"
  // step lingering after the round has moved on without them.
  useEffect(() => {
    if (view?.phase !== "blackjack_round" || view.blackjack_round?.status !== "betting") {
      setBlackjackBetStep("idle");
      setBlackjackStructurePick(null);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [view?.phase, view?.blackjack_round?.status, view?.blackjack_round?.responses_pending]);

  // NUKE_DROPPED fires for every player, not just the actor/victim -- pop
  // a brief toast for whoever's watching. Keyed off `nukeEvent.seq` so a
  // repeat actor/target pair (multiple nukes in one game) still re-triggers.
  useEffect(() => {
    if (!nukeEvent || !view) return;
    const actorName = view.players[nukeEvent.actor]?.nickname ?? "Someone";
    const targetName = view.players[nukeEvent.target]?.nickname ?? "an opponent";
    const pieceLabel = nukeEvent.destroyedBuildingType === "city" ? "city" : "settlement";
    const id = nukeEvent.seq;
    setNukeToasts((prev) => [...prev, { id, text: `${actorName} nuked ${targetName}'s ${pieceLabel} and a road!` }]);
    const timer = window.setTimeout(() => {
      setNukeToasts((prev) => prev.filter((toast) => toast.id !== id));
    }, 4000);
    nukeToastTimers.current.push(timer);
    // Only re-run when a genuinely new event lands, not on every `view` update.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nukeEvent?.seq]);

  // BLACKJACK_ROUND_RESOLVED fires once per round, for every player --
  // pop one toast per bettor outcome (win/loss/push), reusing NukeToast's
  // exact pattern via BlackjackToast.
  useEffect(() => {
    if (!blackjackResolvedEvent || !view) return;
    const { dealer_id, outcomes } = blackjackResolvedEvent.payload;
    const dealerName = view.players[dealer_id]?.nickname ?? "The dealer";
    const newToasts: BlackjackToastItem[] = Object.entries(outcomes).map(([playerId, outcome], i) => {
      const name = view.players[playerId]?.nickname ?? "A player";
      const verb =
        outcome.result === "win" ? "won against" : outcome.result === "loss" ? "lost to" : "pushed with";
      return { id: blackjackResolvedEvent.seq * 1000 + i, text: `${name} ${verb} ${dealerName}'s blackjack hand.` };
    });
    setBlackjackToasts((prev) => [...prev, ...newToasts]);
    const timer = window.setTimeout(() => {
      const ids = new Set(newToasts.map((t) => t.id));
      setBlackjackToasts((prev) => prev.filter((toast) => !ids.has(toast.id)));
    }, 4000);
    blackjackToastTimers.current.push(timer);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [blackjackResolvedEvent?.seq]);

  // Rush mode's auto-roll countdown is derived client-side from
  // settings.rush_roll_interval_seconds + last_dice_roll_ts (see
  // ClientGameStateView's doc comments) rather than pushed by the
  // server every second -- this just ticks a local clock to re-render
  // that derivation once a second while rush mode is active.
  const rushMode = !!view?.settings.rush_mode;
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!rushMode) return;
    const id = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => window.clearInterval(id);
  }, [rushMode]);

  useEffect(() => {
    const unbind = bindGameStoreToWsClient(wsClient);
    if (wsClient.getStatus() === "disconnected") {
      const existingSession = loadSessionForRoom(roomCode);
      if (existingSession) {
        useGameStore.getState().setMyPlayerId(existingSession.player_id);
        wsClient.connect(roomCode, existingSession.token);
      } else {
        // No connection and nothing to reconnect with -- bounce to Lobby,
        // which knows how to establish a fresh join.
        navigate(`/room/${roomCode}/lobby`);
      }
    }
    return () => unbind();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [roomCode]);

  useEffect(() => {
    if (view?.phase === "game_over") {
      navigate(`/room/${roomCode}/game-over`);
    }
  }, [view?.phase, roomCode, navigate]);

  const myId = view?.viewer_player_id ?? storeMyPlayerId ?? loadSessionForRoom(roomCode)?.player_id ?? null;
  const me = view && myId ? view.players[myId] : undefined;
  const isMyTurn = !!(view && myId && !rushMode && view.turn_order[view.current_player_index] === myId);
  const pending = view?.pending ?? null;
  // Rush mode has no "current player" -- every seated player may act at
  // any time UNLESS they personally owe a discard or are the player
  // currently assigned to move the robber (mirrors
  // rules_engine._require_rush_unblocked exactly).
  const rushBlocked = !!(
    rushMode &&
    myId &&
    view &&
    ((view.rush_pending_robber && view.rush_pending_robber.actor === myId) ||
      (view.rush_pending_discard && view.rush_pending_discard.required_counts[myId] != null))
  );
  // Gates the normal MAIN-phase action set (build/buy/play-dev-card/end
  // turn): in rush mode, any unblocked player at any time; otherwise the
  // viewer's own turn with no pending robber/discard/trade-response
  // action. Never gated while mid-flow on a separate exclusive
  // interaction (nuke targeting).
  const canAct =
    !!view &&
    view.phase === "main" &&
    nukeStep === "idle" &&
    (rushMode ? !rushBlocked : isMyTurn && !pending);
  const nukeEligible = isNukeEligibleHand(me?.hand);

  const graph = useMemo(() => (view ? buildBoardGraph(view.board) : null), [view?.board]);

  const setupExpectation = useMemo(
    () => computeSetupExpectation(view?.phase, view?.turn_order, view?.board, view?.settings.rush_mode),
    [view?.phase, view?.turn_order, view?.board, view?.settings.rush_mode]
  );
  const rushSetupAction = useMemo(
    () => (rushMode ? computeRushSetupAction(view?.board, myId) : null),
    [rushMode, view?.board, myId]
  );
  // Unified "what does the viewer personally owe during SETUP right now"
  // -- snake draft's single global next-placer in normal mode, or this
  // player's own independent progress in rush mode (see
  // computeRushSetupAction).
  const mySetupAction: "settlement" | "road" | null = rushMode
    ? rushSetupAction
    : setupExpectation && myId && setupExpectation.playerId === myId
      ? setupExpectation.action
      : null;
  const isMySetupTurn = mySetupAction != null;

  let legalVertexIds: readonly VertexId[] | null = null;
  let legalEdgeIds: readonly EdgeId[] | null = null;
  if (view && graph && myId) {
    if (view.phase === "setup" && isMySetupTurn) {
      if (mySetupAction === "settlement") {
        legalVertexIds = legalSettlementVertexIds(graph, view.board, myId, false);
      } else {
        legalEdgeIds = legalRoadEdgeIds(graph, view.board, myId);
      }
    } else if (view.phase === "main" && (rushMode ? !rushBlocked : isMyTurn) && buildMode) {
      if (buildMode === "settlement") {
        legalVertexIds = legalSettlementVertexIds(graph, view.board, myId, true);
      } else if (buildMode === "city") {
        legalVertexIds = legalCityVertexIds(view.board, myId);
      } else if (buildMode === "road" || buildMode === "road_building") {
        legalEdgeIds = legalRoadEdgeIds(graph, view.board, myId);
      }
    } else if (view.phase === "main" && (rushMode ? !rushBlocked : isMyTurn) && nukeTargetPlayer) {
      // Nuke target-selection: highlight only the target player's own
      // settlements/cities (pick_vertex step) or roads (pick_edge step),
      // reusing the same legalVertexIds/legalEdgeIds highlighting
      // BoardCanvas already uses for normal build-mode selection.
      if (nukeStep === "pick_vertex") {
        legalVertexIds = view.board.buildings
          .filter((b) => b.player_id === nukeTargetPlayer)
          .map((b) => b.vertex_id);
      } else if (nukeStep === "pick_edge") {
        legalEdgeIds = view.board.roads
          .filter((r) => r.player_id === nukeTargetPlayer)
          .map((r) => r.edge_id);
      }
    } else if (view.phase === "blackjack_round" && blackjackBetStep === "pick_structure") {
      // Blackjack structure-bet picking: unlike nuke's victim selection,
      // the bettor stakes one of their OWN pieces -- either kind may be
      // clicked, so both vertex and edge highlighting are shown at once
      // (BoardCanvas treats them as fully independent props).
      legalVertexIds = view.board.buildings.filter((b) => b.player_id === myId).map((b) => b.vertex_id);
      legalEdgeIds = view.board.roads.filter((r) => r.player_id === myId).map((r) => r.edge_id);
    }
  }

  function startNuke() {
    setBuildMode(null);
    setRoadBuildingEdges([]);
    setDevCardPrompt(null);
    setNukeStep("pick_player");
  }

  function cancelNuke() {
    setNukeStep("idle");
    setNukeTargetPlayer(null);
    setNukeTargetVertex(null);
    setNukeTargetEdge(null);
  }

  function pickNukeTargetPlayer(playerId: PlayerId) {
    setNukeTargetPlayer(playerId);
    setNukeStep("pick_vertex");
  }

  function confirmNuke() {
    if (!nukeTargetPlayer || !nukeTargetVertex || !nukeTargetEdge) return;
    wsClient.send({
      type: "PLAY_NUKE",
      payload: {
        target_player_id: nukeTargetPlayer,
        target_vertex_id: nukeTargetVertex,
        target_edge_id: nukeTargetEdge,
      },
    });
    cancelNuke();
  }

  // Blackjack-on-7 bet-placement flow (mirrors startNuke/cancelNuke/
  // confirmNuke's shape exactly).
  function startBlackjackResourceBet() {
    setBlackjackBetStep("resources");
  }

  function startBlackjackStructureBet() {
    setBlackjackStructurePick(null);
    setBlackjackBetStep("pick_structure");
  }

  function cancelBlackjackBet() {
    setBlackjackBetStep("idle");
    setBlackjackStructurePick(null);
  }

  function submitBlackjackResourceBet(resources: ResourceHand) {
    wsClient.send({ type: "BLACKJACK_PLACE_BET", payload: { resources } });
    cancelBlackjackBet();
  }

  function confirmBlackjackStructureBet() {
    if (!blackjackStructurePick) return;
    wsClient.send({ type: "BLACKJACK_PLACE_BET", payload: blackjackStructurePick });
    cancelBlackjackBet();
  }

  function declineBlackjack() {
    wsClient.send({ type: "BLACKJACK_DECLINE", payload: {} });
    cancelBlackjackBet();
  }

  function handleVertexClick(vertexId: VertexId) {
    if (!view || !myId) return;
    if (nukeStep === "pick_vertex") {
      setNukeTargetVertex(vertexId);
      setNukeStep("pick_edge");
      return;
    }
    if (view.phase === "blackjack_round" && blackjackBetStep === "pick_structure") {
      setBlackjackStructurePick({ vertex_id: vertexId });
      setBlackjackBetStep("confirm_structure");
      return;
    }
    if (view.phase === "setup" && isMySetupTurn && mySetupAction === "settlement") {
      wsClient.send({ type: "BUILD_SETTLEMENT", payload: { vertex_id: vertexId } });
      return;
    }
    if (view.phase === "main" && buildMode === "settlement") {
      wsClient.send({ type: "BUILD_SETTLEMENT", payload: { vertex_id: vertexId } });
      setBuildMode(null);
    } else if (view.phase === "main" && buildMode === "city") {
      wsClient.send({ type: "BUILD_CITY", payload: { vertex_id: vertexId } });
      setBuildMode(null);
    }
  }

  function handleEdgeClick(edgeId: EdgeId) {
    if (!view || !myId) return;
    if (nukeStep === "pick_edge") {
      setNukeTargetEdge(edgeId);
      setNukeStep("confirm");
      return;
    }
    if (view.phase === "blackjack_round" && blackjackBetStep === "pick_structure") {
      setBlackjackStructurePick({ edge_id: edgeId });
      setBlackjackBetStep("confirm_structure");
      return;
    }
    if (view.phase === "setup" && isMySetupTurn && mySetupAction === "road") {
      wsClient.send({ type: "BUILD_ROAD", payload: { edge_id: edgeId } });
      return;
    }
    if (view.phase === "main" && buildMode === "road") {
      wsClient.send({ type: "BUILD_ROAD", payload: { edge_id: edgeId } });
      setBuildMode(null);
    } else if (view.phase === "main" && buildMode === "road_building") {
      const next = [...roadBuildingEdges, edgeId];
      if (next.length >= 2) {
        wsClient.send({
          type: "PLAY_DEV_CARD",
          payload: { card_type: "road_building", road_building_edges: next },
        });
        setBuildMode(null);
        setRoadBuildingEdges([]);
      } else {
        setRoadBuildingEdges(next);
      }
    }
  }

  function confirmSingleRoadBuilding() {
    if (roadBuildingEdges.length !== 1) return;
    wsClient.send({
      type: "PLAY_DEV_CARD",
      payload: { card_type: "road_building", road_building_edges: roadBuildingEdges },
    });
    setBuildMode(null);
    setRoadBuildingEdges([]);
  }

  function handlePlayDevCard(cardType: DevCardType) {
    if (cardType === "knight") {
      wsClient.send({ type: "PLAY_DEV_CARD", payload: { card_type: "knight" } });
    } else if (cardType === "road_building") {
      setBuildMode("road_building");
      setRoadBuildingEdges([]);
    } else if (cardType === "monopoly") {
      setDevCardPrompt("monopoly");
    } else if (cardType === "year_of_plenty") {
      setDevCardPrompt("year_of_plenty");
    }
  }

  const otherPlayers: PlayerSummary[] = view
    ? Object.values(view.players)
        .filter((p) => p.player_id !== myId)
        .map((p) => ({
          player_id: p.player_id,
          nickname: p.nickname,
          seat: p.seat,
          is_connected: p.is_connected,
          is_host: false,
        }))
    : [];

  const turnLogEntries: TurnLogEntry[] = log.map((entry) => ({
    id: String(entry.id),
    timestamp: entry.ts * 1000,
    message: entry.text,
  }));

  // Live "next auto-roll in Ns" countdown, derived from
  // settings.rush_roll_interval_seconds + last_dice_roll_ts and ticked by
  // `nowMs` (see the effect above). null outside rush mode, or before
  // the server has primed last_dice_roll_ts (shouldn't normally happen
  // once Phase.MAIN begins -- see RushModeSetup.on_setup_complete).
  const rushRollCountdownSeconds =
    rushMode && view && view.last_dice_roll_ts != null
      ? Math.max(0, Math.ceil(view.settings.rush_roll_interval_seconds - (nowMs / 1000 - view.last_dice_roll_ts)))
      : null;

  // Unified discard/robber-move/steal derivations: read from the
  // rush-specific fields in rush mode, or from `pending` otherwise --
  // same underlying shapes (AwaitingDiscard / AwaitingRobberPlacement /
  // AwaitingSteal), just a different source field per
  // ClientGameStateView's doc comments. `rushRobberPending` is pulled
  // into its own local so TypeScript can narrow its `.kind` cleanly
  // below, instead of narrowing through a chain of optional-chained
  // property accesses.
  const rushRobberPending = rushMode ? view?.rush_pending_robber ?? null : null;
  const myDiscardOwed: number | null =
    myId == null
      ? null
      : rushMode
        ? view?.rush_pending_discard?.required_counts[myId] ?? null
        : pending?.kind === "awaiting_discard"
          ? pending.required_counts[myId] ?? null
          : null;
  const myRobberMovePending: boolean = rushMode
    ? !!rushRobberPending && rushRobberPending.kind === "awaiting_robber_placement" && rushRobberPending.actor === myId
    : pending?.kind === "awaiting_robber_placement" && pending.actor === myId;
  const myStealPending = rushMode
    ? rushRobberPending && rushRobberPending.kind === "awaiting_steal" && rushRobberPending.actor === myId
      ? rushRobberPending
      : null
    : pending && pending.kind === "awaiting_steal" && pending.actor === myId
      ? pending
      : null;
  // Who (if anyone) is currently assigned to handle the robber in rush
  // mode -- shown to everyone, without blocking anyone else's UI.
  const rushRobberAssigneeId = rushRobberPending?.actor ?? null;

  // Blackjack-on-7 (settings.blackjack_mode): derive the viewer's own
  // eligibility/turn state from the masked round view, mirroring the
  // discard/robber-move derivations above.
  const blackjackRound = view?.blackjack_round ?? null;
  const myBlackjackEligible = !!(
    myId &&
    blackjackRound &&
    blackjackRound.status === "betting" &&
    blackjackRound.responses_pending.includes(myId)
  );
  const myBlackjackTurn = !!(
    myId &&
    blackjackRound &&
    blackjackRound.status === "bettor_turn" &&
    blackjackRound.bettor_queue[0] === myId
  );
  const myStakeableVertices = view && myId ? view.board.buildings.filter((b) => b.player_id === myId) : [];
  const myStakeableEdges = view && myId ? view.board.roads.filter((r) => r.player_id === myId) : [];
  const hasStakeableStructure = myStakeableVertices.length > 0 || myStakeableEdges.length > 0;
  const blackjackNicknames: Record<PlayerId, string> = view
    ? Object.fromEntries(Object.values(view.players).map((p) => [p.player_id, p.nickname]))
    : {};
  const pendingStructureLabel =
    view && blackjackStructurePick
      ? "vertex_id" in blackjackStructurePick
        ? `your ${
            view.board.buildings.find((b) => vertexIdKey(b.vertex_id) === vertexIdKey(blackjackStructurePick.vertex_id))
              ?.building_type ?? "settlement"
          }`
        : "your road"
      : null;

  if (!view) {
    return (
      <div>
        <h1>Game</h1>
        <p>Room code: {roomCode}</p>
        <p>Connection: {connectionStatus}</p>
        {connectionStatus === "reconnecting" ? <p role="status">Reconnecting...</p> : null}
        <p>Waiting for game state...</p>
      </div>
    );
  }

  const currentPlayerId = view.turn_order[view.current_player_index];
  const currentPlayerName = view.players[currentPlayerId]?.nickname ?? currentPlayerId;

  return (
    <div style={{ display: "flex", flexWrap: "wrap", gap: 16 }}>
      <NukeToast toasts={nukeToasts} />
      <BlackjackToast toasts={blackjackToasts} />
      <div>
        <h1>Game</h1>
        <p>Room code: {roomCode}</p>
        <p>Connection: {connectionStatus}</p>
        {connectionStatus === "reconnecting" ? <p role="status">Reconnecting...</p> : null}
        {resyncPending ? <p role="status">Resyncing...</p> : null}
        <p>
          Phase: {view.phase}
          {rushMode
            ? " -- no turns (rush mode)"
            : ` -- ${isMyTurn ? "Your turn" : `${currentPlayerName}'s turn`}`}
        </p>
        {rushMode && rushRollCountdownSeconds != null && (
          <p>Next auto-roll in {rushRollCountdownSeconds}s</p>
        )}
        {rushMode && rushRobberAssigneeId && (
          <p>
            {view.players[rushRobberAssigneeId]?.nickname ?? rushRobberAssigneeId} is handling the
            robber{myRobberMovePending ? " (you)" : ""}.
          </p>
        )}
        {(view.last_dice_roll || diceRollEvent) && (
          <DiceRoll
            die1={diceRollEvent?.die1 ?? view.last_dice_roll?.[0] ?? null}
            die2={diceRollEvent?.die2 ?? view.last_dice_roll?.[1] ?? null}
            rollSeq={diceRollEvent?.seq ?? null}
          />
        )}

        <BoardCanvas
          board={view.board}
          legalVertexIds={legalVertexIds}
          legalEdgeIds={legalEdgeIds}
          onVertexClick={handleVertexClick}
          onEdgeClick={handleEdgeClick}
        />

        {view.phase === "setup" && (
          <p>
            {isMySetupTurn
              ? `Place your ${mySetupAction === "settlement" ? "settlement" : "road"}.`
              : rushMode
                ? "Setup complete -- waiting for other players to finish placing."
                : setupExpectation
                  ? `Waiting for ${view.players[setupExpectation.playerId]?.nickname ?? setupExpectation.playerId} to place.`
                  : "Setup complete."}
          </p>
        )}

        {!rushMode && view.phase === "roll" && isMyTurn && !pending && (
          <button type="button" onClick={() => wsClient.send({ type: "ROLL_DICE", payload: {} })}>
            Roll Dice
          </button>
        )}

        {myId && myDiscardOwed != null && (
          <DiscardPanel
            hand={me?.hand ?? {}}
            required={myDiscardOwed}
            onSubmit={(resources) => wsClient.send({ type: "DISCARD_CARDS", payload: { resources } })}
          />
        )}

        {myRobberMovePending && (
          <div style={{ border: "2px solid orange", padding: 8, margin: "8px 0" }}>
            <p>Move the robber:</p>
            {view.board.hexes
              .filter((h) => h.terrain !== "sea" && hexKey(h.coord) !== hexKey(view.board.robber_hex))
              .map((h) => (
                <button
                  key={hexKey(h.coord)}
                  type="button"
                  onClick={() => wsClient.send({ type: "MOVE_ROBBER", payload: { hex: h.coord } })}
                >
                  ({h.coord[0]}, {h.coord[1]}) {h.terrain}
                </button>
              ))}
          </div>
        )}

        {myStealPending && (
          <div style={{ border: "2px solid orange", padding: 8, margin: "8px 0" }}>
            <p>Steal from:</p>
            {myStealPending.candidate_targets.map((targetId) => (
              <button
                key={targetId}
                type="button"
                onClick={() => wsClient.send({ type: "STEAL_RESOURCE", payload: { target_player_id: targetId } })}
              >
                {view.players[targetId]?.nickname ?? targetId}
              </button>
            ))}
          </div>
        )}

        <NukeFlowPanel
          step={nukeStep}
          otherPlayers={otherPlayers}
          targetPlayerId={nukeTargetPlayer}
          onPickPlayer={pickNukeTargetPlayer}
          onConfirm={confirmNuke}
          onCancel={cancelNuke}
        />

        {blackjackRound && (
          <>
            <BlackjackHands round={blackjackRound} nicknames={blackjackNicknames} />

            {myBlackjackEligible && (
              <BlackjackBetPanel
                step={blackjackBetStep}
                hand={me?.hand ?? {}}
                hasStakeableStructure={hasStakeableStructure}
                pendingStructureLabel={pendingStructureLabel}
                onStartResources={startBlackjackResourceBet}
                onStartStructure={startBlackjackStructureBet}
                onSubmitResources={submitBlackjackResourceBet}
                onConfirmStructure={confirmBlackjackStructureBet}
                onCancel={cancelBlackjackBet}
                onDecline={declineBlackjack}
              />
            )}

            {myBlackjackTurn && (
              <div style={{ border: "2px solid #2f7a3a", padding: 8, margin: "8px 0" }}>
                <p>Your blackjack turn: hit or stand?</p>
                <button type="button" onClick={() => wsClient.send({ type: "BLACKJACK_HIT", payload: {} })}>
                  Hit
                </button>
                <button type="button" onClick={() => wsClient.send({ type: "BLACKJACK_STAND", payload: {} })}>
                  Stand
                </button>
              </div>
            )}
          </>
        )}

        {canAct && (
          <div style={{ margin: "8px 0" }}>
            <button type="button" disabled={buildMode === "settlement"} onClick={() => setBuildMode("settlement")}>
              Build Settlement
            </button>
            <button type="button" disabled={buildMode === "road"} onClick={() => setBuildMode("road")}>
              Build Road
            </button>
            <button type="button" disabled={buildMode === "city"} onClick={() => setBuildMode("city")}>
              Build City
            </button>
            <button type="button" onClick={() => wsClient.send({ type: "BUY_DEV_CARD", payload: {} })}>
              Buy Dev Card
            </button>
            {buildMode && (
              <button type="button" onClick={() => { setBuildMode(null); setRoadBuildingEdges([]); }}>
                Cancel
              </button>
            )}
            {buildMode === "road_building" && roadBuildingEdges.length === 1 && (
              <button type="button" onClick={confirmSingleRoadBuilding}>
                Build just this 1 road
              </button>
            )}
            {/* Rush mode has no turns to end -- END_TURN is rejected
                outright server-side (see rules_engine._validate_end_turn),
                so the control is simply not shown. */}
            {!rushMode && (
              <button
                type="button"
                onClick={() => {
                  setBuildMode(null);
                  wsClient.send({ type: "END_TURN", payload: {} });
                }}
              >
                End Turn
              </button>
            )}
          </div>
        )}

        {devCardPrompt === "monopoly" && (
          <div style={{ border: "2px solid purple", padding: 8, margin: "8px 0" }}>
            <select value={monopolyChoice} onChange={(e) => setMonopolyChoice(e.target.value as ResourceType)}>
              {RESOURCE_ORDER.map((r) => (
                <option key={r} value={r}>
                  {RESOURCE_LABEL[r]}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => {
                wsClient.send({
                  type: "PLAY_DEV_CARD",
                  payload: { card_type: "monopoly", monopoly_resource: monopolyChoice },
                });
                setDevCardPrompt(null);
              }}
            >
              Play Monopoly
            </button>
            <button type="button" onClick={() => setDevCardPrompt(null)}>
              Cancel
            </button>
          </div>
        )}

        {devCardPrompt === "year_of_plenty" && (
          <div style={{ border: "2px solid purple", padding: 8, margin: "8px 0" }}>
            <select value={yopChoice[0]} onChange={(e) => setYopChoice([e.target.value as ResourceType, yopChoice[1]])}>
              {RESOURCE_ORDER.map((r) => (
                <option key={r} value={r}>
                  {RESOURCE_LABEL[r]}
                </option>
              ))}
            </select>
            <select value={yopChoice[1]} onChange={(e) => setYopChoice([yopChoice[0], e.target.value as ResourceType])}>
              {RESOURCE_ORDER.map((r) => (
                <option key={r} value={r}>
                  {RESOURCE_LABEL[r]}
                </option>
              ))}
            </select>
            <button
              type="button"
              onClick={() => {
                wsClient.send({
                  type: "PLAY_DEV_CARD",
                  payload: { card_type: "year_of_plenty", year_of_plenty_resources: yopChoice },
                });
                setDevCardPrompt(null);
              }}
            >
              Play Year of Plenty
            </button>
            <button type="button" onClick={() => setDevCardPrompt(null)}>
              Cancel
            </button>
          </div>
        )}
      </div>

      <div style={{ minWidth: 280 }}>
        <VpCounter
          victoryPoints={me?.victory_points ?? 0}
          targetVictoryPoints={view.settings.victory_points_target}
          hasLongestRoad={me?.has_longest_road}
          hasLargestArmy={me?.has_largest_army}
        />
        <ResourceTray hand={me?.hand ?? {}} />
        {view.settings.nuke_mode && nukeEligible && nukeStep === "idle" && (
          <div style={{ margin: "8px 0" }}>
            <NukeButton
              disabled={!canAct}
              title={
                canAct
                  ? "Destroy an opponent's settlement/city and one of their roads (costs 2 of each resource)."
                  : "You can drop a nuke only during your own main-phase turn."
              }
              onClick={startNuke}
            />
          </div>
        )}
        <DevCardHand
          cards={me?.dev_cards ?? {}}
          playableCardTypes={canAct ? PLAYABLE_DEV_CARDS : []}
          onPlay={canAct ? handlePlayDevCard : undefined}
        />
        {myId && (
          <TradePanel
            hand={me?.hand ?? {}}
            otherPlayers={otherPlayers}
            ports={computePortAccess(view.board, myId)}
            pendingTrades={Object.values(tradeOffers)}
            onProposeBankTrade={(payload: BankTradePayload) => wsClient.send({ type: "BANK_TRADE", payload })}
            onProposePortTrade={(payload: PortTradePayload) => wsClient.send({ type: "PORT_TRADE", payload })}
            onProposePlayerTrade={(payload: ProposeTradePayload) => wsClient.send({ type: "PROPOSE_TRADE", payload })}
            onRespondTrade={(payload: RespondTradePayload) => wsClient.send({ type: "RESPOND_TRADE", payload })}
          />
        )}
        <TurnLog entries={turnLogEntries} />
      </div>
    </div>
  );
}
