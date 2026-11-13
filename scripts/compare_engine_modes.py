"""Regression test for engine speed patches: fast engine vs unpatched engine (ENGINE_FAST=0).

Both must produce identical states for the same seeds and actions. Fast to run (no real game).

  .venv/bin/python scripts/compare_engine_modes.py --seeds 20 --policy mixed
"""

from __future__ import annotations

import argparse
import os
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from balatro_bot.engine.client import EngineClient  # noqa: E402
from balatro_bot.game.client import RPCError  # noqa: E402
from balatro_bot.policies.mixed import MixedPolicy  # noqa: E402
from balatro_bot.policies.random_policy import RandomPolicy  # noqa: E402
from balatro_bot.policies.scripted import ScriptedPolicy  # noqa: E402
from balatro_bot.validation.diff import first_diff, normalize  # noqa: E402


def make_engine(fast: bool) -> EngineClient:
    old = os.environ.get("ENGINE_FAST")
    os.environ["ENGINE_FAST"] = "1" if fast else "0"
    try:
        return EngineClient()
    finally:
        if old is None:
            os.environ.pop("ENGINE_FAST", None)
        else:
            os.environ["ENGINE_FAST"] = old


def step(c, m, p):
    try:
        return c.call(m, p), None
    except RPCError as e:
        return None, e.name


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--policy", choices=["scripted", "random", "mixed"], default="mixed")
    ap.add_argument("--max-actions", type=int, default=800)
    args = ap.parse_args()
    factories = {
        "scripted": lambda s: ScriptedPolicy(),
        "random": lambda s: RandomPolicy(rng=random.Random(s)),
        "mixed": lambda s: MixedPolicy(rng=random.Random(s)),
    }
    fast, slow = make_engine(True), make_engine(False)
    ok = total = 0
    for i in range(args.seeds):
        seed = f"CMP{i:04d}"
        fast.menu(), slow.menu()
        a, b = fast.start("BLACK", "GOLD", seed), slow.start("BLACK", "GOLD", seed)
        pol = factories[args.policy](seed)
        diff = None
        n = 0
        for n in range(args.max_actions):  # noqa: B007 (n is the action count after the loop)
            diff = first_diff(normalize(a), normalize(b), labels=("fast", "slow"))
            if diff or a["state"] == "GAME_OVER" or a.get("won"):
                break
            m, p = pol.act(a)
            a2, ea = step(fast, m, p)
            b2, eb = step(slow, m, p)
            if ea or eb:
                if ea != eb:
                    diff = f"action {m} {p}: fast error={ea} slow error={eb}"
                    break
                fb = ("next_round", None) if a["state"] == "SHOP" else ("pack", {"skip": True})
                a2, ea = step(fast, *fb)
                b2, eb = step(slow, *fb)
                if ea != eb:
                    diff = f"fallback {fb}: fast error={ea} slow error={eb}"
                    break
                if a2 is None:
                    a2, b2 = fast.call("gamestate"), slow.call("gamestate")
            a, b = a2, b2
        total += n
        if diff:
            print(f"{seed}: MISMATCH after {n} actions -> {diff}")
        else:
            ok += 1
            print(f"{seed}: identical for {n} actions (ante {a.get('ante_num')})")
    fast.close(), slow.close()
    print(f"\n{ok}/{args.seeds} identical ({total} actions, policy={args.policy})")
    sys.exit(0 if ok == args.seeds else 1)


if __name__ == "__main__":
    main()
