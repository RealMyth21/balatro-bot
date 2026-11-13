"""Comparing game states from two backends (real game, exact engine, jackdaw).

normalize()   drops fields that legitimately differ between processes (see comments)
rules_view()  normalize() minus localized text fields (jackdaw fills them with names)
first_diff()  path and values of the first difference, or None
deep_diff()   hidden state: RNG streams and card ability tables (engine debug view vs jackdaw Game)
"""

from __future__ import annotations

import json
import re
from typing import Any

# Card sort_id counters depend on how many objects (incl. UI) a process created, not on game logic.
IGNORE_KEYS = {"id"}

AREAS = ("jokers", "consumables", "cards", "hand", "shop", "vouchers", "packs", "pack")


def _norm(x: Any) -> Any:
    if isinstance(x, dict):
        return {k: _norm(v) for k, v in sorted(x.items()) if k not in IGNORE_KEYS}
    if isinstance(x, list):
        return [_norm(v) for v in x]
    if isinstance(x, float) and x.is_integer():
        return int(x)
    return x


def normalize(gs: dict) -> dict:
    out = _norm(gs)
    # The draw pile is re-sorted and reshuffled from the seed at every blind; between blinds it is
    # just "cards returned to the deck". Compare it as a multiset; a real ordering difference shows
    # up as a different hand on the next draw.
    deck = out.get("cards")
    if isinstance(deck, dict) and isinstance(deck.get("cards"), list):
        deck["cards"] = sorted(deck["cards"], key=lambda c: json.dumps(c, sort_keys=True))
        # Steamodded recomputes the deck's size limit from the card count; vanilla only raises it for
        # cards added straight to the deck. It feeds two deck-size unlocks, not gameplay.
        deck.pop("limit", None)
    # Booster art variants (p_buffoon_normal_1 vs _2) are picked with unseeded math.random, whose
    # state depends on how many cosmetic random calls the renderer made. Same pack.
    for area in ("packs", "pack"):
        a = out.get(area)
        if isinstance(a, dict):
            for c in a.get("cards", []) or []:
                if isinstance(c.get("key"), str) and c["key"].startswith("p_"):
                    c["key"] = re.sub(r"_\d+$", "", c["key"])
    return out


def rules_view(gs: dict) -> dict:
    """normalize() minus text: card effects, blind/tag effects, voucher descriptions."""
    out = normalize(gs)
    for area in AREAS:
        a = out.get(area)
        if isinstance(a, dict):
            for c in a.get("cards", []) or []:
                if isinstance(c.get("value"), dict):
                    c["value"].pop("effect", None)
    for b in (out.get("blinds") or {}).values():
        if isinstance(b, dict):
            b.pop("effect", None)
            b.pop("tag_effect", None)
    uv = out.get("used_vouchers")
    if isinstance(uv, dict):
        out["used_vouchers"] = sorted(uv)
    return out


def first_diff(a: Any, b: Any, labels: tuple[str, str] = ("a", "b"), path: str = "$") -> str | None:
    la, lb = labels
    if type(a) is not type(b):
        return f"{path}: {la}={a!r} {lb}={b!r}"
    if isinstance(a, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}.{k}: present only in {la if k in a else lb}"
            d = first_diff(a[k], b[k], labels, f"{path}.{k}")
            if d:
                return d
        return None
    if isinstance(a, list):
        if len(a) != len(b):
            return f"{path}: len {la}={len(a)} {lb}={len(b)}"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            d = first_diff(x, y, labels, f"{path}[{i}]")
            if d:
                return d
        return None
    return None if a == b else f"{path}: {la}={a!r} {lb}={b!r}"


def same_value(ev: Any, jv: Any) -> bool:
    """Engine (Lua) value vs jackdaw value: numbers within 1e-9 relative, absent Lua fields equal to
    jackdaw's zero/empty defaults, dicts compared on jackdaw's keys."""
    if isinstance(jv, bool) or isinstance(ev, bool):
        return bool(ev) == bool(jv) if (ev is None or jv is None) else ev == jv
    if isinstance(jv, (int, float)) and isinstance(ev, (int, float)):
        return abs(ev - jv) <= 1e-9 * max(1.0, abs(ev), abs(jv))
    if isinstance(jv, dict):
        ev = ev if isinstance(ev, dict) else {}
        return all(same_value(ev.get(str(k)), v) for k, v in jv.items() if v is not None)
    if ev is None:
        return jv in (0, 0.0, "", None) or jv == {} or jv == []
    return ev == jv


def deep_diff(engine_debug: dict, jd_game) -> str | None:
    """Hidden state: every RNG stream, and every joker/consumable/hand card's ability table.
    engine_debug is the engine's `engine_debug_state` reply; jd_game is jackdaw's Game."""
    d = jd_game.to_dict()["rng"]
    rj = dict(d["streams"])
    rj["seed"], rj["hashed_seed"] = d["seed"], d["hashed_seed"]
    re_ = engine_debug["rng"]
    if set(re_) != set(rj):
        return (f"rng streams differ: only engine {sorted(set(re_) - set(rj))[:6]}, "
                f"only jackdaw {sorted(set(rj) - set(re_))[:6]}")
    for k in sorted(re_):
        if not same_value(re_[k], rj[k]):
            return f"rng[{k}]: engine={re_[k]} jackdaw={rj[k]}"
    for area, views in (("jokers", jd_game.jokers), ("consumables", jd_game.consumables), ("hand", jd_game.hand)):
        cards = engine_debug.get(area) or []
        if len(cards) != len(views):
            return f"{area}: engine has {len(cards)} cards, jackdaw {len(views)}"
        for i, (ec, jv) in enumerate(zip(cards, views, strict=True)):
            for k, v in jv.ability.items():
                if v is None:
                    continue
                if not same_value(ec["ability"].get(k), v):
                    return f"{area}[{i}] {jv.key} ability.{k}: engine={ec['ability'].get(k)} jackdaw={v}"
            if area == "jokers" and ec.get("sell_cost") is not None and ec["sell_cost"] != jv.sell_cost:
                return f"jokers[{i}] {jv.key} sell_cost: engine={ec['sell_cost']} jackdaw={jv.sell_cost}"
    return None
