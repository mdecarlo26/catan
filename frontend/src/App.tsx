import { Outlet } from "react-router-dom";
import { useGameStore } from "./state/gameStore";
import styles from "./App.module.css";

/**
 * Root layout mounted by the router (see app/router.tsx). Keeps a
 * persistent connection-status indicator across routes; each route
 * under app/ renders into <Outlet />.
 *
 * `.main` fills the remaining viewport height below the slim header via
 * flex, so a route like Game (which needs the full remaining space for
 * its own internal grid) can size itself with `height: 100%` rather than
 * fighting an unconstrained document flow.
 */
export default function App() {
  const connectionStatus = useGameStore((state) => state.connectionStatus);

  return (
    <div className={styles.shell}>
      <header className={styles.header}>
        <strong>Catan</strong>
        <span className={styles.status}>{connectionStatus}</span>
      </header>
      <main className={styles.main}>
        <Outlet />
      </main>
    </div>
  );
}
