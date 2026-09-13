/**
 * DEV-ONLY MANUAL-TEST HARNESS -- NOT WIRED INTO THE REAL APP.
 *
 * Not imported anywhere in the real app (Game.tsx, main.tsx, App.tsx are
 * untouched). Exists purely so this workstream's SceneManager state
 * machine and CSS cross-fade could be sanity-checked standalone, without
 * waiting on the Phase 2 integration agent to wire it into Game.tsx.
 *
 * To manually eyeball it: temporarily render <SceneManagerHarness /> from
 * main.tsx (or any scratch route), `npm run dev`, and watch it. Revert
 * that temporary wiring afterward -- it should never ship. The integration
 * agent should treat this whole file as reference/scratch, not part of
 * the real component surface (it is intentionally NOT exported from
 * ./index.ts).
 *
 * It cycles `phase` between "main" and "blackjack_round" on a timer and
 * renders SceneManager with:
 *   - boardAndHud: a stand-in "board" block (not the real BoardCanvas,
 *     to keep this file dependency-light) that increments a counter every
 *     second, so leaving that counter uninterrupted across minigame
 *     cross-fades is a visible proxy for "the real BoardCanvas would not
 *     have been unmounted/remounted here either".
 *   - minigameContent: CasinoTable hosting the real, unchanged
 *     BlackjackHands / BlackjackBetPanel / BlackjackToast components
 *     against small mock fixtures.
 */
import { useEffect, useState } from "react";
import type { BlackjackRoundView, Phase } from "../../types/protocol";
import { BlackjackHands } from "../Hud/BlackjackHands";
import { BlackjackBetPanel } from "../Hud/BlackjackBetPanel";
import { BlackjackToast } from "../Hud/BlackjackToast";
import { SceneManager } from "./SceneManager";
import { CasinoTable } from "./CasinoTable";

const NICKNAMES = { dealer1: "Dealer Dan", bettor1: "Alice", bettor2: "Bob" };

const MOCK_ROUND: BlackjackRoundView = {
  dealer_id: "dealer1",
  dealer_up_card: { rank: "K", suit: "spades" },
  dealer_hole_card: null,
  dealer_hole_card_revealed: false,
  dealer_hand: [],
  status: "bettor_turn",
  responses_pending: ["bettor2"],
  bettor_queue: ["bettor1", "bettor2"],
  participants: {
    bettor1: {
      stake: { kind: "resources", resources: { lumber: 2, brick: 1 } },
      hand: [
        { rank: "7", suit: "hearts" },
        { rank: "9", suit: "clubs" },
      ],
      status: "playing",
    },
    bettor2: {
      stake: {
        kind: "road",
        edge_id: [
          [0, 0],
          [1, 0],
        ],
      },
      hand: [
        { rank: "10", suit: "diamonds" },
        { rank: "8", suit: "spades" },
      ],
      status: "stood",
    },
  },
};

function StandInBoard() {
  const [tick, setTick] = useState(0);
  useEffect(() => {
    const id = window.setInterval(() => setTick((t) => t + 1), 1000);
    return () => window.clearInterval(id);
  }, []);
  return (
    <div
      style={{
        width: "100%",
        height: "100%",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "#0b3d59",
        color: "#dff3ff",
        fontFamily: "system-ui, sans-serif",
      }}
    >
      <div style={{ textAlign: "center" }}>
        <div style={{ fontSize: "1.5rem", fontWeight: 700 }}>Stand-in board</div>
        <div>
          Uninterrupted tick counter (proxy for "BoardCanvas never unmounts"): <strong>{tick}</strong>
        </div>
      </div>
    </div>
  );
}

export function SceneManagerHarness() {
  const [phase, setPhase] = useState<Phase>("main");

  useEffect(() => {
    const id = window.setInterval(() => {
      setPhase((p) => (p === "blackjack_round" ? "main" : "blackjack_round"));
    }, 5000);
    return () => window.clearInterval(id);
  }, []);

  return (
    <div style={{ width: "100vw", height: "100vh" }}>
      <div style={{ position: "fixed", top: 8, left: 8, zIndex: 100, color: "white", fontFamily: "monospace" }}>
        phase: {phase}
      </div>
      <SceneManager
        phase={phase}
        boardAndHud={<StandInBoard />}
        minigameContent={
          <CasinoTable>
            <BlackjackHands round={MOCK_ROUND} nicknames={NICKNAMES} />
            <BlackjackBetPanel
              step="idle"
              hand={{ lumber: 3, brick: 2, wool: 1, grain: 0, ore: 0 }}
              hasStakeableStructure
              pendingStructureLabel={null}
              onStartResources={() => {}}
              onStartStructure={() => {}}
              onSubmitResources={() => {}}
              onConfirmStructure={() => {}}
              onCancel={() => {}}
              onDecline={() => {}}
            />
            <BlackjackToast toasts={[{ id: 1, text: "Alice stood on 16." }]} />
          </CasinoTable>
        }
      />
    </div>
  );
}
