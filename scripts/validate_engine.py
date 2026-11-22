"""Differential test: the headless engine must match the real game action-for-action.

Plays the same seeds with the same deterministic policy on the real game (balatrobot) and on
the engine, and compares the full game state after every action.

  .venv/bin/python scripts/validate_engine.py --seeds 10
  .venv/bin/python scripts/validate_engine.py --seeds 10 --policy random   # broader coverage

Requires Steam running (the real game is launched headless).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balatro_bot.engine.client import EngineClient  # noqa: E402
from balatro_bot.game.client import BalatroClient, RPCError, settled  # noqa: E402
from balatro_bot.game.launcher import InstancePool  # noqa: E402
from balatro_bot.policies.mixed import MixedPolicy  # noqa: E402
from balatro_bot.policies.random_policy import RandomPolicy  # noqa: E402
from balatro_bot.policies.scripted import ScriptedPolicy  # noqa: E402
from balatro_bot.validation.diff import first_diff, normalize  # noqa: E402


def step(client, method, params, settle=False):
    try:
        r = client.call(method, params)
        return (settled(client, r) if settle else r), None
    except RPCError as e:
        return None, e.name


def compare_seed(real, eng, seed: str, policy_factory, max_actions: int) -> tuple[int, str | None]:
    real.menu()
    eng.menu()
    a = settled(real, real.start("BLACK", "GOLD", seed))
    b = eng.start("BLACK", "GOLD", seed)
    pol = policy_factory(seed)
    for n in range(max_actions):
        d = first_diff(normalize(a), normalize(b), labels=("real", "engine"))
        if d:
            return n, d
        if a["state"] == "GAME_OVER" or (a.get("won") and a["state"] != "SELECTING_HAND"):
            return n, None
        method, params = pol.act(a)
        a2, ea = step(real, method, params, settle=True)
        b2, eb = step(eng, method, params)
        if ea or eb:
            if ea != eb:
                return n, f"action {method} {params}: real error={ea} engine error={eb}"
            # both rejected: leave the sub-state identically on both sides
            fallback = ("next_round", None) if a["state"] == "SHOP" else ("pack", {"skip": True})
            a2, _ = step(real, *fallback, settle=True)
            b2, _ = step(eng, *fallback)
            if a2 is None or b2 is None:
                return n, f"could not recover from rejected {method}"
        a, b = a2, b2
    return max_actions, first_diff(normalize(a), normalize(b), labels=("real", "engine"))  # after the last action


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=10)
    ap.add_argument("--policy", choices=["scripted", "random", "mixed"], default="mixed")
    ap.add_argument("--max-actions", type=int, default=600)
    ap.add_argument("--dump", type=Path, default=Path("runtime/logs/validate_fail.json"))
    args = ap.parse_args()

    if args.policy == "scripted":
        factory = lambda seed: ScriptedPolicy()  # noqa: E731
    elif args.policy == "random":
        factory = lambda seed: RandomPolicy(rng=random.Random(seed))  # noqa: E731
    else:
        factory = lambda seed: MixedPolicy(rng=random.Random(seed))  # noqa: E731

    ok = 0
    total_actions = 0
    with InstancePool(1) as pool, EngineClient() as eng:
        real = BalatroClient(pool.ports[0])
        for i in range(args.seeds):
            seed = f"VAL{i:04d}"
            n, diff = compare_seed(real, eng, seed, factory, args.max_actions)
            total_actions += n
            if diff:
                print(f"{seed}: MISMATCH after {n} actions -> {diff}")
                args.dump.write_text(json.dumps({"seed": seed, "after_actions": n, "diff": diff}, indent=1))
            else:
                ok += 1
                print(f"{seed}: identical for {n} actions")
    print(f"\n{ok}/{args.seeds} seeds identical ({total_actions} actions compared, policy={args.policy})")
    sys.exit(0 if ok == args.seeds else 1)


if __name__ == "__main__":
    main()
