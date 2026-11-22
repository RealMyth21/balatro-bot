"""Play Black Deck / Gold Stake games with an LLM through BalatroLLM, many in parallel.

  # dry run of the whole pipeline with a mock model (no GPU):
  uv run python scripts/run_llm.py --mock --n 4 --parallel 4

  # an open-weights model served on the local GPU by vLLM (scripts/gpu/run_on_gpu.sh does all of this):
  uv run python scripts/run_llm.py --model Qwen/Qwen3-8B --n 10 --parallel 4
  uv run python scripts/run_llm.py --model Qwen/Qwen3-8B --tools --n 10 --parallel 4
  # Qwen3 without its thinking mode (a chat-template switch sent with each request):
  uv run python scripts/run_llm.py --model Qwen/Qwen3-8B --label qwen3-8b-nothink \
      --model-config '{"extra_body": {"chat_template_kwargs": {"enable_thinking": false}}}'

The model is any OpenAI-compatible server; by default a local one (vLLM) at http://127.0.0.1:8000/v1,
which needs no key. Games run on our exact engine by default (real description text for the prompt;
needs a Balatro install); --backend jackdaw uses the simulator (text fields are names only).
Per-game summaries go to runtime/eval/llm.jsonl and full BalatroLLM logs to runtime/llm_runs/.
Rerunning skips seeds already finished under the same label, deck, stake and model, and retries seeds
that stopped on an infrastructure error (model server down, dead game server, budget stop); those are
left out of the results until they finish.
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

LOCAL_URL = "http://127.0.0.1:8000/v1"  # vLLM's default


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--label", default=None, help="name for this configuration in the results")
    ap.add_argument("--model", default="Qwen/Qwen3-8B", help="model name the server serves")
    ap.add_argument("--base-url", default=LOCAL_URL, help="OpenAI-compatible server (default: local vLLM)")
    ap.add_argument("--api-key-env", default="LOCAL_LLM_KEY",
                    help="environment variable holding a key, if the server needs one (a local one doesn't)")
    ap.add_argument("--tools", action="store_true",
                    help="enable the analysis tools (exact scores, clear odds, joker and card order)")
    ap.add_argument("--backend", default="engine", choices=["engine", "jackdaw"])
    ap.add_argument("--parallel", type=int, default=4)
    ap.add_argument("--split", default="dev", choices=["dev", "heldout", "smoke"])
    ap.add_argument("--n", type=int, default=10)
    ap.add_argument("--deck", default="BLACK")
    ap.add_argument("--stake", default="GOLD")
    ap.add_argument("--strategy", default="default", help="BalatroLLM strategy name")
    ap.add_argument("--model-config", default="{}", help='JSON merged into BalatroLLM\'s request params')
    ap.add_argument("--max-cost", type=float, default=float("inf"),
                    help="USD cap for this label/deck/stake/model, earlier runs included; checked before every call")
    ap.add_argument("--price-in", type=float, default=0.0, help="USD per 1M input tokens (0: local model)")
    ap.add_argument("--price-out", type=float, default=0.0, help="USD per 1M output tokens")
    ap.add_argument("--price-cached", type=float, default=0.0, help="USD per 1M cached input tokens")
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
        local = base_url.startswith(("http://127.0.0.1", "http://localhost"))
        if not api_key:
            if not local:
                sys.exit(f"set {args.api_key_env} in your shell (the key is read from the environment only)")
            api_key = "local"  # a local server ignores it; the client needs some value

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
                drop_params=tuple(args.drop_param), port_alive=pool.alive))
    finally:
        if mock_httpd:
            mock_httpd.shutdown()
    print(f"\nplayed {out['played']} games, spent ${out['spent_usd']:.2f} (all runs of this label: "
          f"${out['total_spent_usd']:.2f})" + (" (stopped: budget reached)" if out["stopped_on_budget"] else "")
          + (f"; {out['servers_lost']} servers died (see runtime/logs/servers)" if out["servers_lost"] else ""))
    wanted = set(seed_list)
    rows = [r for r in load(args.summary)
            if r["seed"] in wanted and r.get("deck") == args.deck and r.get("stake") == args.stake]
    models: dict[str, set] = {}
    for r in rows:
        models.setdefault(r["policy"], set()).add(r.get("model"))
    by: dict[str, list[dict]] = {}
    for r in rows:
        key = r["policy"] if len(models[r["policy"]]) == 1 else f"{r['policy']} [{r.get('model')}]"
        by.setdefault(key, []).append(r)
    for pol, rs in sorted(by.items()):
        print("  " + format_summary(pol, summarize(rs)))
    pols = sorted(by)
    for i in range(len(pols)):
        for j in range(i + 1, len(pols)):
            print("  " + format_paired(pols[i], pols[j], paired(by[pols[i]], by[pols[j]])))


if __name__ == "__main__":
    main()
