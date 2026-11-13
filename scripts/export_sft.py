"""Export logged LLM games as a chat-format fine-tuning dataset (JSONL).

  uv run python scripts/export_sft.py --runs runtime/llm_runs --out runtime/sft/kimi.jsonl --min-ante 3
  uv run python scripts/export_sft.py --won-only --no-reasoning

Each line: {"messages": [...prompt..., assistant tool call], "tools": [...], "metadata": {...}}.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from balatro_bot.data.sft import Filters, export  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", type=Path, default=Path("runtime/llm_runs"))
    ap.add_argument("--out", type=Path, default=Path("runtime/sft/dataset.jsonl"))
    ap.add_argument("--min-ante", type=int, default=0, help="only games that reached at least this ante")
    ap.add_argument("--won-only", action="store_true")
    ap.add_argument("--with-tool-calls", action="store_true", help="also keep analysis-tool calls as examples")
    ap.add_argument("--no-reasoning", action="store_true", help="drop the model's reasoning text")
    args = ap.parse_args()
    keep = ("successful", "local_tool") if args.with_tool_calls else ("successful",)
    stats = export(args.runs, args.out, Filters(args.min_ante, args.won_only, keep, not args.no_reasoning))
    print(f"{stats['examples']} examples from {stats['runs']} runs -> {stats['out']}")


if __name__ == "__main__":
    main()
