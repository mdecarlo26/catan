/**
 * Minimal placeholder root component so `npm run build` succeeds on the
 * empty repo skeleton. Real routing (Home, CreateRoom, Lobby, Game,
 * GameOver) is implemented under src/app/ by a later agent -- see
 * ARCHITECTURE.md's "Repo Structure" and AGENT_BUILD_PROMPTS.md's
 * "Frontend App Shell & WS Client" task.
 */
export default function App() {
  return (
    <div>
      <h1>Catan</h1>
      <p>Scaffold placeholder -- routes not wired up yet.</p>
    </div>
  );
}
