"""Random legal-ish actions. Broad coverage for engine validation and throughput benchmarks.

It exercises skips, rerolls, buying jokers/consumables/vouchers/packs, pack picks, selling,
using consumables, discards and plays.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class RandomPolicy:
    rng: random.Random = field(default_factory=random.Random)
    name: str = "random"

    def act(self, gs: dict) -> tuple[str, dict | None]:
        r = self.rng
        st = gs["state"]
        if st == "BLIND_SELECT":
            on_deck = [b for b in gs.get("blinds", {}).values() if b.get("status") == "SELECT"]
            if on_deck and on_deck[0].get("type") != "BOSS" and r.random() < 0.2:
                return "skip", None
            return "select", None
        if st == "ROUND_EVAL":
            return "cash_out", None
        if st == "SMODS_BOOSTER_OPENED":
            cards = (gs.get("pack") or {}).get("cards", [])
            if not cards or r.random() < 0.25:
                return "pack", {"skip": True}
            i = r.randrange(len(cards))
            hand = (gs.get("hand") or {}).get("cards", [])
            if hand and cards[i].get("set") in ("TAROT", "SPECTRAL"):
                k = r.randint(1, min(2, len(hand)))
                return "pack", {"card": i, "targets": sorted(r.sample(range(len(hand)), k))}
            return "pack", {"card": i}
        if st == "SHOP":
            x = r.random()
            money = gs.get("money", 0)
            shop = (gs.get("shop") or {}).get("cards", [])
            vouchers = (gs.get("vouchers") or {}).get("cards", [])
            packs = (gs.get("packs") or {}).get("cards", [])
            jokers = (gs.get("jokers") or {}).get("cards", [])
            if x < 0.30 and shop:
                i = r.randrange(len(shop))
                if shop[i]["cost"]["buy"] <= money:
                    return "buy", {"card": i}
            if x < 0.40 and packs:
                i = r.randrange(len(packs))
                if packs[i]["cost"]["buy"] <= money:
                    return "buy", {"pack": i}
            if x < 0.45 and vouchers and vouchers[0]["cost"]["buy"] <= money:
                return "buy", {"voucher": 0}
            if x < 0.50 and jokers and r.random() < 0.3:
                return "sell", {"joker": r.randrange(len(jokers))}
            if x < 0.55 and money >= gs.get("round", {}).get("reroll_cost", 99) + 5:
                return "reroll", None
            return "next_round", None
        if st == "SELECTING_HAND":
            hand = gs["hand"]["cards"]
            rd = gs["round"]
            cons = (gs.get("consumables") or {}).get("cards", [])
            if cons and r.random() < 0.15:
                k = r.randint(1, min(2, len(hand)))
                return "use", {"consumable": r.randrange(len(cons)), "cards": sorted(r.sample(range(len(hand)), k))}
            k = r.randint(1, min(5, len(hand)))
            idx = sorted(r.sample(range(len(hand)), k))
            if rd.get("discards_left", 0) > 0 and r.random() < 0.35:
                return "discard", {"cards": idx}
            return "play", {"cards": idx}
        raise ValueError(f"no action for state {st}")
