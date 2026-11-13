"""Poker-hand detection following Balatro's rules (functions/misc_functions.lua).

Cards are dicts in balatrobot gamestate format. Only the fields used here matter:
  value.rank ("2".."9","T","J","Q","K","A"), value.suit ("H","D","C","S"),
  modifier.enhancement (e.g. "STONE", "WILD"), state.debuff.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from itertools import combinations

RANK_ORDER = {r: i for i, r in enumerate("23456789TJQKA", start=2)}  # 2..14
RANK_CHIPS = {**{str(n): n for n in range(2, 10)}, "T": 10, "J": 10, "Q": 10, "K": 10, "A": 11}
FACE_RANKS = {"J", "Q", "K"}
SUITS = ("H", "D", "C", "S")

HAND_PRIORITY = [
    "Flush Five", "Flush House", "Five of a Kind", "Straight Flush", "Four of a Kind",
    "Full House", "Flush", "Straight", "Three of a Kind", "Two Pair", "Pair", "High Card",
]


@dataclass(frozen=True)
class HandFlags:
    """Joker-driven rule changes for hand detection."""
    four_fingers: bool = False  # flushes/straights with 4 cards
    shortcut: bool = False      # straights may skip one rank
    smeared: bool = False       # hearts=diamonds, spades=clubs
    splash: bool = False        # every played card scores


def rank(c: dict) -> str | None:
    if enhancement(c) == "STONE":
        return None
    return (c.get("value") or {}).get("rank")


def suit(c: dict) -> str | None:
    if enhancement(c) == "STONE":
        return None
    return (c.get("value") or {}).get("suit")


def enhancement(c: dict) -> str | None:
    m = c.get("modifier") or {}
    return m.get("enhancement") if isinstance(m, dict) else None


def is_debuffed(c: dict) -> bool:
    s = c.get("state") or {}
    return bool(s.get("debuff")) if isinstance(s, dict) else False


def _suit_matches(c: dict, s: str, flags: HandFlags) -> bool:
    if enhancement(c) == "WILD" and not is_debuffed(c):
        return True
    cs = suit(c)
    if cs is None:
        return False
    if flags.smeared:
        red, black = {"H", "D"}, {"S", "C"}
        return (cs in red and s in red) or (cs in black and s in black)
    return cs == s


def _flush(cards: list[dict], flags: HandFlags) -> list[dict]:
    need = 4 if flags.four_fingers else 5
    if len(cards) < need:
        return []
    for s in SUITS:
        members = [c for c in cards if _suit_matches(c, s, flags)]
        if len(members) >= need:
            return members
    return []


def _straight(cards: list[dict], flags: HandFlags) -> list[dict]:
    need = 4 if flags.four_fingers else 5
    if len(cards) < need:
        return []
    by_rank: dict[int, list[dict]] = {}
    for c in cards:
        r = rank(c)
        if r is None:
            continue
        v = RANK_ORDER[r]
        by_rank.setdefault(v, []).append(c)
        if v == 14:
            by_rank.setdefault(1, []).append(c)  # ace low
    present = sorted(by_rank)
    best: list[int] = []
    # walk runs allowing a gap of 1 rank when shortcut is held
    for start in present:
        run = [start]
        cur = start
        while True:
            nxt = None
            for step in ((1, 2) if flags.shortcut else (1,)):
                if cur + step in by_rank and cur + step <= 14:
                    nxt = cur + step
                    break
            if nxt is None:
                break
            run.append(nxt)
            cur = nxt
        if len(run) > len(best):
            best = run
    if len(best) < need:
        return []
    out: list[dict] = []
    for v in best:
        for c in by_rank[v]:
            if c not in out:
                out.append(c)
    return out


def classify(cards: list[dict], flags: HandFlags = HandFlags()) -> tuple[str, list[dict]]:
    """Return (hand_name, scoring_cards) for a played set of 1-5 cards."""
    ranked = [c for c in cards if rank(c) is not None]
    counts = Counter(rank(c) for c in ranked)
    groups = sorted(counts.items(), key=lambda kv: (kv[1], RANK_ORDER[kv[0]]), reverse=True)
    flush = _flush(cards, flags)
    straight = _straight(cards, flags)

    def of_rank(*ranks: str) -> list[dict]:
        return [c for c in cards if rank(c) in ranks]

    top = groups[0][1] if groups else 0
    name: str
    scoring: list[dict]
    if top >= 5 and flush:
        name, scoring = "Flush Five", of_rank(groups[0][0])
    elif top >= 3 and len(groups) > 1 and groups[1][1] >= 2 and flush:
        name, scoring = "Flush House", of_rank(groups[0][0], groups[1][0])
    elif top >= 5:
        name, scoring = "Five of a Kind", of_rank(groups[0][0])
    elif flush and straight:
        name, scoring = "Straight Flush", [c for c in cards if c in flush or c in straight]
    elif top >= 4:
        name, scoring = "Four of a Kind", of_rank(groups[0][0])
    elif top >= 3 and len(groups) > 1 and groups[1][1] >= 2:
        name, scoring = "Full House", of_rank(groups[0][0], groups[1][0])
    elif flush:
        name, scoring = "Flush", flush
    elif straight:
        name, scoring = "Straight", straight
    elif top >= 3:
        name, scoring = "Three of a Kind", of_rank(groups[0][0])
    elif top >= 2 and len(groups) > 1 and groups[1][1] >= 2:
        name, scoring = "Two Pair", of_rank(groups[0][0], groups[1][0])
    elif top >= 2:
        name, scoring = "Pair", of_rank(groups[0][0])
    else:
        hi = max(ranked, key=lambda c: RANK_ORDER[rank(c)], default=None)
        name, scoring = "High Card", ([hi] if hi else [])

    if flags.splash:
        scoring = list(cards)
    else:
        # stone cards always score
        scoring = scoring + [c for c in cards if enhancement(c) == "STONE" and c not in scoring]
        scoring.sort(key=lambda c: cards.index(c))  # keep play order
    return name, scoring


def contained_hands(cards: list[dict], flags: HandFlags = HandFlags()) -> set[str]:
    """Hands 'contained' in a played set (for jokers like Jolly/Sly/Spare Trousers)."""
    name, _ = classify(cards, flags)
    out = {name}
    idx = HAND_PRIORITY.index(name)
    counts = Counter(rank(c) for c in cards if rank(c) is not None)
    vals = sorted(counts.values(), reverse=True)
    if vals and vals[0] >= 2:
        out.add("Pair")
    if vals and vals[0] >= 3:
        out.add("Three of a Kind")
    if vals and vals[0] >= 4:
        out.add("Four of a Kind")
    if len(vals) > 1 and vals[0] >= 2 and vals[1] >= 2:
        out.add("Two Pair")
    if _flush(cards, flags):
        out.add("Flush")
    if _straight(cards, flags):
        out.add("Straight")
    if len(vals) > 1 and vals[0] >= 3 and vals[1] >= 2:
        out.add("Full House")
    return {h for h in out if HAND_PRIORITY.index(h) >= idx or h == name}


def candidate_plays(hand: list[dict], max_size: int = 5):
    """All index tuples of 1..max_size cards from the hand (218 for an 8-card hand)."""
    n = len(hand)
    for k in range(1, min(max_size, n) + 1):
        yield from combinations(range(n), k)
