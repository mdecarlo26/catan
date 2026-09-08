"""In-memory session token issuance/validation.

Per the plan's "Room, Lobby & Reconnect" section: no accounts, no JWT, no
signing. A `player_id` (uuid4, minted by whoever calls `JOIN_ROOM` -- see
`app.core.room.Room.add_player`) is paired with a separate random
`session_token` the client stores (e.g. in localStorage) and presents on
`/ws/{room_code}?token=...` to reconnect. Because the whole game is
single-process and in-memory, a plain `token -> player_id` dict is
sufficient -- no cryptographic signing/verification is needed, only
enough randomness that tokens can't be guessed.

`SessionStore` is owned per-`Room` (each room has its own token
namespace), not shared globally -- see `app.core.room.Room.session`.
"""

from __future__ import annotations

import secrets

#: Number of random bytes of entropy per issued token (per the plan:
#: "a separate random session_token"). `secrets.token_urlsafe(nbytes)`
#: encodes exactly this many random bytes as a URL-safe base64 string.
TOKEN_NBYTES = 32


def generate_token(nbytes: int = TOKEN_NBYTES) -> str:
    """Return a fresh, unguessable, URL-safe session token string."""
    return secrets.token_urlsafe(nbytes)


class SessionStore:
    """A single room's `token -> player_id` map, plus its inverse for
    O(1) re-issuance/invalidation by player.

    Not thread-safe beyond what CPython's GIL gives you for free -- this
    is fine since the whole app is single-process/single-event-loop per
    the plan's persistence model.
    """

    def __init__(self) -> None:
        self._token_to_player: dict[str, str] = {}
        self._player_to_token: dict[str, str] = {}

    def issue(self, player_id: str) -> str:
        """Mint a new token for `player_id`, replacing (invalidating) any
        token previously issued to that player. Returns the new token.
        """
        self.invalidate_player(player_id)
        token = generate_token()
        self._token_to_player[token] = player_id
        self._player_to_token[player_id] = token
        return token

    def validate(self, token: str) -> str | None:
        """Return the `player_id` bound to `token`, or `None` if the
        token is unknown/invalid. Does not mutate any state -- validating
        a token does not consume or rotate it.
        """
        return self._token_to_player.get(token)

    def token_for(self, player_id: str) -> str | None:
        """Return the currently-valid token for `player_id`, if any."""
        return self._player_to_token.get(player_id)

    def invalidate_player(self, player_id: str) -> None:
        """Invalidate whatever token (if any) is currently issued to
        `player_id`. Used on kick/leave so a removed player's old token
        can never be used to rejoin the seat that no longer exists.
        """
        old_token = self._player_to_token.pop(player_id, None)
        if old_token is not None:
            self._token_to_player.pop(old_token, None)

    def __len__(self) -> int:
        return len(self._token_to_player)
