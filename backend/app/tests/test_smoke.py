"""Smoke test: every module under app.game and app.protocol must import
cleanly and be free of syntax/definition errors.

Intentionally dynamic (walks the package tree at test time) rather than
hardcoding a module list, so it automatically covers new modules as
Wave 1 agents add rules_engine.py, board_generator.py, and friends,
without this test needing to be updated.
"""

import importlib
import pkgutil
from types import ModuleType

import app.game
import app.protocol


def _import_all_submodules(package: ModuleType) -> list[str]:
    imported = [package.__name__]
    for module_info in pkgutil.walk_packages(
        package.__path__, prefix=f"{package.__name__}."
    ):
        module = importlib.import_module(module_info.name)
        imported.append(module.__name__)
    return imported


def test_game_package_imports_cleanly():
    imported = _import_all_submodules(app.game)
    for expected in (
        "app.game.board",
        "app.game.players",
        "app.game.state",
        "app.game.actions",
        "app.game.settings_schema",
    ):
        assert expected in imported


def test_protocol_package_imports_cleanly():
    imported = _import_all_submodules(app.protocol)
    assert "app.protocol.events" in imported


def test_core_contract_types_are_constructible():
    """A minimal sanity check that the frozen models actually validate
    real data, not just import -- catches typos in field types that a
    bare import wouldn't.
    """
    from app.game.board import Board, HexCoord, HexTile, Terrain
    from app.game.players import PlayerState, ResourceType
    from app.game.settings_schema import GameSettings, SETTINGS_REGISTRY
    from app.game.state import Bank, GameState, Phase

    settings = GameSettings()
    assert settings.player_count == 4
    assert {f.key for f in SETTINGS_REGISTRY} == set(
        GameSettings.model_fields.keys()
    )

    hex_coord = HexCoord(q=0, r=0)
    board = Board(
        hexes={hex_coord: HexTile(coord=hex_coord, terrain=Terrain.DESERT)},
        ports=[],
        robber_hex=hex_coord,
    )
    assert board.hexes[hex_coord].terrain == Terrain.DESERT

    player = PlayerState(player_id="p1", nickname="Alice", seat=0)
    assert player.hand[ResourceType.BRICK] == 0

    state = GameState(
        room_code="ABCD",
        settings=settings,
        board=board,
        bank=Bank(resources={r: 19 for r in ResourceType}),
        players={"p1": player},
    )
    assert state.phase == Phase.LOBBY
    assert state.pending is None
