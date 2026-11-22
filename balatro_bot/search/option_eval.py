"""Evaluate shop, pack and blind-select decisions by how far the run gets afterwards.

Each option is a plan: a list of balatrobot requests ("buy card 0, then leave"). For every option the
same set of imagined futures is built from what the player can see (WorldSampler: unseen deck
reshuffled, all randomness and the seed redrawn), the plan is carried out in each future through
jackdaw's balatrobot session (the live API's own semantics), and the run is played on to its end by a
default player. Every option faces the same futures, so differences come from the choice, not luck.

What is measured: the chance of reaching each ante (2..8) and of winning (clearing ante 8), plus mean
ante and furthest blind reached (skipped blinds count as passed). These are reported raw. The single
`score` combines them with weights that grow with the ante (RunScore): early in a run it behaves like
"how far do I get", late in a run like "do I win". The weights are a setting, not part of the
measurement, so they can be changed without re-running anything.

The default player finishing the run limits the numbers: a stronger player makes them more meaningful.
`rollout="search"` (default) plays hands with RoundSearchPolicy on a small budget and everything else
with jackdaw's smart agent; `rollout="smart"` uses the smart agent throughout (much faster, weaker).
A learned position evaluator can replace the play-to-the-end rollouts later (the `finish` hook).
"""

from __future__ import annotations

import json
import math
import random
import time
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jackdaw import _core
from jackdaw.cli.smart_agent import smart_agent
from jackdaw.core import Game
from jackdaw.engine.actions import GamePhase, decode_action

from balatro_bot.search.round_search import RoundSearchPolicy, WorldSampler

ANTES = range(2, 9)  # "reach ante k" for k = 2..8; then "win" = clear ante 8
BLIND_POS = {"bl_small": 1, "bl_big": 2}  # anything else is the boss (3)
DECISION_PHASES = (GamePhase.SHOP, GamePhase.PACK_OPENING, GamePhase.BLIND_SELECT)
# request methods that make up the plans (sorting/rearranging hands changes nothing here)
PLAN_METHODS = {"buy", "sell", "reroll", "next_round", "use", "pack", "select", "skip"}


@dataclass(frozen=True)
class RunScore:
    """One number for "how good is this run's outlook": a weighted sum of the chances of reaching
    ante 2..8 and of winning, scaled to 0..100. Default weights double per ante, so reaching ante 8 is
    worth 64x reaching ante 2 and a win 128x. Equal weights give plain average ante reached; steeper
    weights move the score toward pure win rate."""

    reach_weights: tuple[float, ...] = (1, 2, 4, 8, 16, 32, 64)  # reach ante 2, 3, ..., 8
    win_weight: float = 128

    def __post_init__(self):
        if len(self.reach_weights) != len(ANTES):
            raise ValueError(f"need {len(ANTES)} reach weights (antes 2..8)")

    def __call__(self, reach: dict[int, float], win: float) -> float:
        total = sum(self.reach_weights) + self.win_weight
        value = sum(w * reach[a] for w, a in zip(self.reach_weights, ANTES, strict=True)) + self.win_weight * win
        return 100.0 * value / total


@dataclass
class Outcome:
    ante: int
    won: bool
    blind_pos: int  # furthest blind reached, 1..24 (25 = won)
    steps: int


def outcome_of(g: Game, steps: int) -> Outcome:
    if g.won:
        return Outcome(g.ante, True, 25, steps)
    pos = BLIND_POS.get(g.blind.key, 3) if g.blind else 1
    return Outcome(g.ante, False, (g.ante - 1) * 3 + pos, steps)


def _policy(kind: str, seed: int) -> Callable[[Game], Any]:
    if kind == "smart":
        return lambda g: smart_agent(g, g.legal_actions())
    if kind == "search":
        return RoundSearchPolicy(worlds=2, n_plays=3, n_discards=3, seed=seed).act
    raise ValueError(f"unknown rollout player {kind!r} (use 'search' or 'smart')")


def play_out(g: Game, act: Callable[[Game], Any], leave_shop: bool, max_steps: int = 3000) -> Outcome:
    """Play to the end of the run. leave_shop: the plan was a whole shop visit, so if the game is
    still in that shop (or a pack opened from it), it leaves as soon as it is back in the shop."""
    steps = 0
    while g.phase != GamePhase.GAME_OVER and not g.won and steps < max_steps:
        if leave_shop and g.phase == GamePhase.SHOP:
            action = next(a for a in g.legal_actions() if type(a).__name__ == "NextRound")
        else:
            if leave_shop and g.phase not in (GamePhase.SHOP, GamePhase.PACK_OPENING):
                leave_shop = False
            action = act(g)
        g.step(action)
        steps += 1
    return outcome_of(g, steps)


def normalize_plan(option: Any) -> list[tuple[str, dict]]:
    """A plan as [(method, params)]. Accepts {"steps": [...]} or a bare list; each step is
    {"buy": {"card": 0}} or {"method": "buy", "params": {"card": 0}}."""
    steps = option.get("steps") if isinstance(option, dict) else option
    if not isinstance(steps, list) or not steps:
        raise ValueError("an option needs a non-empty list of steps")
    out = []
    for step in steps:
        if not isinstance(step, dict):
            raise ValueError(f"bad step {step!r}")
        if "method" in step:
            method, params = step["method"], step.get("params") or {}
        elif len(step) == 1:
            method, params = next(iter(step.items()))
            params = params or {}
        else:
            raise ValueError(f"bad step {step!r}: use {{\"buy\": {{\"card\": 0}}}}")
        if method not in PLAN_METHODS:
            raise ValueError(f"{method!r} is not a shop/pack/blind action ({', '.join(sorted(PLAN_METHODS))})")
        if not isinstance(params, dict):
            raise ValueError(f"params of {method!r} must be an object")
        out.append((method, params))
    return out


def default_options(game: Game, limit: int = 8) -> list[list[tuple[str, dict]]]:
    """A shortlist from the legal moves: in the shop every purchase (then leave) and leaving now; in
    a pack every pick and skipping; at blind select playing or skipping the blind."""
    session = _core.BotSession.from_game(game._g)
    plans: list[list[tuple[str, dict]]] = []
    for raw in json.loads(session.legal_actions("balatrobot")):
        name = type(decode_action(raw)).__name__
        if name not in ("BuyCard", "OpenBooster", "RedeemVoucher", "NextRound", "PickPackCard", "SkipPack",
                        "SelectBlind", "SkipBlind"):
            continue
        tr = session.translate(json.dumps(raw))
        if tr is None:
            continue
        method, params = tr[0], json.loads(tr[1]) or {}
        if not isinstance(params, dict):
            params = {}
        plan = [(method, params)]
        # buying a card or voucher ends the plan with leaving; a bought pack opens first (its pick is
        # made by the default player, and the shop is then left automatically)
        if game.phase == GamePhase.SHOP and name in ("BuyCard", "RedeemVoucher"):
            plan.append(("next_round", {}))
        plans.append(plan)
    plans.sort(key=lambda p: p[0][0] not in ("next_round", "select"))  # the "do nothing" baseline first
    seen, out = set(), []
    for p in plans:
        key = json.dumps(p, sort_keys=True)
        if key not in seen:
            seen.add(key)
            out.append(p)
    return out[:limit]


@dataclass
class OptionResult:
    plan: list[tuple[str, dict]]
    error: str | None = None
    outcomes: list[Outcome] = field(default_factory=list)
    cut_short: int = 0  # futures where a step after a reroll was not possible

    def summary(self, scorer: RunScore) -> dict:
        steps = [{m: p} for m, p in self.plan]
        if self.error:
            return {"steps": steps, "error": self.error}
        n = len(self.outcomes)
        reach = {a: sum(o.ante >= a or o.won for o in self.outcomes) / n for a in ANTES}
        win = sum(o.won for o in self.outcomes) / n
        per_world = [scorer({a: float(o.ante >= a or o.won) for a in ANTES}, float(o.won)) for o in self.outcomes]
        mean = sum(per_world) / n
        sd = math.sqrt(sum((x - mean) ** 2 for x in per_world) / max(1, n - 1))
        out = {"steps": steps, "score": round(scorer(reach, win), 2), "score_stderr": round(sd / math.sqrt(n), 2),
               "reach_ante": {str(a): round(reach[a], 3) for a in ANTES}, "win_chance": round(win, 3),
               "mean_ante": round(sum(o.ante for o in self.outcomes) / n, 2),
               "mean_furthest_blind": round(sum(o.blind_pos for o in self.outcomes) / n, 2)}
        if self.cut_short:
            out["plan_cut_short_in"] = f"{self.cut_short}/{n} futures (a step after a reroll was not possible)"
        return out


@dataclass
class OptionEvaluator:
    """Compare plans by playing shared imagined futures to the end of the run (see module docstring)."""

    worlds: int = 12
    rollout: str = "search"
    seed: int = 0
    scorer: RunScore = field(default_factory=RunScore)
    max_options: int = 8

    def evaluate(self, game: Game, options: list[Any] | None = None) -> dict:
        t0 = time.perf_counter()
        if game.phase not in DECISION_PHASES:
            raise ValueError("options can be evaluated in the shop, in an open pack or at blind select")
        plans: list[Any] = []
        errors: list[OptionResult] = []
        for opt in (options if options else default_options(game, self.max_options))[: self.max_options]:
            try:
                plans.append(normalize_plan(opt) if options else opt)
            except ValueError as e:
                errors.append(OptionResult(plan=[], error=f"{e} (option: {opt!r})"[:300]))
        rng = random.Random(self.seed)
        sampler = WorldSampler(game)
        worlds = [sampler.sample_core(rng) for _ in range(self.worlds)]
        results = []
        for plan in plans:
            res = OptionResult(plan=plan)
            # In the shop a plan is the whole visit: unless it leaves itself, leave once it is done.
            leave = game.phase == GamePhase.SHOP and not any(m == "next_round" for m, _ in plan)
            for w, core in enumerate(worlds):
                session = _core.BotSession.from_game(core.clone())
                rerolled = False
                for method, params in plan:
                    ok, text = session.call(method, json.dumps(params))
                    if not ok:
                        msg = json.loads(text).get("message", text) if text.startswith("{") else text
                        if not rerolled:  # what the step needs is visible, so it fails in every future
                            res.error = f"step {method} {params} is not possible now: {msg}"
                        else:  # the rerolled shop differs between futures
                            res.cut_short += 1
                        break
                    rerolled = rerolled or method == "reroll"
                if res.error:
                    break
                act = _policy(self.rollout, zlib.crc32(f"{self.seed}:{w}".encode()))  # same player per future
                res.outcomes.append(play_out(Game(session.game()), act, leave_shop=leave))
            results.append(res)
        summaries = [r.summary(self.scorer) for r in results] + [r.summary(self.scorer) for r in errors]
        base = summaries[0] if summaries and "score" in summaries[0] else None
        if base:
            for s in summaries[1:]:
                if "score" in s:
                    s["vs_first"] = round(s["score"] - base["score"], 2)
        ranked = sorted(summaries, key=lambda s: s.get("score", -1), reverse=True)
        return {"phase": game.phase.name, "worlds": self.worlds, "rollout": self.rollout,
                "weights": {"reach_ante_2_to_8": list(self.scorer.reach_weights), "win": self.scorer.win_weight},
                "baseline": summaries[0]["steps"] if summaries else None, "options": ranked,
                "seconds": round(time.perf_counter() - t0, 2)}
