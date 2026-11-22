"""Fine-tuning exporter, evaluation report and mock model. No game needed."""

from __future__ import annotations

import json
from pathlib import Path

from balatro_bot.agent.llm_runner import Prices, run_cost
from balatro_bot.agent.mock_llm import choose_tool_call
from balatro_bot.data.sft import Filters, export
from balatro_bot.eval.report import paired, summarize, wilson
from balatro_bot.eval.seeds import check_disjoint, seeds


def _write_run(root: Path, name: str, final_ante: int, won: bool) -> Path:
    """A run folder in BalatroLLM v1.1.1's layout, plus our outcomes.jsonl."""
    rd = root / name
    rd.mkdir(parents=True)
    (rd / "stats.json").write_text(json.dumps({"run_won": won, "final_ante": final_ante}))
    (rd / "task.json").write_text(json.dumps({"seed": "ABC", "deck": "BLACK", "stake": "GOLD", "model": "m"}))
    reqs, resps, outs = [], [], []
    for i, outcome in enumerate(["successful", "failed", "local_tool", "successful"], 1):
        cid = f"request-{i:05}"
        reqs.append({"custom_id": cid, "method": "POST", "url": "/v1/chat/completions",
                     "body": {"model": "m", "messages": [{"role": "user", "content": f"state {i}"}], "tools": []}})
        msg = {"role": "assistant", "content": None, "reasoning_content": "thinking",
               "tool_calls": [{"id": f"c{i}", "type": "function", "function": {"name": "play", "arguments": "{}"}}]}
        resps.append({"id": "1", "custom_id": cid, "error": None, "response": {
            "request_id": "1", "status_code": 200,
            "body": {"model": "m", "choices": [{"message": msg}],
                     "usage": {"prompt_tokens": 1000, "completion_tokens": 100,
                               "prompt_tokens_details": {"cached_tokens": 400}}}}})
        outs.append({"custom_id": cid, "outcome": outcome})
    for fname, rows in (("requests.jsonl", reqs), ("responses.jsonl", resps), ("outcomes.jsonl", outs)):
        (rd / fname).write_text("".join(json.dumps(r) + "\n" for r in rows))
    return rd


def test_sft_export_filters(tmp_path: Path):
    _write_run(tmp_path / "runs", "good", final_ante=6, won=False)
    _write_run(tmp_path / "runs", "bad", final_ante=1, won=False)
    out = tmp_path / "sft.jsonl"
    stats = export(tmp_path / "runs", out, Filters(min_ante=3))
    assert stats == {"runs": 2, "examples": 2, "out": str(out)}  # only successful decisions of the good run
    ex = [json.loads(line) for line in out.read_text().splitlines()]
    assert ex[0]["messages"][-1]["role"] == "assistant" and ex[0]["messages"][-1]["tool_calls"]
    assert ex[0]["messages"][-1]["reasoning_content"] == "thinking"
    stats = export(tmp_path / "runs", out, Filters(keep_outcomes=("successful", "local_tool"), keep_reasoning=False))
    assert stats["examples"] == 6
    assert "reasoning_content" not in json.loads(out.read_text().splitlines()[0])["messages"][-1]


def test_cost_accounting(tmp_path: Path):
    rd = _write_run(tmp_path, "r", 2, False)
    c = run_cost(rd, Prices(input=2.0, output=10.0, cached_input=0.2))
    # per call: 600 uncached * 2 + 400 cached * 0.2 + 100 out * 10 = 2280 / 1e6 USD; 4 calls
    assert abs(c["cost_usd"] - 4 * 2280 / 1e6) < 1e-12
    assert c["tokens"] == {"prompt": 4000, "completion": 400, "cached": 1600, "calls": 4, "failed_calls": 0}


def test_cost_counts_moonshot_cached_tokens(tmp_path: Path):
    rd = tmp_path / "m"
    rd.mkdir()
    usage = {"prompt_tokens": 1000, "completion_tokens": 100, "cached_tokens": 400}  # Moonshot puts it top-level
    lines = [{"response": {"body": {"usage": usage}}}, {"error": {"code": "timeout", "message": "t"}}]
    (rd / "responses.jsonl").write_text("\n".join(json.dumps(x) for x in lines) + "\n")
    c = run_cost(rd, Prices(input=2.0, output=10.0, cached_input=0.2))
    assert c["tokens"]["cached"] == 400 and c["tokens"]["failed_calls"] == 1
    assert abs(c["cost_usd"] - (600 * 2 + 400 * 0.2 + 100 * 10) / 1e6) < 1e-12


def test_report_statistics():
    lo, hi = wilson(0, 400)
    assert lo == 0.0 and 0.005 < hi < 0.015
    rows_a = [{"seed": str(i), "won": i < 3, "ante": 5} for i in range(20)]
    rows_b = [{"seed": str(i), "won": False, "ante": 3} for i in range(20)]
    s = summarize(rows_a)
    assert s["wins"] == 3 and s["mean_ante"] == 5
    p = paired(rows_a, rows_b)
    assert p["wins_only_a"] == 3 and p["wins_only_b"] == 0 and p["mean_ante_diff"] == 2
    assert p["mcnemar_p"] == 0.25


def test_seed_lists_are_fixed_and_disjoint():
    assert seeds("dev", 3) == seeds("dev", 3)
    assert all(len(s) == 8 and "0" not in s and "O" not in s for s in seeds("heldout", 50))
    check_disjoint()


def test_mock_model_picks_valid_tools():
    def tool(n):
        return {"type": "function", "function": {
            "name": n, "parameters": {"type": "object", "properties": {"reasoning": {"type": "string"}}}}}
    assert choose_tool_call([tool("buy"), tool("next_round")]) == ("next_round", {"reasoning": "mock"})
    assert choose_tool_call([tool("play"), tool("discard")])[0] == "play"


def test_report_scores_errors_by_kind():
    from balatro_bot.eval.report import error_kind, paired, summarize

    rows = [
        {"seed": "A", "policy": "p", "won": False, "ante": None, "error": "BotError: LLM error: 503"},  # infra
        {"seed": "A", "policy": "p", "won": True, "ante": 9, "error": None},  # its retry
        {"seed": "B", "policy": "p", "won": False, "ante": 4,
         "error": "BotError: Too many consecutive error/failed calls"},  # the model's own failure
        {"seed": "C", "policy": "p", "won": False, "ante": None, "error": "ConnectError: refused"},  # infra only
    ]
    assert [error_kind(r) for r in rows] == ["infra", None, "policy", "infra"]
    s = summarize(rows)
    assert (s["games"], s["wins"], s["mean_ante"]) == (2, 1, 6.5)
    assert s["policy_failures"] == 1 and s["excluded_infra_errors"] == 1
    p = paired(rows, [{"seed": "A", "won": False, "ante": 3}, {"seed": "C", "won": False, "ante": 2}])
    assert p["common_seeds"] == 1 and p["wins_only_a"] == 1
