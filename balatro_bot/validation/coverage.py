"""Which game content a validation run actually exercised (from balatrobot game-state views).

Recorded per step: jokers held (with their stickers) while hands were played, boss blinds faced, tags
taken by skipping, vouchers redeemed, consumables used (directly or picked from packs), boosters
opened, and the editions / enhancements / seals present on scored or held cards. The universe of
content comes from jackdaw's generated game tables (the game's own prototypes), so a report can list
what was never exercised.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

CATEGORIES = ("jokers_in_play", "bosses", "tags", "vouchers", "consumables", "boosters",
              "editions", "enhancements", "seals", "stickers")


def new() -> dict[str, set]:
    return {c: set() for c in CATEGORIES}


def _cards(view: dict, area: str) -> list[dict]:
    return ((view or {}).get(area) or {}).get("cards") or []


def _mods(card: dict) -> dict:
    m = card.get("modifier")
    return m if isinstance(m, dict) else {}


def record(cov: dict[str, set], before: dict, method: str, params: dict | None, after: dict) -> None:
    """Update coverage with one step (the view before the action, the action, the view after)."""
    params = params or {}
    if method == "play":
        for j in _cards(before, "jokers"):
            cov["jokers_in_play"].add(j.get("key"))
            for s in ("eternal", "perishable", "rental"):
                if _mods(j).get(s):
                    cov["stickers"].add(s)
            if _mods(j).get("edition"):
                cov["editions"].add(_mods(j)["edition"])
        for c in _cards(before, "hand"):
            m = _mods(c)
            if m.get("edition"):
                cov["editions"].add(m["edition"])
            if m.get("enhancement"):
                cov["enhancements"].add(m["enhancement"])
            if m.get("seal"):
                cov["seals"].add(m["seal"])
    if (before or {}).get("state") == "SELECTING_HAND":
        for b in ((before or {}).get("blinds") or {}).values():
            if b.get("type") == "BOSS" and b.get("status") == "CURRENT":
                cov["bosses"].add(b.get("name"))
    if method == "skip":
        for b in ((before or {}).get("blinds") or {}).values():
            if b.get("status") == "SELECT" and b.get("tag_name"):
                cov["tags"].add(b["tag_name"])
    if method == "use":
        cons = _cards(before, "consumables")
        i = params.get("consumable")
        if isinstance(i, int) and 0 <= i < len(cons):
            cov["consumables"].add(cons[i].get("key"))
    if method == "pack" and isinstance(params.get("card"), int):
        pack = _cards(before, "pack")
        i = params["card"]
        if 0 <= i < len(pack) and pack[i].get("set") in ("TAROT", "PLANET", "SPECTRAL"):
            cov["consumables"].add(pack[i].get("key"))
    if method == "buy" and isinstance(params.get("pack"), int):
        packs = _cards(before, "packs")
        i = params["pack"]
        if 0 <= i < len(packs):
            cov["boosters"].add(_booster(packs[i].get("key")))
    for v in ((after or {}).get("used_vouchers") or {}):
        cov["vouchers"].add(v)


def _booster(key: str | None) -> str | None:
    """p_arcana_normal_3 -> p_arcana_normal: the number is only the pack's art."""
    return re.sub(r"_\d+$", "", key) if isinstance(key, str) else key


def to_json(cov: dict[str, set]) -> dict[str, list]:
    return {k: sorted(x for x in v if x) for k, v in cov.items()}


def universe() -> dict[str, set]:
    """All content of game version 1.0.1o, from jackdaw's tables (the game's own prototypes)."""
    import jackdaw

    data = Path(jackdaw.__file__).parent / "engine/data"
    centers = json.loads((data / "centers.json").read_text())
    by_set: dict[str, set] = defaultdict(set)
    for key, c in centers.items():
        by_set[c.get("set")].add(key)
    blinds = json.loads((data / "blinds.json").read_text())
    tags = json.loads((data / "tags.json").read_text())
    return {
        "jokers_in_play": by_set["Joker"],
        "bosses": {b["name"] for b in blinds.values() if b.get("boss")},
        "tags": {t["name"] for t in tags.values()},
        "vouchers": by_set["Voucher"],
        "consumables": by_set["Tarot"] | by_set["Planet"] | by_set["Spectral"],
        "boosters": {_booster(k) for k in by_set["Booster"]},
        "editions": {"FOIL", "HOLO", "POLYCHROME", "NEGATIVE"},
        "enhancements": {"BONUS", "MULT", "WILD", "GLASS", "STEEL", "STONE", "GOLD", "LUCKY"},
        "seals": {"RED", "BLUE", "GOLD", "PURPLE"},
        "stickers": {"eternal", "perishable", "rental"},
    }


def report(covered: dict[str, set]) -> list[str]:
    uni = universe()
    lines = []
    for cat in CATEGORIES:
        have = covered.get(cat, set()) & uni[cat]
        missing = sorted(uni[cat] - have)
        lines.append(f"{cat:15s} {len(have):3d}/{len(uni[cat]):3d} covered" +
                     (f"; missing: {', '.join(missing[:40])}{' ...' if len(missing) > 40 else ''}" if missing else ""))
    return lines
