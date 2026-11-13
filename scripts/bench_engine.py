"""Throughput benchmark for the headless engine: N workers, each driving its own engine process.

  .venv/bin/python scripts/bench_engine.py --workers 1 --games 20
  .venv/bin/python scripts/bench_engine.py --workers 8 --games 80 --policy mixed

Reports games/s, actions/s, ms/action, antes reached, and engine memory per process.
"""

from __future__ import annotations

import argparse
import random
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def worker(args) -> list[dict]:
    wid, seeds, policy_name = args
    from balatro_bot.engine.client import EngineClient
    from balatro_bot.game.runloop import play_game
    from balatro_bot.policies.mixed import MixedPolicy
    from balatro_bot.policies.random_policy import RandomPolicy
    from balatro_bot.policies.scripted import ScriptedPolicy

    def make(seed):
        if policy_name == "scripted":
            return ScriptedPolicy()
        if policy_name == "random":
            return RandomPolicy(rng=random.Random(seed))
        return MixedPolicy(rng=random.Random(seed))

    out = []
    with EngineClient() as eng:
        rss = _rss_mb(eng._proc.pid)
        for s in seeds:
            r = play_game(eng, make(s), s)
            out.append({"seed": s, "won": r.won, "ante": r.ante, "actions": r.actions,
                        "seconds": r.seconds, "rss_mb": rss})
        out[-1]["rss_mb_end"] = _rss_mb(eng._proc.pid)
    return out


def _rss_mb(pid: int) -> float:
    try:
        kb = int(subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True).stdout.strip())
        return kb / 1024
    except Exception:
        return float("nan")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--workers", type=int, default=1)
    ap.add_argument("--games", type=int, default=20)
    ap.add_argument("--policy", choices=["scripted", "random", "mixed"], default="mixed")
    args = ap.parse_args()
    seeds = [f"B{i:05d}" for i in range(args.games)]
    chunks = [(w, seeds[w :: args.workers], args.policy) for w in range(args.workers)]
    t0 = time.perf_counter()
    # Each worker owns one engine process; the game logic runs there, so threads are enough here.
    with ThreadPoolExecutor(args.workers) as pool:
        results = [r for chunk in pool.map(worker, chunks) for r in chunk]
    wall = time.perf_counter() - t0
    actions = sum(r["actions"] for r in results)
    busy = sum(r["seconds"] for r in results)
    antes = [r["ante"] for r in results]
    rss_end = [r["rss_mb_end"] for r in results if "rss_mb_end" in r]
    print(f"workers={args.workers} games={len(results)} policy={args.policy}")
    print(f"wall {wall:.1f}s | {len(results) / wall:.2f} games/s | {actions / wall:.0f} actions/s aggregate")
    print(f"per game: {busy / len(results):.2f}s mean, {actions / len(results):.0f} actions | "
          f"{1000 * busy / actions:.1f} ms/action")
    print(f"antes reached: mean {statistics.mean(antes):.2f}, max {max(antes)} | wins {sum(r['won'] for r in results)}")
    if rss_end:
        print(f"engine memory per process: ~{statistics.mean(rss_end):.0f} MB")


if __name__ == "__main__":
    main()
