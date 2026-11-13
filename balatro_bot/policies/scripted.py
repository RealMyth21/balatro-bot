"""A simple scripted (non-LLM) policy. Used for timing tests and as a floor baseline.

Plays the best hand by base score (no joker effects), discards toward it when the
best hand can't clear the blind, buys affordable jokers, skips packs.
"""

from __future__ import annotations

from balatro_bot.scoring.hands import RANK_CHIPS, candidate_plays, classify, enhancement, is_debuffed, rank


def base_score(cards: list[dict], hands_info: dict) -> tuple[float, str]:
    name, scoring = classify(cards)
    info = hands_info.get(name, {"chips": 0, "mult": 0})
    chips, mult = float(info["chips"]), float(info["mult"])
    for c in scoring:
        if is_debuffed(c):
            continue
        e = enhancement(c)
        chips += 50 if e == "STONE" else RANK_CHIPS.get(rank(c) or "", 0)
        if e == "BONUS":
            chips += 30
        elif e == "MULT":
            mult += 4
        elif e == "GLASS":
            mult *= 2
        ed = (c.get("modifier") or {}).get("edition") if isinstance(c.get("modifier"), dict) else None
        if ed == "FOIL":
            chips += 50
        elif ed == "HOLO":
            mult += 10
        elif ed == "POLYCHROME":
            mult *= 1.5
    return chips * mult, name


def current_blind_target(gs: dict) -> int:
    for b in gs.get("blinds", {}).values():
        if b.get("status") == "CURRENT":
            return int(b.get("score", 0))
    return 0


class ScriptedPolicy:
    name = "scripted"

    def act(self, gs: dict) -> tuple[str, dict | None]:
        st = gs["state"]
        if st == "BLIND_SELECT":
            return "select", None
        if st == "ROUND_EVAL":
            return "cash_out", None
        if st == "SMODS_BOOSTER_OPENED":
            return "pack", {"skip": True}
        if st == "SHOP":
            jokers = gs.get("jokers", {})
            free_slots = jokers.get("limit", 0) - jokers.get("count", 0)
            for i, card in enumerate((gs.get("shop") or {}).get("cards", [])):
                if card.get("set") == "JOKER" and free_slots > 0 and card["cost"]["buy"] <= gs["money"]:
                    return "buy", {"card": i}
            return "next_round", None
        if st == "SELECTING_HAND":
            hand = gs["hand"]["cards"]
            need = current_blind_target(gs) - gs["round"].get("chips", 0)
            best = max(candidate_plays(hand), key=lambda idx: base_score([hand[i] for i in idx], gs["hands"])[0])
            score, _ = base_score([hand[i] for i in best], gs["hands"])
            r = gs["round"]
            if score >= need or r["discards_left"] == 0 or r["hands_left"] == 1:
                return "play", {"cards": list(best)}
            rest = [i for i in range(len(hand)) if i not in best]
            rest.sort(key=lambda i: RANK_CHIPS.get(rank(hand[i]) or "", 0))
            return "discard", {"cards": rest[:5] or [0]}
        raise ValueError(f"no action for state {st}")
