"""Time full Black Deck / Gold Stake runs on the real game with the scripted policy.

Measures game-side time per action type and actions per run, so LLM latency can be
added on top for a wall-clock estimate.

  .venv/bin/python scripts/time_runs.py --runs 5
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balatro_bot.game.client import BalatroClient, RPCError  # noqa: E402
from balatro_bot.game.launcher import InstancePool  # noqa: E402
from balatro_bot.policies.scripted import ScriptedPolicy  # noqa: E402


def play_one(c: BalatroClient, policy, seed: str) -> dict:
    c.menu()
    gs = c.start("BLACK", "GOLD", seed)
    per_method: dict[str, list[float]] = defaultdict(list)
    actions = 0
    prompt_chars = []
    t_run = time.perf_counter()
    while gs["state"] != "GAME_OVER" and actions < 2000:
        method, params = policy.act(gs)
        if method in ("play", "discard", "buy", "pack"):
            prompt_chars.append(len(json.dumps(gs)))
        t = time.perf_counter()
        try:
            gs = c.call(method, params)
        except RPCError as e:
            if method == "buy":  # e.g. not enough money after all; leave the shop
                gs = c.call("next_round")
            else:
                raise RuntimeError(f"{e} in state {gs['state']}") from e
        per_method[method].append(time.perf_counter() - t)
        actions += 1
    return {
        "seed": seed,
        "won": bool(gs.get("won")),
        "ante": gs.get("ante_num"),
        "round": gs.get("round_num"),
        "actions": actions,
        "game_seconds": time.perf_counter() - t_run,
        "per_method": {k: (len(v), sum(v)) for k, v in per_method.items()},
        "mean_state_chars": statistics.mean(prompt_chars) if prompt_chars else 0,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", type=int, default=5)
    args = ap.parse_args()
    t0 = time.perf_counter()
    with InstancePool(1) as pool:
        boot = time.perf_counter() - t0
        c = BalatroClient(pool.ports[0])
        results = [play_one(c, ScriptedPolicy(), f"TIME{i:04d}") for i in range(args.runs)]
    agg: dict[str, list[float]] = defaultdict(lambda: [0, 0.0])
    for r in results:
        print(f"{r['seed']}: ante {r['ante']} round {r['round']} won={r['won']} actions={r['actions']} "
              f"game_time={r['game_seconds']:.1f}s state_chars~{r['mean_state_chars']:.0f}")
        for m, (n, s) in r["per_method"].items():
            agg[m][0] += n
            agg[m][1] += s
    print(f"\nboot: {boot:.1f}s")
    print("per-action game time (mean seconds):")
    for m, (n, s) in sorted(agg.items(), key=lambda kv: -kv[1][1]):
        print(f"  {m:12s} n={n:4d}  mean={s / n:.3f}s  total={s:.1f}s")
    out = Path("runtime/logs/timing.json")
    out.write_text(json.dumps({"boot": boot, "runs": results}, indent=1))


if __name__ == "__main__":
    main()
