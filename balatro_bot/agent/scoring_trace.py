"""How a played hand scored, step by step, for game records (viewer and training data).

`trace_play(game, cards)` plays the cards on a copy of a jackdaw game with scoring recording on and
returns the breakdown in readable form: hand type and level, the cards played and the ones that scored,
every scoring event in order (a card adding chips, a joker adding +Mult or x-Mult, retriggers...) with
the running chips and mult, and the total. The copy has the same randomness as the game it copies, so
the trace is what the real play scores. It is written to the record after the play, for review and
training; nothing here is shown to the model.
"""

from __future__ import annotations

import json
from typing import Any

from jackdaw.core import Game

_SUIT = {"H": "♥", "D": "♦", "C": "♣", "S": "♠"}


def _card_label(c: dict) -> str:
    """'K♥' for a playing card (with its enhancement/edition/seal), the key for anything else."""
    front = c.get("front")
    if not front:
        return c.get("center") or "?"
    suit, rank = front.split("_", 1)
    rank = "10" if rank == "T" else rank
    edition = c.get("edition")
    edition = edition.get("type") if isinstance(edition, dict) else edition
    enhancement = (c.get("center") or "").replace("c_base", "").replace("m_", "")
    extra = [x for x in (enhancement, edition, c.get("seal")) if x]
    return f"{rank}{_SUIT.get(suit, suit)}" + (f" ({', '.join(str(x).lower() for x in extra)})" if extra else "")


def trace_play(game: Game, cards: list[int] | tuple[int, ...]) -> dict[str, Any] | None:
    """The scoring breakdown of playing hand positions `cards` now, or None if it can't be played."""
    core = game._g.clone()
    labels: dict[int, str] = {}
    for area in ("hand", "jokers"):
        for c in json.loads(core.view(area)):
            labels[c["sort_id"]] = c.get("center", "?") if area == "jokers" else _card_label(c)
    core.record_scoring = True
    try:
        core.step(json.dumps(["PlayHand", sorted(int(i) for i in cards)]))
    except Exception:  # noqa: BLE001  an illegal play: the game refuses it too
        return None
    raw = core.last_play_json()
    if not raw:
        return None
    t = json.loads(raw)
    events = []
    for e in t.get("events") or []:
        ev = {"kind": e.get("kind")}
        if "card" in e:
            ev["source"] = labels.get(e["card"], f"card {e['card']}")
        for k in ("chips", "mult", "x_mult", "xmult", "dollars", "level"):
            if e.get(k) not in (None, 0, 0.0) or (k == "level" and e.get(k) is not None):
                ev[k] = e[k]
        ev["running"] = [e.get("hand_chips"), e.get("hand_mult")]
        events.append(ev)
    return {"hand": t.get("hand"), "level": t.get("level"), "chips": t.get("chips"), "mult": t.get("mult"),
            "total": t.get("total"), "dollars": t.get("dollars"), "debuffed": t.get("debuffed"),
            "played": [labels.get(i, str(i)) for i in t.get("played") or []],
            "scoring": [labels.get(i, str(i)) for i in t.get("scoring") or []], "events": events}
