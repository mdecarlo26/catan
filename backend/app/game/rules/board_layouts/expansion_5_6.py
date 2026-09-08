"""Official 5-6 player expansion board: 30 hexes, 4 rings.

Terrain ratios are scaled up from the standard 19-hex board (roughly
30/19x each terrain count, rounded to keep the total at 30) rather than
hand-copied from the physical expansion set, per the plan's "scaling
terrain ratios, number tokens, and port count proportionally from one
parametric algorithm + lookup table" approach. `special_build_phase`
(the other official 5-6p rule) lives in the rules engine, not here --
this module only owns the board shape.
"""

from app.game.board import Terrain
from app.game.board_generator import LayoutSpec, register_layout

EXPANSION_5_6_LAYOUT = LayoutSpec(
    layout_name="expansion_5_6",
    player_count_bucket="5-6p",
    hex_count=30,
    port_count=11,
    terrain_counts={
        Terrain.FOREST: 6,
        Terrain.PASTURE: 6,
        Terrain.FIELDS: 6,
        Terrain.HILLS: 5,
        Terrain.MOUNTAINS: 5,
        Terrain.DESERT: 2,
    },
    description="Official-style 5-6 player expansion board: 4 rings, 30 hexes, 11 ports.",
)

register_layout(EXPANSION_5_6_LAYOUT, default_for_bucket=True)
