"""Differential test: jackdaw (fast Rust sim) vs our exact engine (the real game's Lua).

Both receive identical BalatroBot requests; the full gamestate is compared after every action.
Actions come from jackdaw's own policies so runs get deep into the game:
  smart  - jackdaw's SmartPolicy (exact best plays, sensible shop/pack choices, some exploration)
  random - jackdaw's RandomPolicy (random kinds of moves, half the hands played well)

  .venv/bin/python scripts/compare_jackdaw.py --seeds 40 --workers 4 --policy smart
  .venv/bin/python scripts/compare_jackdaw.py --seeds 40 --workers 4 --policy random --deck BLACK --stake GOLD

Writes every divergence (seed, step, action, first differing field) to runtime/logs/jackdaw_diffs.jsonl.
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))


from balatro_bot.engine.client import EngineClient  # noqa: E402
from balatro_bot.validation.diff import deep_diff, first_diff, rules_view  # noqa: E402

_local = threading.local()

def diff_views(engine_view: dict, jackdaw_view: dict) -> str | None:
    return first_diff(rules_view(engine_view), rules_view(jackdaw_view), labels=("engine", "jackdaw"))


def engine() -> EngineClient:
    if not hasattr(_local, "eng"):
        _local.eng = EngineClient()
    return _local.eng


def call(backend, method, params):
    try:
        return backend(method, params), None
    except Exception as e:  # noqa: BLE001
        name = getattr(e, "name", None) or (getattr(e, "data", {}) or {}).get("name") or type(e).__name__
        return None, name


def current_target(gs: dict) -> int:
    for bl in (gs.get("blinds") or {}).values():
        if bl.get("status") == "CURRENT":
            return int(bl.get("score") or 0)
    return 0


def run_seed(seed: str, deck: str, stake: str, policy_name: str, max_steps: int,
             assist_money: int = 0, assist_chips: bool = False, start_ante: int = 0,
             deep: bool = True) -> dict:
    from jackdaw.bridge.backend import SimBackend
    from jackdaw.bridge.lockstep import RandomPolicy, SmartPolicy

    eng = engine()
    jd = SimBackend()
    rng = random.Random(seed)
    policy = SmartPolicy() if policy_name == "smart" else RandomPolicy()

    eng.menu()
    a = eng.start(deck, stake, seed)
    b = jd.handle("start", {"deck": deck, "stake": stake, "seed": seed})
    last = None
    trace: list = []
    max_ante = 1

    def both(method, params):
        nonlocal a, b, last
        last = [method, params]
        trace.append(last)
        a, b = eng.call(method, params), jd.handle(method, params)

    if start_ante > 1:
        both("set", {"ante": start_ante})
    shop_topped = False
    for step in range(max_steps):
        max_ante = max(max_ante, a.get("ante_num") or 1)
        if a.get("won") or b.get("won"):
            # The real game pauses on the win screen before the last boss's cleanup events run
            # (debuffs, face-down jokers); only the outcome is compared at this terminal state.
            ka = (a.get("won"), a.get("ante_num"), a.get("money"), (a.get("round") or {}).get("chips"))
            kb = (b.get("won"), b.get("ante_num"), b.get("money"), (b.get("round") or {}).get("chips"))
            if ka != kb:
                return {"seed": seed, "result": "diverged", "step": step, "last_action": last,
                        "diff": f"win state (won, ante, money, chips): engine={ka} jackdaw={kb}",
                        "ante": a.get("ante_num"), "state": a.get("state"), "trace": trace}
            return {"seed": seed, "result": "won", "step": step, "ante": a.get("ante_num")}
        d = diff_views(a, b)
        if not d and deep:
            d = deep_diff(eng.call("engine_debug_state"), jd.game)
        if d:
            return {"seed": seed, "result": "diverged", "step": step, "last_action": last, "diff": d,
                    "ante": a.get("ante_num"), "state": a.get("state"), "trace": trace}
        if a["state"] == "GAME_OVER" or a.get("won"):
            return {"seed": seed, "result": "won" if a.get("won") else "lost", "step": step,
                    "ante": a.get("ante_num")}
        # late-game assists (identical requests to both engines)
        if a["state"] != "SHOP":
            shop_topped = False
        if assist_money and a["state"] == "SHOP" and not shop_topped:
            shop_topped = True
            if a["money"] < assist_money:
                both("set", {"money": assist_money})
                continue
        if assist_chips and a["state"] == "SELECTING_HAND" and (a.get("round") or {}).get("hands_left") == 1:
            need = current_target(a) - 1
            if need > (a.get("round") or {}).get("chips", 0):
                both("set", {"chips": need})
                continue
        # pick an action that maps to a BalatroBot request
        legal = jd.legal_actions()
        req = None
        for _ in range(20):
            action = policy.choose(jd.game, legal, rng, b)
            req = jd.request_for(action)
            if req is not None:
                break
        if req is None:
            return {"seed": seed, "result": "no_request", "step": step, "ante": a.get("ante_num")}
        method, params = req
        last = [method, params]
        trace.append(last)
        a2, ea = call(eng.call, method, params or None)
        b2, eb = call(jd.handle, method, params or None)
        if ea or eb:
            if ea != eb:
                return {"seed": seed, "result": "diverged", "step": step, "last_action": last,
                        "diff": f"error mismatch: engine={ea} jackdaw={eb}", "ante": a.get("ante_num"),
                        "state": a.get("state"), "trace": trace}
            a2, b2 = eng.call("gamestate"), jd.handle("gamestate", None)
        a, b = a2, b2
    return {"seed": seed, "result": "max_steps", "step": max_steps, "ante": a.get("ante_num")}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--policy", choices=["smart", "random"], default="smart")
    ap.add_argument("--deck", default="BLACK")
    ap.add_argument("--stake", default="GOLD")
    ap.add_argument("--max-steps", type=int, default=1500)
    ap.add_argument("--assist-money", type=int, default=0,
                    help="top money up to this at every shop (both engines), so builds get strong")
    ap.add_argument("--assist-chips", action="store_true",
                    help="before the last hand of a round, set the score to target-1 so runs reach ante 8")
    ap.add_argument("--start-ante", type=int, default=0)
    ap.add_argument("--no-deep", action="store_true", help="skip RNG-stream and hidden-ability checks")
    ap.add_argument("--out", type=Path, default=Path("runtime/logs/jackdaw_diffs.jsonl"))
    args = ap.parse_args()

    tag = "A" if (args.assist_money or args.assist_chips) else args.policy[0].upper()
    seeds = [f"JD{tag}{i:05d}" for i in range(args.start, args.start + args.seeds)]
    t0 = time.perf_counter()
    results = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(args.workers) as pool, args.out.open("a") as fh:
        futs = [pool.submit(run_seed, s, args.deck, args.stake, args.policy, args.max_steps,
                            args.assist_money, args.assist_chips, args.start_ante, not args.no_deep)
                for s in seeds]
        for f in futs:
            r = f.result()
            r.update(policy=args.policy, deck=args.deck, stake=args.stake, assist_money=args.assist_money,
                     assist_chips=args.assist_chips, start_ante=args.start_ante)
            results.append(r)
            if r["result"] == "diverged":
                fh.write(json.dumps(r) + "\n")
                fh.flush()
            tag = r["result"].upper() if r["result"] == "diverged" else r["result"]
            extra = f" after {r.get('last_action')} -> {r['diff']}" if r["result"] == "diverged" else ""
            print(f"{r['seed']}: {tag} at step {r['step']} (ante {r.get('ante')}){extra}", flush=True)

    counts = Counter(r["result"] for r in results)
    steps = sum(r["step"] for r in results)
    print(f"\n{len(results)} seeds, {steps} steps compared in {time.perf_counter() - t0:.0f}s "
          f"({args.deck}/{args.stake}, policy={args.policy}): {dict(counts)}")
    print(f"max ante reached: {max((r.get('ante') or 0) for r in results)}; "
          f"antes: {sorted(Counter(r.get('ante') for r in results).items())}")


if __name__ == "__main__":
    main()
