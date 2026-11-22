"""Differential test: jackdaw (fast Rust sim) vs our exact engine (the real game's Lua).

Both receive identical BalatroBot requests; the full gamestate is compared after every action.
Actions come from jackdaw's own policies so runs get deep into the game:
  smart     - jackdaw's SmartPolicy (exact best plays, sensible shop/pack choices, some exploration)
  random    - jackdaw's RandomPolicy (random kinds of moves, half the hands played well)
  collector - content coverage: buys/opens/uses everything it can (see CollectorPolicy)

  .venv/bin/python scripts/compare_jackdaw.py --seeds 40 --workers 4 --policy smart
  .venv/bin/python scripts/compare_jackdaw.py --seeds 40 --workers 4 --policy random --deck BLACK --stake GOLD
  .venv/bin/python scripts/compare_jackdaw.py --seeds 100 --policy collector --assist-money 100 --assist-chips

The collector policy (below) buys, opens, uses and sells as much as it can and sometimes skips blinds,
so rare content comes up through the game's own shops, packs and tags; hands are played by SmartPolicy.

Writes every divergence (seed, step, action, first differing field) to runtime/logs/jackdaw_diffs.jsonl,
and reports which game content the run exercised (jokers in play, bosses, tags, vouchers, consumables,
boosters, editions, enhancements, seals, stickers); --coverage-out accumulates that across runs.
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
from balatro_bot.validation import coverage  # noqa: E402
from balatro_bot.validation.diff import deep_diff, first_diff, rules_view  # noqa: E402

_local = threading.local()
_engines: list[EngineClient] = []
_engines_lock = threading.Lock()

def diff_views(engine_view: dict, jackdaw_view: dict) -> str | None:
    return first_diff(rules_view(engine_view), rules_view(jackdaw_view), labels=("engine", "jackdaw"))


def engine() -> EngineClient:
    """One engine per worker thread (it restarts itself if it ever dies); closed at the end of main."""
    if not hasattr(_local, "eng"):
        _local.eng = EngineClient()
        with _engines_lock:
            _engines.append(_local.eng)
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


class CollectorPolicy:
    """Exercises as much content as possible through ordinary game actions: uses consumables, takes
    pack cards, redeems vouchers, buys cards and packs, rerolls a little, sometimes sells a joker to
    make room and sometimes skips a blind (for tags). Hands are played by jackdaw's SmartPolicy. Pair
    it with --assist-money so the shop purchases are affordable."""

    def __init__(self):
        from jackdaw.bridge.lockstep import RandomPolicy, SmartPolicy

        self.smart, self.random = SmartPolicy(), RandomPolicy()
        self.rerolls = 0

    def choose(self, game, legal, rng: random.Random, view: dict):
        by: dict[str, list] = {}
        for act in legal:
            by.setdefault(type(act).__name__, []).append(act)

        def pick(name: str):
            return self.random.concretize(game, rng.choice(by[name]), rng, view)

        if by.get("UseConsumable") and rng.random() < 0.8:
            return pick("UseConsumable")
        if by.get("PickPackCard"):
            return pick("PickPackCard")
        if by.get("SelectBlind"):
            return by["SkipBlind"][0] if by.get("SkipBlind") and rng.random() < 0.3 else by["SelectBlind"][0]
        if by.get("NextRound"):  # in the shop
            for name in ("RedeemVoucher", "BuyCard", "BuyAndUse", "OpenBooster"):
                if by.get(name):
                    return pick(name)
            jokers = ((view.get("jokers") or {}).get("cards")) or []
            if by.get("SellCard") and len(jokers) >= 4 and rng.random() < 0.3:
                return pick("SellCard")
            if by.get("Reroll") and self.rerolls < 2:
                self.rerolls += 1
                return by["Reroll"][0]
            self.rerolls = 0
            return by["NextRound"][0]
        return self.smart.choose(game, legal, rng, view)


def run_seed(seed: str, deck: str, stake: str, policy_name: str, max_steps: int,
             assist_money: int = 0, assist_chips: bool = False, start_ante: int = 0,
             deep: bool = True) -> dict:
    from jackdaw.bridge.backend import SimBackend
    from jackdaw.bridge.lockstep import RandomPolicy, SmartPolicy

    eng = engine()
    jd = SimBackend()
    rng = random.Random(seed)
    policy = {"smart": SmartPolicy, "random": RandomPolicy, "collector": CollectorPolicy}[policy_name]()

    eng.menu()
    a = eng.start(deck, stake, seed)
    b = jd.handle("start", {"deck": deck, "stake": stake, "seed": seed})
    last = None
    trace: list = []
    max_ante = 1
    cov = coverage.new()

    def done(result: dict) -> dict:
        return {**result, "coverage": coverage.to_json(cov)}

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
                return done({"seed": seed, "result": "diverged", "step": step, "last_action": last,
                             "diff": f"win state (won, ante, money, chips): engine={ka} jackdaw={kb}",
                             "ante": a.get("ante_num"), "state": a.get("state"), "trace": trace})
            return done({"seed": seed, "result": "won", "step": step, "ante": a.get("ante_num")})
        d = diff_views(a, b)
        if not d and deep:
            d = deep_diff(eng.call("engine_debug_state"), jd.game)
        if d:
            return done({"seed": seed, "result": "diverged", "step": step, "last_action": last, "diff": d,
                         "ante": a.get("ante_num"), "state": a.get("state"), "trace": trace})
        if a["state"] == "GAME_OVER" or a.get("won"):
            return done({"seed": seed, "result": "won" if a.get("won") else "lost", "step": step,
                         "ante": a.get("ante_num")})
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
            return done({"seed": seed, "result": "no_request", "step": step, "ante": a.get("ante_num")})
        method, params = req
        last = [method, params]
        trace.append(last)
        a2, ea = call(eng.call, method, params or None)
        b2, eb = call(jd.handle, method, params or None)
        if ea or eb:
            if ea != eb:
                return done({"seed": seed, "result": "diverged", "step": step, "last_action": last,
                             "diff": f"error mismatch: engine={ea} jackdaw={eb}", "ante": a.get("ante_num"),
                             "state": a.get("state"), "trace": trace})
            a2, b2 = eng.call("gamestate"), jd.handle("gamestate", None)
        else:
            coverage.record(cov, a, method, params, a2)
        a, b = a2, b2
    d = diff_views(a, b)  # the state after the last action
    if d:
        return done({"seed": seed, "result": "diverged", "step": max_steps, "last_action": last, "diff": d,
                     "ante": a.get("ante_num"), "state": a.get("state"), "trace": trace})
    return done({"seed": seed, "result": "max_steps", "step": max_steps, "ante": a.get("ante_num")})


def run_seed_safe(seed: str, *args, **kw) -> dict:
    """run_seed, with a crash in either engine reported as a result instead of stopping the run."""
    try:
        return run_seed(seed, *args, **kw)
    except (KeyboardInterrupt, SystemExit):
        raise
    except BaseException as e:  # noqa: BLE001  incl. pyo3 PanicException from jackdaw
        return {"seed": seed, "result": "crashed", "step": 0, "diff": f"{type(e).__name__}: {e}"[:500],
                "coverage": {}}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--seeds", type=int, default=20)
    ap.add_argument("--start", type=int, default=0)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--policy", choices=["smart", "random", "collector"], default="smart")
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
    ap.add_argument("--coverage-out", type=Path, default=None,
                    help="JSON file to merge this run's content coverage into (cumulative across runs)")
    args = ap.parse_args()

    tag = ("C" if args.policy == "collector" else "A" if (args.assist_money or args.assist_chips)
           else args.policy[0].upper())
    seeds = [f"JD{tag}{i:05d}" for i in range(args.start, args.start + args.seeds)]
    t0 = time.perf_counter()
    results = []
    args.out.parent.mkdir(parents=True, exist_ok=True)
    cov = coverage.new()
    with ThreadPoolExecutor(args.workers) as pool, args.out.open("a") as fh:
        futs = [pool.submit(run_seed_safe, s, args.deck, args.stake, args.policy, args.max_steps,
                            args.assist_money, args.assist_chips, args.start_ante, not args.no_deep)
                for s in seeds]
        for f in futs:
            r = f.result()
            for k, v in (r.pop("coverage", None) or {}).items():
                cov[k].update(v)
            r.update(policy=args.policy, deck=args.deck, stake=args.stake, assist_money=args.assist_money,
                     assist_chips=args.assist_chips, start_ante=args.start_ante)
            results.append(r)
            if r["result"] in ("diverged", "crashed"):
                fh.write(json.dumps(r) + "\n")
                fh.flush()
            tag = r["result"].upper() if r["result"] in ("diverged", "crashed") else r["result"]
            extra = (f" after {r.get('last_action')} -> {r['diff']}" if r["result"] == "diverged"
                     else f" -> {r['diff']}" if r["result"] == "crashed" else "")
            print(f"{r['seed']}: {tag} at step {r['step']} (ante {r.get('ante')}){extra}", flush=True)

    counts = Counter(r["result"] for r in results)
    steps = sum(r["step"] for r in results)
    print(f"\n{len(results)} seeds, {steps} steps compared in {time.perf_counter() - t0:.0f}s "
          f"({args.deck}/{args.stake}, policy={args.policy}): {dict(counts)}")
    print(f"max ante reached: {max((r.get('ante') or 0) for r in results)}; "
          f"antes: {sorted(Counter(r.get('ante') for r in results).items(), key=lambda t: t[0] or 0)}")
    with _engines_lock:
        for e in _engines:
            e.close()
    if args.coverage_out:
        if args.coverage_out.exists():
            for k, v in json.loads(args.coverage_out.read_text()).items():
                cov.setdefault(k, set()).update(v)
        args.coverage_out.parent.mkdir(parents=True, exist_ok=True)
        args.coverage_out.write_text(json.dumps(coverage.to_json(cov), indent=1))
    print("\ncontent exercised" + (f" (cumulative, {args.coverage_out})" if args.coverage_out else "") + ":")
    for line in coverage.report(cov):
        print("  " + line)


if __name__ == "__main__":
    main()
