import { createBrowserRouter } from "react-router-dom";
import App from "../App";
import CreateRoom from "./CreateRoom";
import Game from "./Game";
import GameOver from "./GameOver";
import Home from "./Home";
import Lobby from "./Lobby";

export const router = createBrowserRouter([
  {
    path: "/",
    element: <App />,
    children: [
      { index: true, element: <Home /> },
      { path: "create", element: <CreateRoom /> },
      { path: "room/:roomCode/lobby", element: <Lobby /> },
      { path: "room/:roomCode/game", element: <Game /> },
      { path: "room/:roomCode/game-over", element: <GameOver /> },
    ],
  },
]);
