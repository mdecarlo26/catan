import { useEffect, useMemo, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { wsClient } from "../api/wsClient";
import { loadSessionForRoom } from "../api/session";
import { bindGameStoreToWsClient, useGameStore } from "../state/gameStore";
import { BoardCanvas, buildBoardGraph, legalCityVertexIds, legalRoadEdgeIds, legalSettlementVertexIds } from "../board";
import {
  DevCardHand,
  ResourceTray,
  TradePanel,
  TurnLog,
  VpCounter,
  RESOURCE_ICON,
  RESOURCE_LABEL,
  RESOURCE_ORDER,
} from "../components/Hud";
import type { PortAccess, TurnLogEntry } from "../components/Hud";
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

  const [buildMode, setBuildMode] = useState<BuildMode>(null);
  const [roadBuildingEdges, setRoadBuildingEdges] = useState<EdgeId[]>([]);
  const [devCardPrompt, setDevCardPrompt] = useState<"monopoly" | "year_of_plenty" | null>(null);
  const [monopolyChoice, setMonopolyChoice] = useState<ResourceType>("brick");
  const [yopChoice, setYopChoice] = useState<[ResourceType, ResourceType]>(["brick", "brick"]);

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
  const isMyTurn = !!(view && myId && view.turn_order[view.current_player_index] === myId);
  const pending = view?.pending ?? null;

  const graph = useMemo(() => (view ? buildBoardGraph(view.board) : null), [view?.board]);

  const setupExpectation = useMemo(
    () => computeSetupExpectation(view?.phase, view?.turn_order, view?.board, view?.settings.rush_mode),
    [view?.phase, view?.turn_order, view?.board, view?.settings.rush_mode]
  );
  const isMySetupTurn = !!(setupExpectation && myId && setupExpectation.playerId === myId);

  let legalVertexIds: readonly VertexId[] | null = null;
  let legalEdgeIds: readonly EdgeId[] | null = null;
  if (view && graph && myId) {
    if (view.phase === "setup" && isMySetupTurn) {
      if (setupExpectation!.action === "settlement") {
        legalVertexIds = legalSettlementVertexIds(graph, view.board, myId, false);
      } else {
        legalEdgeIds = legalRoadEdgeIds(graph, view.board, myId);
      }
    } else if (view.phase === "main" && isMyTurn && buildMode) {
      if (buildMode === "settlement") {
        legalVertexIds = legalSettlementVertexIds(graph, view.board, myId, true);
      } else if (buildMode === "city") {
        legalVertexIds = legalCityVertexIds(view.board, myId);
      } else if (buildMode === "road" || buildMode === "road_building") {
        legalEdgeIds = legalRoadEdgeIds(graph, view.board, myId);
      }
    }
  }

  function handleVertexClick(vertexId: VertexId) {
    if (!view || !myId) return;
    if (view.phase === "setup" && isMySetupTurn && setupExpectation!.action === "settlement") {
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
    if (view.phase === "setup" && isMySetupTurn && setupExpectation!.action === "road") {
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
      <div>
        <h1>Game</h1>
        <p>Room code: {roomCode}</p>
        <p>Connection: {connectionStatus}</p>
        {connectionStatus === "reconnecting" ? <p role="status">Reconnecting...</p> : null}
        {resyncPending ? <p role="status">Resyncing...</p> : null}
        <p>
          Phase: {view.phase} -- {isMyTurn ? "Your turn" : `${currentPlayerName}'s turn`}
        </p>
        {view.last_dice_roll ? (
          <p>Last roll: {view.last_dice_roll[0]} + {view.last_dice_roll[1]} = {view.last_dice_roll[0] + view.last_dice_roll[1]}</p>
        ) : null}

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
              ? `Place your ${setupExpectation!.action === "settlement" ? "settlement" : "road"}.`
              : setupExpectation
                ? `Waiting for ${view.players[setupExpectation.playerId]?.nickname ?? setupExpectation.playerId} to place.`
                : "Setup complete."}
          </p>
        )}

        {view.phase === "roll" && isMyTurn && !pending && (
          <button type="button" onClick={() => wsClient.send({ type: "ROLL_DICE", payload: {} })}>
            Roll Dice
          </button>
        )}

        {pending?.kind === "awaiting_discard" && myId && pending.required_counts[myId] != null && (
          <DiscardPanel
            hand={me?.hand ?? {}}
            required={pending.required_counts[myId]}
            onSubmit={(resources) => wsClient.send({ type: "DISCARD_CARDS", payload: { resources } })}
          />
        )}

        {pending?.kind === "awaiting_robber_placement" && pending.actor === myId && (
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

        {pending?.kind === "awaiting_steal" && pending.actor === myId && (
          <div style={{ border: "2px solid orange", padding: 8, margin: "8px 0" }}>
            <p>Steal from:</p>
            {pending.candidate_targets.map((targetId) => (
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

        {view.phase === "main" && isMyTurn && !pending && (
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
            <button
              type="button"
              onClick={() => {
                setBuildMode(null);
                wsClient.send({ type: "END_TURN", payload: {} });
              }}
            >
              End Turn
            </button>
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
        <DevCardHand
          cards={me?.dev_cards ?? {}}
          playableCardTypes={view.phase === "main" && isMyTurn && !pending ? PLAYABLE_DEV_CARDS : []}
          onPlay={view.phase === "main" && isMyTurn && !pending ? handlePlayDevCard : undefined}
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
