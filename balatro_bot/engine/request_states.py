"""The game states in which balatrobot (the real game's API) accepts each request.

jackdaw's request bridge is more lenient: it lets a policy use or sell a card on the round summary or
in an open pack, where the real game refuses with "requires one of these states". A policy driven by a
jackdaw copy but playing the real game must only choose requests that are accepted here. Taken from
the `requires_state` of each endpoint in balatrobot v1.5.2 (src/lua/endpoints/*.lua); None: any state.
"""

from __future__ import annotations

REQUEST_STATES: dict[str, tuple[str, ...] | None] = {
    "start": ("MENU",),
    "select": ("BLIND_SELECT",),
    "skip": ("BLIND_SELECT",),
    "play": ("SELECTING_HAND",),
    "discard": ("SELECTING_HAND",),
    "cash_out": ("ROUND_EVAL",),
    "buy": ("SHOP",),
    "reroll": ("SHOP",),
    "next_round": ("SHOP",),
    "pack": ("SMODS_BOOSTER_OPENED",),
    "use": ("SELECTING_HAND", "SHOP"),
    "sell": ("SELECTING_HAND", "SHOP"),
    "rearrange": ("SELECTING_HAND", "SHOP", "SMODS_BOOSTER_OPENED"),
    "add": ("SELECTING_HAND", "SHOP", "ROUND_EVAL"),
    "set": None, "menu": None, "gamestate": None, "health": None, "screenshot": None,
}


def accepted(method: str, state: str | None) -> bool:
    """Whether the real game accepts `method` in `state` (unknown methods: assume yes)."""
    states = REQUEST_STATES.get(method)
    return states is None or state in states
