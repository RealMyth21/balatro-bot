"""Replay a recorded divergence (runtime/logs/jackdaw_diffs.jsonl) on our engine and on jackdaw,
printing the key state after each of the last N steps and the full diff at the end.

  .venv/bin/python scripts/replay_divergence.py JDS00014 [--last 6]
  .venv/bin/python scripts/replay_divergence.py JDA02034 --real fast     # also on the real game
  .venv/bin/python scripts/replay_divergence.py JDA02034 --real normal   # ... at normal game timing

--real settles which side is right: it replays the same requests on a headless real game (bot
profile, isolated mods; macOS) in balatrobot's fast mode or at normal timing.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from balatro_bot.engine.client import EngineClient  # noqa: E402
from balatro_bot.game.client import RPCError, settled  # noqa: E402
from balatro_bot.sim.jackdaw_backend import JackdawClient  # noqa: E402
from balatro_bot.validation.diff import first_diff, rules_view  # noqa: E402


def brief(gs: dict) -> str:
    r = gs.get("round") or {}
    boss = (gs.get("blinds") or {}).get("boss", {}).get("name")
    cur = [b.get("name") for b in (gs.get("blinds") or {}).values() if b.get("status") == "CURRENT"]
    jok = [j["key"] + ("[R]" if (j.get("modifier") or {}).get("rental") else "")
           for j in (gs.get("jokers") or {}).get("cards", [])]
    return (f"{gs.get('state'):<20} ante {gs.get('ante_num')} ${gs.get('money')} chips {r.get('chips')} "
            f"hands {r.get('hands_left')} disc {r.get('discards_left')} "
            f"hand_limit {(gs.get('hand') or {}).get('limit')} "
            f"blind {cur} boss {boss} jokers {jok}")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("seed")
    ap.add_argument("--last", type=int, default=6)
    ap.add_argument("--file", type=Path, default=Path("runtime/logs/jackdaw_diffs.jsonl"))
    ap.add_argument("--debug", action="store_true")
    ap.add_argument("--real", choices=["fast", "normal"], default=None,
                    help="also replay on the real game (balatrobot fast mode or normal timing)")
    args = ap.parse_args()
    rec = next(json.loads(line) for line in args.file.open() if json.loads(line)["seed"] == args.seed)
    trace = rec["trace"]
    print(f"{args.seed}: {rec['deck']}/{rec['stake']} diverged at step {rec['step']}: {rec['diff']}")
    with contextlib.ExitStack() as stack:
        backends = [("engine", stack.enter_context(EngineClient(debug=args.debug)), False),
                    ("jackdaw", JackdawClient(), False)]
        if args.real:
            from balatro_bot.game.client import BalatroClient
            from balatro_bot.game.launcher import InstancePool

            pool = stack.enter_context(InstancePool(1, fast=args.real == "fast"))
            backends.append((f"real-{args.real}", BalatroClient(port=pool.ports[0]), True))
        width = max(len(n) for n, _, _ in backends)
        for _, c, _ in backends:
            c.call("menu")
        outs = [c.call("start", {"deck": rec["deck"], "stake": rec["stake"], "seed": args.seed})
                for _, c, _ in backends]
        for i, (m, p) in enumerate(trace):
            show = i >= len(trace) - args.last
            if show:
                print(f"\nstep {i}: {m} {p}")
            outs = []
            for name, c, real in backends:
                try:
                    out = c.call(m, p or None)
                except RPCError as e:
                    print(f"  {name} ERROR {e.name}: {e.message}")
                    out = c.call("gamestate")
                outs.append(settled(c, out, polls=200) if real else out)
            if show:
                for (name, _, _), out in zip(backends, outs, strict=True):
                    print(f"  {name:<{width}}:", brief(out))
        a = outs[0]
        for (name, _, _), out in zip(backends[1:], outs[1:], strict=True):
            d = first_diff(rules_view(a), rules_view(out), labels=("engine", name))
            print(f"\nfirst diff engine vs {name}:", d)


if __name__ == "__main__":
    main()
