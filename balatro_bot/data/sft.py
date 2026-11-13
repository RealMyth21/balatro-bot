"""Turn logged LLM games (BalatroLLM run folders) into a supervised fine-tuning dataset.

Each decision becomes one chat example: the exact prompt the model saw (including any earlier
analysis-tool exchanges within that decision) plus the assistant message it produced. Examples can
be filtered by how the game went (won / final ante) and by what the decision did (only successful
game actions, optionally analysis-tool calls), and are weighted by final ante for later use.

Run folder files used (BalatroLLM v1.1.1 layout): requests.jsonl, responses.jsonl, stats.json, and
outcomes.jsonl (written by balatro_bot.agent.llm_runner: successful / failed / error / local_tool).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Filters:
    min_ante: int = 0
    won_only: bool = False
    keep_outcomes: tuple[str, ...] = ("successful",)
    keep_reasoning: bool = True


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def run_dirs(root: Path) -> Iterator[Path]:
    for stats in sorted(root.rglob("stats.json")):
        if (stats.parent / "requests.jsonl").exists():
            yield stats.parent


def examples_from_run(run_dir: Path, filters: Filters) -> Iterator[dict]:
    stats = json.loads((run_dir / "stats.json").read_text())
    final_ante = int(stats.get("final_ante") or 0)
    won = bool(stats.get("run_won"))
    if filters.won_only and not won:
        return
    if final_ante < filters.min_ante:
        return
    requests = {r["custom_id"]: r.get("body") or {} for r in _jsonl(run_dir / "requests.jsonl")}
    responses = {r["custom_id"]: r for r in _jsonl(run_dir / "responses.jsonl")}
    outcomes = {r["custom_id"]: r.get("outcome") for r in _jsonl(run_dir / "outcomes.jsonl")}
    task = json.loads((run_dir / "task.json").read_text()) if (run_dir / "task.json").exists() else {}
    for custom_id, body in requests.items():
        resp = responses.get(custom_id) or {}
        if resp.get("error") or not resp.get("response"):
            continue
        choice = ((resp["response"].get("body") or {}).get("choices") or [{}])[0]
        message = dict(choice.get("message") or {})
        if not message.get("tool_calls"):
            continue
        outcome = outcomes.get(custom_id, "unknown")
        if outcomes and outcome not in filters.keep_outcomes:
            continue
        if not filters.keep_reasoning:
            message.pop("reasoning_content", None)
        assistant = {k: v for k, v in message.items() if v is not None and k in
                     ("role", "content", "tool_calls", "reasoning_content")}
        assistant["role"] = "assistant"
        yield {
            "messages": list(body.get("messages") or []) + [assistant],
            "tools": body.get("tools") or [],
            "metadata": {"run": run_dir.name, "custom_id": custom_id, "seed": task.get("seed"),
                         "deck": task.get("deck"), "stake": task.get("stake"), "model": task.get("model"),
                         "final_ante": final_ante, "won": won, "outcome": outcome,
                         "weight": final_ante / 8.0 + (1.0 if won else 0.0)},
        }


def export(root: Path, out: Path, filters: Filters) -> dict:
    out.parent.mkdir(parents=True, exist_ok=True)
    n_runs = n_examples = 0
    with out.open("w") as fh:
        for rd in run_dirs(root):
            n_runs += 1
            for ex in examples_from_run(rd, filters):
                fh.write(json.dumps(ex) + "\n")
                n_examples += 1
    return {"runs": n_runs, "examples": n_examples, "out": str(out)}
