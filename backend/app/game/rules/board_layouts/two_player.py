"""2 player board: reuses the standard 19-hex board as-is.

Per the plan's "Board Layout for 2p and 7-8p" section: "2 players:
default is the standard 19-hex 4-player board reused as-is (matches
colonist.io's approach) -- no new geometry needed." This module doesn't
duplicate `standard.py`'s shape/terrain table; it registers the same
`LayoutSpec` under the "2p" bucket instead, so the registry stays a
genuine `(player_count_bucket, layout_name)` lookup (extensible with a
real 2p-specific alternate layout later) rather than special-casing 2p
elsewhere in the generator.
"""

from dataclasses import replace

from app.game.board_generator import register_layout
from app.game.rules.board_layouts.standard import STANDARD_LAYOUT

TWO_PLAYER_LAYOUT = replace(
    STANDARD_LAYOUT,
    layout_name="two_player",
    player_count_bucket="2p",
    description="2 player board: reuses the standard 19-hex board as-is.",
)

register_layout(TWO_PLAYER_LAYOUT, default_for_bucket=True)
