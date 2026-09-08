/**
 * TEMPORARY, dev-only preview for BoardCanvas -- not part of the real
 * app shell (that's a later agent's job, see ARCHITECTURE.md's "Repo
 * Structure" -> frontend/src/app/). This file exists purely so
 * BoardCanvas + the mock fixture can be visually sanity-checked (hexes
 * tile with no gaps/overlaps, vertices/edges land at the right spots,
 * pieces render per-player) without wiring up real routing/state.
 *
 * Not imported by main.tsx/App.tsx -- to view it locally, temporarily
 * point main.tsx at <BoardPreview /> instead of <App />, `npm run dev`,
 * then revert main.tsx before committing (main.tsx is outside this
 * module's scope).
 */
import { useState } from "react";
import { BoardCanvas } from "./BoardCanvas";
import { MOCK_BOARD, mockLegalEdgeIds, mockLegalVertexIds } from "./mockBoard";
import type { EdgeId, VertexId } from "../types/protocol";
import { edgeIdKey, vertexIdKey } from "./hexMath";

export default function BoardPreview() {
  const [showLegal, setShowLegal] = useState(true);
  const [log, setLog] = useState<string[]>([]);

  const legalVertexIds = showLegal ? mockLegalVertexIds() : null;
  const legalEdgeIds = showLegal ? mockLegalEdgeIds() : null;

  const appendLog = (line: string) => setLog((prev) => [line, ...prev].slice(0, 8));

  const handleVertexClick = (vertexId: VertexId) => {
    appendLog(`vertex click: ${vertexIdKey(vertexId)}`);
  };
  const handleEdgeClick = (edgeId: EdgeId) => {
    appendLog(`edge click: ${edgeIdKey(edgeId)}`);
  };

  return (
    <div style={{ fontFamily: "system-ui, sans-serif", padding: 16, color: "#e8e8e8", background: "#111", minHeight: "100vh" }}>
      <h1 style={{ marginTop: 0 }}>Board Preview (dev-only)</h1>
      <p>
        Standard 19-hex mock board. Toggle the buildable highlight to confirm the legal-id-driven
        highlighting and click-through work; click a highlighted vertex/edge to see it logged below.
      </p>
      <label style={{ display: "block", marginBottom: 12 }}>
        <input type="checkbox" checked={showLegal} onChange={(e) => setShowLegal(e.target.checked)} /> show
        buildable highlight (mock legal set)
      </label>
      <BoardCanvas
        board={MOCK_BOARD}
        width={900}
        height={760}
        legalVertexIds={legalVertexIds}
        legalEdgeIds={legalEdgeIds}
        onVertexClick={handleVertexClick}
        onEdgeClick={handleEdgeClick}
      />
      <h3>Click log</h3>
      <ul>
        {log.map((line, i) => (
          <li key={i}>{line}</li>
        ))}
      </ul>
    </div>
  );
}
