"""Registry of named board layouts, keyed by (player_count_bucket,
layout_name) -- see `app.game.board_generator.LAYOUT_REGISTRY`.

Each sibling module here defines one `LayoutSpec` and registers it via
`app.game.board_generator.register_layout` at import time. Importing
this package (as `app.game.board_generator` does, at the bottom of that
file) is what populates the registry -- new layouts are added by
dropping in another module here and importing it below, without
changing the generator's core algorithm or its public interface.
"""

from app.game.rules.board_layouts import (  # noqa: F401
    expansion_5_6,
    extended_7_8,
    standard,
    two_player,
)
