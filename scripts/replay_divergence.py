"""Replay a recorded divergence (runtime/logs/jackdaw_diffs.jsonl) on our engine and on jackdaw,
printing the key state after each of the last N steps and the full diff at the end.

  .venv/bin/python scripts/replay_divergence.py JDS00014 [--last 6]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from balatro_bot.engine.client import EngineClient  # noqa: E402
from balatro_bot.game.client import RPCError  # noqa: E402
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
    args = ap.parse_args()
    rec = next(json.loads(line) for line in args.file.open() if json.loads(line)["seed"] == args.seed)
    trace = rec["trace"]
    print(f"{args.seed}: {rec['deck']}/{rec['stake']} diverged at step {rec['step']}: {rec['diff']}")
    eng, jd = EngineClient(debug=args.debug), JackdawClient()
    eng.menu()
    jd.menu()
    a = eng.start(rec["deck"], rec["stake"], args.seed)
    b = jd.start(rec["deck"], rec["stake"], args.seed)
    for i, (m, p) in enumerate(trace):
        show = i >= len(trace) - args.last
        if show:
            print(f"\nstep {i}: {m} {p}")
        outs = []
        for name, c in (("engine", eng), ("jackdaw", jd)):
            try:
                outs.append(c.call(m, p or None))
            except RPCError as e:
                print(f"  {name} ERROR {e.name}: {e.message}")
                outs.append(c.call("gamestate"))
        a, b = outs
        if show:
            print("  engine :", brief(a))
            print("  jackdaw:", brief(b))
    print("\nfirst diff:", first_diff(rules_view(a), rules_view(b), labels=("engine", "jackdaw")))
    eng.close()


if __name__ == "__main__":
    main()
