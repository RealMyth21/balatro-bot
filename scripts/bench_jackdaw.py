"""Speed of jackdaw (pinned v0.2.6) on Black Deck / Gold Stake, two ways:

  api    - through BalatroBot's API (the interface our agents use), with our MixedPolicy in Python.
           Includes JSON views and Python policy time, like an agent loop would.
  vector - jackdaw's native BalatroVectorEnv (Rust, multi-threaded) with random masked actions:
           the raw engine ceiling for RL.

  .venv/bin/python scripts/bench_jackdaw.py --mode api --games 200
  .venv/bin/python scripts/bench_jackdaw.py --mode vector --envs 256 --seconds 10 --threads 0
"""

from __future__ import annotations

import argparse
import random
import statistics
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def bench_api(games: int) -> None:
    from balatro_bot.game.runloop import play_game
    from balatro_bot.policies.mixed import MixedPolicy
    from balatro_bot.sim.jackdaw_backend import JackdawClient

    jd = JackdawClient()
    t0 = time.perf_counter()
    res = [play_game(jd, MixedPolicy(rng=random.Random(i)), f"JB{i:05d}") for i in range(games)]
    wall = time.perf_counter() - t0
    acts = sum(r.actions for r in res)
    print(f"api: {games} games, {acts} actions in {wall:.2f}s -> {games / wall:.0f} games/s, "
          f"{acts / wall:.0f} actions/s, {1000 * wall / acts:.3f} ms/action (policy=mixed, one thread)")
    print(f"     antes: mean {statistics.mean(r.ante for r in res):.2f}, max {max(r.ante for r in res)}")


def bench_vector(envs: int, seconds: float, threads: int) -> None:
    import numpy as np
    from jackdaw.env import BalatroVectorEnv

    env = BalatroVectorEnv(num_envs=envs, back_keys=["b_black"], stakes=[8], num_threads=threads,
                           seed_prefix="BENCH")
    obs, info = env.reset(seed=0)
    masks = env.action_masks()
    rng = np.random.default_rng(0)
    steps = episodes = 0
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < seconds:
        cs = np.cumsum(masks, axis=1)
        pick = (cs > rng.random((envs, 1)) * cs[:, -1:]).argmax(axis=1)
        obs, rew, term, trunc, info = env.step(pick)
        masks = env.action_masks()
        steps += envs
        episodes += int(np.sum(term | trunc))
    wall = time.perf_counter() - t0
    print(f"vector: {envs} envs, threads={threads or 'all cores'}: {steps / wall:,.0f} steps/s, "
          f"{episodes / wall:,.0f} episodes/s (random masked actions, Black/Gold)")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mode", choices=["api", "vector"], default="api")
    ap.add_argument("--games", type=int, default=200)
    ap.add_argument("--envs", type=int, default=256)
    ap.add_argument("--seconds", type=float, default=10)
    ap.add_argument("--threads", type=int, default=0)
    args = ap.parse_args()
    if args.mode == "api":
        bench_api(args.games)
    else:
        bench_vector(args.envs, args.seconds, args.threads)


if __name__ == "__main__":
    main()
