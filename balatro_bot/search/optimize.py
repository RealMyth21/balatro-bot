"""Order optimizers for the current hand: which joker order and which played-card order score most.

In Balatro jokers trigger left to right (a x-Mult joker after the +Mult ones multiplies more; Blueprint
and Brainstorm copy their neighbours) and played cards score in hand order (Hanging Chad, Photograph and
similar look at the first card). The game lets a player rearrange both before playing, so the best
order is part of the best play. Scores come from jackdaw's exact scoring on copies of the game,
reordered with balatrobot's own `rearrange` request.

Every score is given three ways, never revealing the actual upcoming rolls:
  min  every chance effect fails (Lucky cards, Glass breaking, Bloodstone, ...): guaranteed
  max  every chance effect succeeds
  avg  the mean over futures with all randomness redrawn (and face-down cards redrawn), as a player
       would expect it

Fairness follows the other tools: face-down hand cards are scored as the average over what they could
be (balatro_bot.search.round_search.WorldSampler), and face-down jokers (Amber Acorn) can't be ordered
on purpose, since the player doesn't know which is which.
"""

from __future__ import annotations

import json
import random
from itertools import permutations
from typing import Any

from jackdaw import _core
from jackdaw.core import Game

from balatro_bot.search.round_search import (
    WorldSampler,
    _hand_info,
    _preview_total,
    has_hidden,
    visible_ranked_plays,
)

EXHAUSTIVE_JOKERS = 6  # all orders up to 6 jokers (720); local search beyond


def _reorder(core, area: str, perm: list[int]):
    """A copy of the game with `area` ("jokers" or "hand") rearranged: perm lists current indices in
    their new order, as balatrobot's rearrange takes them."""
    session = _core.BotSession.from_game(core)
    ok, text = session.call("rearrange", json.dumps({area: perm}))
    if not ok:
        raise ValueError(f"rearrange {area} {perm} refused: {text[:200]}")
    return session.game()


class _Scorer:
    """Scores plays on the worlds a player can reason about: the real game when nothing is hidden,
    else sampled worlds (face-down hand cards redrawn). avg uses separate worlds with all randomness
    redrawn, so it is an honest expectation."""

    def __init__(self, game: Game, rng: random.Random, hidden_worlds: int = 8, avg_worlds: int = 16):
        self.game = game
        self.hidden = has_hidden(game)
        self.cores = ([WorldSampler(game).sample_core(rng) for _ in range(hidden_worlds)] if self.hidden
                      else [game._g])
        self.rng, self.avg_worlds = rng, avg_worlds

    def reordered(self, area: str, perm: list[int]) -> list:
        return [_reorder(c, area, perm) for c in self.cores]

    @staticmethod
    def mean(cores: list, cards: tuple, rolls: str | None) -> float | None:
        totals = [_preview_total(c, cards, rolls) for c in cores]
        return None if any(t is None for t in totals) else sum(totals) / len(totals)

    def average(self, area: str | None, perm: list[int] | None, cards: tuple) -> float | None:
        """Mean score over futures with redrawn randomness, after an optional rearrange."""
        g = self.game if area is None else Game(_reorder(self.game._g, area, perm))
        sampler = WorldSampler(g)
        worlds = [sampler.sample_core(random.Random(self.rng.random())) for _ in range(self.avg_worlds)]
        return self.mean(worlds, cards, None)

    def scores(self, cores: list, cards: tuple, area: str | None = None, perm: list[int] | None = None) -> dict:
        lo, hi = self.mean(cores, cards, "low"), self.mean(cores, cards, "high")
        return {"min": _r(lo), "max": _r(hi), "avg": _r(self.average(area, perm, cards))}


def _r(x: float | None) -> float | None:
    return None if x is None else round(x, 1)


def _names(views) -> list[str]:
    return [v.key for v in views]


def optimize_jokers(game: Game, top_plays: int = 8, seed: int = 0) -> dict:
    """The joker order that makes the best play score most (guaranteed score first, then best case)."""
    jokers = list(game.jokers)
    if any(j.face_down for j in jokers):
        return {"error": "jokers are face down (Amber Acorn): which joker is where is hidden, so their order "
                         "can't be chosen on purpose"}
    n = len(jokers)
    rng = random.Random(seed)
    sc = _Scorer(game, rng)
    plays = [c for _, c in visible_ranked_plays(game, rng, cores=sc.cores)[:top_plays]]
    if not plays:
        return {"error": "no legal play"}

    def value(cores) -> tuple[float, float, tuple]:
        best = (-1.0, -1.0, plays[0])
        for c in plays:
            lo, hi = sc.mean(cores, c, "low"), sc.mean(cores, c, "high")
            if lo is not None and (lo, hi) > best[:2]:
                best = (lo, hi, c)
        return best

    current = list(range(n))
    cur_val = value(sc.cores)
    best_perm, best_val, checked = current, cur_val, 1
    if n > 1:
        if n <= EXHAUSTIVE_JOKERS:
            method = f"all {sum(1 for _ in permutations(range(n)))} orders"
            for perm in permutations(range(n)):
                perm = list(perm)
                if perm == current:
                    continue
                v = value(sc.reordered("jokers", perm))
                checked += 1
                if v[:2] > best_val[:2]:
                    best_perm, best_val = perm, v
        else:  # too many orders: improve by moving one joker at a time until nothing helps
            method = "local search (move one joker at a time)"
            improved = True
            while improved:
                improved = False
                for i in range(n):
                    for j in range(n):
                        if i == j:
                            continue
                        perm = list(best_perm)
                        perm.insert(j, perm.pop(i))
                        v = value(sc.reordered("jokers", perm))
                        checked += 1
                        if v[:2] > best_val[:2]:
                            best_perm, best_val, improved = perm, v, True
    else:
        method = "nothing to order"
    names = _names(jokers)
    play_info = lambda v, cores, perm: {"cards": list(v[2]), **sc.scores(cores, v[2], "jokers" if perm else None, perm)}  # noqa: E731
    changed = best_perm != current
    out = {"current_order": names, "best_order": [names[i] for i in best_perm],
           "best_play_now": play_info(cur_val, sc.cores, None),
           "best_play_with_best_order": play_info(best_val, sc.reordered("jokers", best_perm) if changed else sc.cores,
                                                  best_perm if changed else None),
           "gain_guaranteed": _r(best_val[0] - cur_val[0]), "orders_checked": checked, "method": method,
           "rearrange": {"jokers": best_perm} if changed else None}
    if sc.hidden:
        out["note"] = "face-down cards in hand: scores are averages over what they could be"
    if not changed:
        out["note"] = (out.get("note", "") + "; " if out.get("note") else "") + "the current order is already best"
    return out


def optimize_hand(game: Game, top: int = 5, candidates: int = 8, seed: int = 0) -> dict:
    """The best plays with their best scoring order: for each strong card set, every order of the played
    cards is tried (played cards score left to right in hand order)."""
    rng = random.Random(seed)
    sc = _Scorer(game, rng)
    n, _ = _hand_info(game._g)
    ranked = visible_ranked_plays(game, rng, cores=sc.cores)
    if not ranked:
        return {"error": "no legal play"}
    results = []
    for _, cards in ranked[:candidates]:
        positions = sorted(cards)
        base_lo, base_hi = sc.mean(sc.cores, cards, "low"), sc.mean(sc.cores, cards, "high")
        best = (base_lo, base_hi, None)
        for order in permutations(positions):
            if list(order) == positions:
                continue
            perm = list(range(n))
            for pos, card in zip(positions, order, strict=True):  # same slots, cards in the new order
                perm[pos] = card
            cores = sc.reordered("hand", perm)
            lo, hi = sc.mean(cores, cards, "low"), sc.mean(cores, cards, "high")
            if lo is not None and (lo, hi) > best[:2]:
                best = (lo, hi, perm)
        perm = best[2]
        order = [perm[p] for p in positions] if perm else positions
        cores = sc.reordered("hand", perm) if perm else sc.cores
        entry = {"cards": list(cards), "scoring_order": order,
                 **sc.scores(cores, cards, "hand" if perm else None, perm),
                 "gain_guaranteed_vs_hand_order": _r(best[0] - base_lo) if base_lo is not None else None,
                 "rearrange": {"hand": perm} if perm else None}
        results.append(entry)
    results.sort(key=lambda e: (e["min"] or -1, e["max"] or -1), reverse=True)
    hand_type = {}
    for e in results[:top]:  # hand types only for plays made of face-up cards (as score_plays does)
        if not any(game.hand[i].face_down for i in e["cards"]):
            hand_type[tuple(e["cards"])] = game.preview_play(tuple(e["cards"]), rolls="low").hand_type
        e["hand"] = hand_type.get(tuple(e["cards"]), "unknown (face-down cards)")
    out = {"plays": results[:top], "how_to_use": ("if a play has 'rearrange', send that rearrange first; the play's "
                                                  "card indices stay the same, only their order changes")}
    if sc.hidden:
        out["note"] = "face-down cards in hand: scores are averages over what they could be"
    return out


def average_scores(game: Game, plays: list[tuple], worlds: int = 16, seed: int = 0) -> dict[tuple, Any]:
    """Expected score of each play over futures with all randomness (and face-down cards) redrawn."""
    sampler = WorldSampler(game)
    rng = random.Random(seed)
    cores = [sampler.sample_core(rng) for _ in range(worlds)]
    return {c: _r(_Scorer.mean(cores, c, None)) for c in plays}

