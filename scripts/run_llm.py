"""Play Black Deck / Gold Stake games with an LLM through BalatroLLM, many in parallel.

  # dry run of the whole pipeline with a local mock model (no API key, no cost):
  uv run python scripts/run_llm.py --mock --n 4 --parallel 4

  # Kimi K3.1 Pro pilot (needs MOONSHOT_API_KEY in your shell), with a hard budget cap:
  uv run python scripts/run_llm.py --label kimi-notools --n 10 --parallel 4 --max-cost 25
  uv run python scripts/run_llm.py --label kimi-tools --tools --n 10 --parallel 4 --max-cost 40

Games run on our exact engine by default (real description text for the prompt); --backend jackdaw
uses the simulator (text fields are names only). Per-game summaries go to runtime/eval/llm.jsonl and
full BalatroLLM logs to runtime/llm_runs/. Rerunning skips seeds already played under the same label.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balatro_bot.eval.report import format_paired, format_summary, load, paired, summarize  # noqa: E402
from balatro_bot.eval.seeds import seeds  # noqa: E402
from balatro_bot.serve_pool import ServerPool  # noqa: E402

MOONSHOT_URL = "https://api.moonshot.ai/v1"


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default=None, help="name for this configuration in the results")
    ap.add_argument("--model", default="kimi-k3.1-pro")
    ap.add_argument("--base-url", default=MOONSHOT_URL)
    ap.add_argument("--api-key-env", default="MOONSHOT_API_KEY", help="environment variable holding the key")
    ap.add_argument("--tools", action="store_true", help="enable the analysis tools (exact scores, clear odds)")
    ap.add_argument("--backend", default="engine", choices=["engine", "jackdaw"])
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--split", default="dev", choices=["dev", "heldout", "smoke"])
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--deck", default="BLACK")
    ap.add_argument("--stake", default="GOLD")
    ap.add_argument("--strategy", default="default", help="BalatroLLM strategy name")
    ap.add_argument("--model-config", default="{}", help='JSON merged into BalatroLLM\'s request params')
    ap.add_argument("--max-cost", type=float, default=float("inf"), help="stop starting new games after this USD")
    ap.add_argument("--price-in", type=float, default=2.0, help="USD per 1M input tokens")
    ap.add_argument("--price-out", type=float, default=10.0, help="USD per 1M output tokens")
    ap.add_argument("--price-cached", type=float, default=0.2, help="USD per 1M cached input tokens")
    ap.add_argument("--max-steps", type=int, default=2000)
    ap.add_argument("--keep-cache-control", action="store_true", help="send OpenRouter/Anthropic cache hints")
    ap.add_argument("--drop-param", action="append", default=[],
                    help="remove a default request param the provider rejects (e.g. seed, parallel_tool_calls, "
                         "extra_headers); repeatable")
    ap.add_argument("--mock", action="store_true", help="use a local mock model instead of a real API")
    ap.add_argument("--summary", type=Path, default=Path("runtime/eval/llm.jsonl"))
    ap.add_argument("--runs-dir", type=Path, default=Path("runtime/llm_runs"))
    args = ap.parse_args()

    try:
        import balatrollm  # noqa: F401

        from balatro_bot.agent.llm_runner import Prices, run_games
    except ImportError:
        sys.exit("BalatroLLM is not installed: uv sync --extra llm (see README)")

    label = args.label or ("mock" if args.mock else args.model) + ("-tools" if args.tools else "")
    mock_httpd = None
    if args.mock:
        from balatro_bot.agent.mock_llm import start

        mock_httpd, _ = start(0)
        base_url, api_key = f"http://127.0.0.1:{mock_httpd.server_address[1]}/v1", "mock"
    else:
        base_url, api_key = args.base_url, os.environ.get(args.api_key_env, "")
        if not api_key:
            sys.exit(f"set {args.api_key_env} in your shell (the key is read from the environment only)")

    seed_list = seeds(args.split, args.n)
    print(f"{label}: {len(seed_list)} {args.split} seeds, {args.parallel} parallel {args.backend} servers, "
          f"budget ${args.max_cost}")
    try:
        with ServerPool(args.backend, n=args.parallel) as pool:
            out = asyncio.run(run_games(
                ports=pool.ports, seeds=seed_list, model=args.model if not args.mock else "mock-model",
                base_url=base_url, api_key=api_key, runs_dir=args.runs_dir, summary=args.summary, label=label,
                deck=args.deck, stake=args.stake, strategy=args.strategy, tools=args.tools,
                model_config=json.loads(args.model_config),
                prices=Prices(args.price_in, args.price_out, args.price_cached), max_cost=args.max_cost,
                max_steps=args.max_steps, strip_cache_control=not args.keep_cache_control,
                strategies_dir=Path(__file__).resolve().parents[1] / "strategies",
                drop_params=tuple(args.drop_param)))
    finally:
        if mock_httpd:
            mock_httpd.shutdown()
    print(f"\nplayed {out['played']} games, spent ${out['spent_usd']:.2f}"
          + (" (stopped: budget reached)" if out["stopped_on_budget"] else ""))
    wanted = set(seed_list)
    rows = [r for r in load(args.summary) if r["seed"] in wanted]
    by: dict[str, list[dict]] = {}
    for r in rows:
        by.setdefault(r["policy"], []).append(r)
    for pol, rs in sorted(by.items()):
        print("  " + format_summary(pol, summarize(rs)))
    pols = sorted(by)
    for i in range(len(pols)):
        for j in range(i + 1, len(pols)):
            print("  " + format_paired(pols[i], pols[j], paired(by[pols[i]], by[pols[j]])))


if __name__ == "__main__":
    main()
