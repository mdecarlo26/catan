import { Outlet } from "react-router-dom";
import { useGameStore } from "./state/gameStore";

/**
 * Root layout mounted by the router (see app/router.tsx). Keeps a
 * persistent connection-status indicator across routes; each route
 * under app/ renders into <Outlet />.
 */
export default function App() {
  const connectionStatus = useGameStore((state) => state.connectionStatus);

  return (
    <div>
      <header>
        <strong>Catan</strong>
        <span> -- {connectionStatus}</span>
      </header>
      <main>
        <Outlet />
      </main>
    </div>
  );
}
