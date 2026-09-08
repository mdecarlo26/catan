"""Standard 3-4 player board: the classic 19-hex, 3-ring Catan layout.

Registered as the default layout for the "3-4p" player-count bucket.
Also the shape `two_player.py` reuses as-is (see that module).
"""

from app.game.board import Terrain
from app.game.board_generator import LayoutSpec, register_layout

#: Classic terrain composition: 4 forest, 4 pasture, 4 fields, 3 hills,
#: 3 mountains, 1 desert -- 19 hexes total.
STANDARD_LAYOUT = LayoutSpec(
    layout_name="standard",
    player_count_bucket="3-4p",
    hex_count=19,
    port_count=9,
    terrain_counts={
        Terrain.FOREST: 4,
        Terrain.PASTURE: 4,
        Terrain.FIELDS: 4,
        Terrain.HILLS: 3,
        Terrain.MOUNTAINS: 3,
        Terrain.DESERT: 1,
    },
    description="Classic 3-4 player board: 3 rings, 19 hexes, 9 ports.",
)

register_layout(STANDARD_LAYOUT, default_for_bucket=True)
