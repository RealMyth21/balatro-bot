"""Evaluate fast (non-LLM) policies on jackdaw, many games in parallel processes.

Policies:
  smart   jackdaw's own smart agent (jackdaw.cli.smart_agent)
  search  RoundSearchPolicy (balatro_bot.search): sampled-world search for plays/discards,
          smart agent for everything else
  random  jackdaw's lockstep RandomPolicy (half the hands played well, the rest random)

Results are appended to a JSONL file, one game per line, labelled with the policy and its options
(e.g. "search[worlds=16]"). Seeds already played with the same label, deck and stake are skipped, so
an interrupted run resumes where it stopped; games that stopped on an error are retried.
"""

from __future__ import annotations

import json
import multiprocessing as mp
import os
import random
import time
import zlib
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

DECKS = {"RED": "b_red", "BLUE": "b_blue", "YELLOW": "b_yellow", "GREEN": "b_green", "BLACK": "b_black",
         "MAGIC": "b_magic", "NEBULA": "b_nebula", "GHOST": "b_ghost", "ABANDONED": "b_abandoned",
         "CHECKERED": "b_checkered", "ZODIAC": "b_zodiac", "PAINTED": "b_painted", "ANAGLYPH": "b_anaglyph",
         "PLASMA": "b_plasma", "ERRATIC": "b_erratic"}
STAKES = {"WHITE": 1, "RED": 2, "GREEN": 3, "BLACK": 4, "BLUE": 5, "PURPLE": 6, "ORANGE": 7, "GOLD": 8}


def run_label(policy: str, opts: dict) -> str:
    return policy + (f"[{','.join(f'{k}={v}' for k, v in sorted(opts.items()))}]" if opts else "")


def _make_policy(name: str, seed: str, opts: dict):
    from jackdaw.cli.smart_agent import smart_agent

    if name == "smart":
        return lambda g: smart_agent(g, g.legal_actions())
    if name == "search":
        from balatro_bot.search.round_search import RoundSearchPolicy

        pol = RoundSearchPolicy(seed=zlib.crc32(seed.encode()), **opts)  # same in every process
        return pol.act
    if name == "random":
        # jackdaw's lockstep RandomPolicy: random kind of move, concrete card selections
        from jackdaw.bridge.lockstep import RandomPolicy

        rng = random.Random(seed)
        rp = RandomPolicy(hand_skill=opts.get("hand_skill", 0.5))
        return lambda g: rp.choose(g, g.legal_actions(), rng)
    raise ValueError(f"unknown policy {name!r}")


def play_one(policy: str, seed: str, deck: str, stake: str, opts: dict, max_steps: int = 4000) -> dict:
    from jackdaw.core import Game, NotPortedError
    from jackdaw.engine.actions import GamePhase, SelectBlind

    from balatro_bot.sim.jackdaw_fixes import fixed_for_select

    t0 = time.perf_counter()
    g = Game.new(back=DECKS[deck], stake=STAKES[stake], seed=seed)
    act = _make_policy(policy, seed, opts)
    steps = 0
    error = kind = None
    while g.phase != GamePhase.GAME_OVER and not g.won and steps < max_steps:
        try:
            action = act(g)
            if isinstance(action, SelectBlind):  # jackdaw corrected where it differs from the game
                g = fixed_for_select(g)
            g.step(action)
        except NotPortedError as e:  # content jackdaw can't run: says nothing about the policy
            error, kind = f"NotPortedError: {e}", "infra"
            break
        except (KeyboardInterrupt, SystemExit):
            raise
        except BaseException as e:  # noqa: BLE001  incl. pyo3 PanicException; keep the run going
            error, kind = f"{type(e).__name__}: {e}", "infra"
            break
        steps += 1
    if error is None and steps >= max_steps:
        error, kind = f"step cap reached ({max_steps} actions)", "policy"
    return {"seed": seed, "policy": policy, "label": run_label(policy, opts), "deck": deck, "stake": stake,
            "won": bool(g.won), "ante": g.ante, "round": g.round, "steps": steps,
            "seconds": round(time.perf_counter() - t0, 3), "error": error, "error_kind": kind, "opts": opts}


def run(policy: str, seeds: list[str], out: Path, deck: str = "BLACK", stake: str = "GOLD",
        workers: int | None = None, opts: dict | None = None, progress: bool = True) -> list[dict]:
    from balatro_bot.eval.report import error_kind, label

    opts = opts or {}
    name = run_label(policy, opts)
    out.parent.mkdir(parents=True, exist_ok=True)
    done = set()
    if out.exists():
        for line in out.read_text().splitlines():
            if line.strip():
                r = json.loads(line)
                if (label(r) == name and r.get("deck") == deck and r.get("stake") == stake
                        and error_kind(r) != "infra"):
                    done.add(r["seed"])
    todo = [s for s in seeds if s not in done]
    workers = workers or max(1, (os.cpu_count() or 2) - 1)
    results = []
    t0 = time.perf_counter()
    with ProcessPoolExecutor(workers, mp_context=mp.get_context("spawn")) as ex, out.open("a") as fh:
        futs = {ex.submit(play_one, policy, s, deck, stake, opts): s for s in todo}
        for i, f in enumerate(as_completed(futs), 1):
            try:
                r = f.result()
            except Exception as e:  # noqa: BLE001  a worker died; record it and keep going
                r = {"seed": futs[f], "policy": policy, "label": name, "deck": deck, "stake": stake,
                     "won": False, "ante": None, "error": f"{type(e).__name__}: {e}", "error_kind": "infra",
                     "opts": opts}
            fh.write(json.dumps(r) + "\n")
            fh.flush()
            results.append(r)
            if progress and (i % 50 == 0 or i == len(futs)):
                print(f"  {name}: {i}/{len(futs)} games, {time.perf_counter() - t0:.0f}s", flush=True)
    return results
