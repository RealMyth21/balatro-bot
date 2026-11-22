"""Evaluate non-LLM policies on jackdaw in parallel and report win rates.

  uv run python scripts/eval_fast.py --policy smart search --split dev --n 500 --workers 6
  uv run python scripts/eval_fast.py --report-only        # summarize what's already in the results file

Results: runtime/eval/fast_<deck>_<stake>.jsonl (resumable). Use --split heldout only for final numbers.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balatro_bot.eval.fast_eval import run  # noqa: E402
from balatro_bot.eval.report import format_paired, format_summary, label, load, paired, summarize  # noqa: E402
from balatro_bot.eval.seeds import seeds  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--policy", nargs="+", default=["smart", "search"], choices=["smart", "search", "random"])
    ap.add_argument("--split", default="dev", choices=["dev", "heldout", "smoke"])
    ap.add_argument("--n", type=int, default=200)
    ap.add_argument("--deck", default="BLACK")
    ap.add_argument("--stake", default="GOLD")
    ap.add_argument("--workers", type=int, default=None)
    ap.add_argument("--worlds", type=int, default=16, help="search: sampled worlds per decision")
    ap.add_argument("--exhaustive", action="store_true", help="search: no adaptive pruning of candidates")
    ap.add_argument("--out", type=Path, default=None)
    ap.add_argument("--report-only", action="store_true")
    args = ap.parse_args()
    out = args.out or Path(f"runtime/eval/fast_{args.deck.lower()}_{args.stake.lower()}.jsonl")
    seed_list = seeds(args.split, args.n)
    if not args.report_only:
        for pol in args.policy:
            opts = {"worlds": args.worlds, "adaptive": not args.exhaustive} if pol == "search" else {}
            run(pol, seed_list, out, args.deck, args.stake, args.workers, opts)
    wanted = set(seed_list)
    rows = [r for r in load(out) if r["seed"] in wanted and r["deck"] == args.deck and r["stake"] == args.stake]
    by_pol: dict[str, list[dict]] = {}
    for r in rows:
        by_pol.setdefault(label(r), []).append(r)
    print(f"\n{args.deck}/{args.stake}, {args.split} seeds (first {args.n}):")
    for pol, rs in sorted(by_pol.items()):
        print("  " + format_summary(pol, summarize(rs)))
    pols = sorted(by_pol)
    for i in range(len(pols)):
        for j in range(i + 1, len(pols)):
            print("  " + format_paired(pols[i], pols[j], paired(by_pol[pols[i]], by_pol[pols[j]])))


if __name__ == "__main__":
    main()
