"""Generalized 7-8 player board: ~42-46 hexes, 5 rings.

There's no official physical set for 7-8 players, so this extends the
same ring pattern the 5-6p expansion uses one ring further and scales
terrain/port counts proportionally, per the plan's "generalized
board_generator that extends the official 5-6p expansion's ring
pattern" description.
"""

from app.game.board import Terrain
from app.game.board_generator import LayoutSpec, register_layout

EXTENDED_7_8_LAYOUT = LayoutSpec(
    layout_name="extended_7_8",
    player_count_bucket="7-8p",
    hex_count=44,
    port_count=13,
    terrain_counts={
        Terrain.FOREST: 9,
        Terrain.PASTURE: 9,
        Terrain.FIELDS: 9,
        Terrain.HILLS: 7,
        Terrain.MOUNTAINS: 7,
        Terrain.DESERT: 3,
    },
    description="Generalized 7-8 player board: 5 rings, 44 hexes, 13 ports.",
)

register_layout(EXTENDED_7_8_LAYOUT, default_for_bucket=True)
