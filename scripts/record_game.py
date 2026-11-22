"""Play one game with a non-LLM player and write a game record for viewer/index.html.

  uv run python scripts/record_game.py --policy search --seed EYEQG8GT            # exact engine
  uv run python scripts/record_game.py --policy smart --backend jackdaw --n 5     # 5 dev seeds, fast
  uv run python scripts/record_game.py --policy search --assist-money 60 --assist-chips   # a full-length run

Players: smart (jackdaw's smart agent), search (balatro_bot.search, its per-candidate estimates are
recorded like a tool call), random (jackdaw's RandomPolicy). Decisions are made on a jackdaw copy of the
game and sent as balatrobot requests to the backend: the exact engine (real description text) by
default, or jackdaw itself. Records go to runtime/records/.
"""

from __future__ import annotations

import argparse
import random
import sys
import time
import zlib
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balatro_bot.agent.recorder import GameRecorder, versions  # noqa: E402
from balatro_bot.agent.scoring_trace import trace_play  # noqa: E402
from balatro_bot.engine.request_states import accepted  # noqa: E402
from balatro_bot.validation.diff import first_diff, rules_view  # noqa: E402

# Not compared: the round summary (only cash_out happens there) and the end of the game. After Amber
# Acorn is beaten the game keeps the jokers face down on the round summary, while jackdaw turns them
# face up at once; the next comparison, in the shop, shows whether that ever matters.
UNCHECKED_STATES = ("ROUND_EVAL", "GAME_OVER")


class Drift(Exception):
    """jackdaw's copy disagreed with the real game; `view` is the real game's response."""

    def __init__(self, message: str, view: dict):
        super().__init__(message)
        self.view = view
from balatro_bot.eval.seeds import seeds  # noqa: E402


def _action_str(a) -> str:
    return f"{type(a).__name__}{tuple(a.card_indices) if hasattr(a, 'card_indices') else ''}"


def record(seed: str, policy: str, backend: str, deck: str, stake: str, out: Path, max_steps: int,
           assist_money: int = 0, assist_chips: bool = False) -> dict:
    from jackdaw.bridge.lockstep import RandomPolicy
    from jackdaw.cli.smart_agent import smart_agent

    from balatro_bot.search.round_search import RoundSearchPolicy
    from balatro_bot.sim.jackdaw_fixes import FixedSimBackend

    jd = FixedSimBackend(describe=backend == "jackdaw")  # the decision-maker's copy (corrected jackdaw)
    eng = None
    if backend == "engine":
        from balatro_bot.engine.client import EngineClient

        eng = EngineClient()
    rng = random.Random(seed)
    search = RoundSearchPolicy(seed=zlib.crc32(seed.encode())) if policy == "search" else None
    rand = RandomPolicy() if policy == "random" else None

    def send(method, params):
        """Send to the decision-maker's jackdaw copy and the backend. With the engine, the copy is
        compared with the real game after every request: if they disagree (a jackdaw rule bug), the
        game stops with that difference as its error rather than play on from a wrong state."""
        b = jd.handle(method, params)
        if not eng:
            return b
        real = eng.call(method, params)
        if real.get("state") in UNCHECKED_STATES or real.get("won"):
            return real  # nothing is decided here; the next request's comparison catches real drift
        diff = first_diff(rules_view(real), rules_view(b), labels=("game", "jackdaw"))
        if diff:
            raise Drift(f"jackdaw drifted from the game after {method}: {diff}"[:500], real)
        return real

    t0 = time.perf_counter()
    rec = GameRecorder(out, seed=seed, deck=deck, stake=stake, backend=backend,
                       player={"kind": "policy", "label": policy, "assist_money": assist_money,
                               "assist_chips": assist_chips}, versions=versions())
    try:
        if eng:
            eng.menu()
        view = send("start", {"deck": deck, "stake": stake, "seed": seed})
        rec.step("start", {"deck": deck, "stake": stake, "seed": seed}, result=view)
        topped = False
        for _ in range(max_steps):
            if view["state"] == "GAME_OVER" or view.get("won"):
                break
            if assist_money and view["state"] == "SHOP" and not topped and view["money"] < assist_money:
                topped = True
                view = send("set", {"money": assist_money})
                rec.step("set", {"money": assist_money}, result=view, actor="assist")
                continue
            if view["state"] != "SHOP":
                topped = False
            rnd = view.get("round") or {}
            target = next((b.get("score") or 0 for b in (view.get("blinds") or {}).values()
                           if b.get("status") == "CURRENT"), 0)
            if assist_chips and view["state"] == "SELECTING_HAND" and rnd.get("hands_left") == 1 \
                    and target - 1 > rnd.get("chips", 0):  # one more hand clears it: score set just short
                view = send("set", {"chips": target - 1})
                rec.step("set", {"chips": target - 1}, result=view, actor="assist")
                continue
            g = jd.game
            t = time.perf_counter()
            # only actions the real game accepts in this state (jackdaw allows more, e.g. using a card
            # on the round summary)
            def ok(x, state=view["state"]) -> bool:
                r = jd.request_for(x)
                return r is not None and accepted(r[0], state)

            legal = [x for x in g.legal_actions() if ok(x)]
            if search:
                action = search.act(g)
                if not ok(action):
                    action = smart_agent(g, legal)
            elif rand:
                action = rand.choose(g, legal, rng, jd.view())
            else:
                action = smart_agent(g, legal)
            decision = {"policy": policy, "seconds": round(time.perf_counter() - t, 3), "tool_calls": [],
                        "reasoning": []}
            if search and search.last_eval:
                decision["tool_calls"].append({"name": "round_search", "arguments": {}, "result": {"results": [
                    {"action": _action_str(r["action"]), "clear_chance": r["p_clear"], "mean_progress": r["progress"],
                     "worlds": r["worlds"]} for r in search.last_eval]}})
            req = jd.request_for(action)
            if req is None:
                rec.note(f"{_action_str(action)} has no balatrobot request; stopping", decision)
                break
            method, params = req
            scoring = trace_play(g, (params or {}).get("cards") or []) if method == "play" else None
            chips_before = (view.get("round") or {}).get("chips")
            t = time.perf_counter()
            try:
                view = send(method, params or None)
            except Drift as d:  # record the step as the real game played it, then stop
                rec.step(method, params or {}, result=d.view, actor="policy", decision=decision,
                         seconds=time.perf_counter() - t, scoring=scoring)
                rec.note(str(d), kind="drift")
                raise
            chips_after = (view.get("round") or {}).get("chips")
            if scoring is not None and chips_before is not None and chips_after is not None \
                    and abs(scoring["total"] - (chips_after - chips_before)) > 1e-6 * max(1.0, abs(chips_after)):
                scoring = {**scoring, "matches_game": False, "game_total": chips_after - chips_before}
            rec.step(method, params or {}, result=view, actor="policy", decision=decision,
                     seconds=time.perf_counter() - t, scoring=scoring)
        summary = {"won": bool(view.get("won")), "ante": view.get("ante_num"), "round": view.get("round_num"),
                   "error": None, "seconds": round(time.perf_counter() - t0, 1)}
    except Exception as e:  # noqa: BLE001  keep what was recorded
        summary = {"won": False, "ante": None, "round": None, "error": f"{type(e).__name__}: {e}",
                   "seconds": round(time.perf_counter() - t0, 1)}
    finally:
        if eng:
            eng.close()
    rec.end(**summary)
    return summary


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--policy", default="search", choices=["search", "smart", "random"])
    ap.add_argument("--backend", default="engine", choices=["engine", "jackdaw"])
    ap.add_argument("--seed", default=None, help="one seed (default: the first --n dev seeds)")
    ap.add_argument("--n", type=int, default=1)
    ap.add_argument("--deck", default="BLACK")
    ap.add_argument("--stake", default="GOLD")
    ap.add_argument("--assist-money", type=int, default=0, help="top money up to this in each shop (longer runs)")
    ap.add_argument("--assist-chips", action="store_true",
                    help="before the last hand of a blind, set the score just short of the target (reach ante 8)")
    ap.add_argument("--max-steps", type=int, default=3000)
    ap.add_argument("--out-dir", type=Path, default=Path("runtime/records"))
    args = ap.parse_args()
    for seed in [args.seed] if args.seed else seeds("dev", args.n):
        out = args.out_dir / f"{args.policy}_{args.deck}_{args.stake}_{seed}.jsonl"
        s = record(seed, args.policy, args.backend, args.deck, args.stake, out, args.max_steps, args.assist_money,
                   args.assist_chips)
        print(f"{seed}: ante {s['ante']} won={s['won']} in {s['seconds']}s -> {out}"
              + (f"  ERROR {s['error']}" if s["error"] else ""))


if __name__ == "__main__":
    main()
