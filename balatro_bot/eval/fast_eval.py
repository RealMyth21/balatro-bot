"""Evaluate fast (non-LLM) policies on jackdaw, many games in parallel processes.

Policies:
  smart   jackdaw's own smart agent (jackdaw.cli.smart_agent)
  search  RoundSearchPolicy (balatro_bot.search): sampled-world search for plays/discards,
          smart agent for everything else
  random  jackdaw's lockstep RandomPolicy (half the hands played well, the rest random)

Results are appended to a JSONL file, one game per line; seeds already in the file are skipped, so
an interrupted run resumes where it stopped.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import random
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

DECKS = {"RED": "b_red", "BLUE": "b_blue", "YELLOW": "b_yellow", "GREEN": "b_green", "BLACK": "b_black",
         "MAGIC": "b_magic", "NEBULA": "b_nebula", "GHOST": "b_ghost", "ABANDONED": "b_abandoned",
         "CHECKERED": "b_checkered", "ZODIAC": "b_zodiac", "PAINTED": "b_painted", "ANAGLYPH": "b_anaglyph",
         "PLASMA": "b_plasma", "ERRATIC": "b_erratic"}
STAKES = {"WHITE": 1, "RED": 2, "GREEN": 3, "BLACK": 4, "BLUE": 5, "PURPLE": 6, "ORANGE": 7, "GOLD": 8}


def _make_policy(name: str, seed: str, opts: dict):
    from jackdaw.cli.smart_agent import smart_agent

    if name == "smart":
        return lambda g: smart_agent(g, g.legal_actions())
    if name == "search":
        from balatro_bot.search.round_search import RoundSearchPolicy

        pol = RoundSearchPolicy(seed=hash(seed) & 0xFFFFFFFF, **opts)
        return pol.act
    if name == "random":
        # jackdaw's lockstep RandomPolicy: random kind of move, concrete card selections
        from jackdaw.bridge.lockstep import RandomPolicy

        rng = random.Random(seed)
        rp = RandomPolicy(hand_skill=opts.get("hand_skill", 0.5))
        return lambda g: rp.choose(g, g.legal_actions(), rng)
    raise ValueError(f"unknown policy {name!r}")


def play_one(policy: str, seed: str, deck: str, stake: str, opts: dict, max_steps: int = 4000) -> dict:
    from jackdaw.core import Game
    from jackdaw.engine.actions import GamePhase

    t0 = time.perf_counter()
    g = Game.new(back=DECKS[deck], stake=STAKES[stake], seed=seed)
    act = _make_policy(policy, seed, opts)
    steps = 0
    error = None
    while g.phase != GamePhase.GAME_OVER and not g.won and steps < max_steps:
        try:
            g.step(act(g))
        except Exception as e:  # noqa: BLE001  record and stop this game, keep the run going
            error = f"{type(e).__name__}: {e}"
            break
        steps += 1
    return {"seed": seed, "policy": policy, "deck": deck, "stake": stake, "won": bool(g.won),
            "ante": g.ante, "round": g.round, "steps": steps, "seconds": round(time.perf_counter() - t0, 3),
            "error": error, "opts": opts}


def run(policy: str, seeds: list[str], out: Path, deck: str = "BLACK", stake: str = "GOLD",
        workers: int | None = None, opts: dict | None = None, progress: bool = True) -> list[dict]:
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if r.get("policy") == policy and r.get("deck") == deck and r.get("stake") == stake:
                    done.add(r["seed"])
    todo = [s for s in seeds if s not in done]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    results = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as ex, out.open("a") as fh:
        futs = [ex.submit(play_one, policy, s, deck, stake, opts or {}) for s in todo]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result()
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            results.append(r)
            if progress and (i % 50 == 0 or i == len(futs)):
                print(f"  {policy}: {i}/{len(futs)} games, {time.perf_counter() - t0:.0f}s", flush=True)
    return results
