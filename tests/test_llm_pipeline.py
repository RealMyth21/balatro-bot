"""End-to-end LLM pipeline: BalatroLLM + our runner + mock model + parallel jackdaw servers.
Needs the `llm` and `sim` extras (no game files, no API key)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("balatrollm")
pytest.importorskip("jackdaw")

from balatro_bot.agent.llm_runner import Prices, run_games, same_model  # noqa: E402
from balatro_bot.agent.mock_llm import start  # noqa: E402
from balatro_bot.data.sft import Filters, export  # noqa: E402
from balatro_bot.serve_pool import ServerPool  # noqa: E402


def test_parallel_games_with_tools_logs_and_export(tmp_path: Path):
    httpd, _ = start(0, parallel_tools=True)  # several analysis calls (and an action) in one message
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    summary = tmp_path / "llm.jsonl"
    try:
        with ServerPool("jackdaw", n=2) as pool:
            out = asyncio.run(run_games(
                ports=pool.ports, seeds=["EYEQG8GT", "NCWEFXTT"], model="mock-model", base_url=base_url,
                api_key="mock", runs_dir=tmp_path / "runs", summary=summary, label="test", tools=True,
                drop_params=("seed",)))
    finally:
        httpd.shutdown()
    assert httpd.mock.rejected == 0  # every tool call was answered
    assert out["played"] == 2
    rows = [json.loads(line) for line in summary.read_text().splitlines()]
    assert {r["seed"] for r in rows} == {"EYEQG8GT", "NCWEFXTT"}
    assert all(r["error"] is None and r["tokens"]["calls"] > 0 and r["cost_usd"] == 0 for r in rows)  # local: free
    outcomes = [json.loads(line)["outcome"] for p in (tmp_path / "runs").rglob("outcomes.jsonl")
                for line in p.read_text().splitlines()]
    assert "local_tool" in outcomes and "successful" in outcomes
    requests = [json.loads(line)["body"] for p in (tmp_path / "runs").rglob("requests.jsonl")
                for line in p.read_text().splitlines()]
    assert all("seed" not in r for r in requests)  # --drop-param worked
    assert not any("**Seed**" in json.dumps(r) for r in requests)  # the prompt never names the run's seed
    # every game has a record: steps in order, model decisions with their tool calls and results
    for r in rows:
        lines = [json.loads(line) for line in Path(r["record"]).read_text().splitlines()]
        assert lines[0]["type"] == "header" and lines[0]["player"]["model"] == "mock-model"
        assert lines[-1]["type"] == "end" and lines[-1]["ante"] == r["ante"]
        llm = [x for x in lines if x.get("actor") == "llm"]
        assert llm and all(x["decision"]["model_calls"] >= 1 for x in llm)
        calls = [t for x in llm for t in x["decision"]["tool_calls"]]
        assert {t["name"] for t in calls} >= {"score_plays", "estimate_clear_chance"}
        assert all(isinstance(t["result"], dict) for t in calls)
        # the model's final answer (the training target) and the exact prompt ids are kept
        assert all(x["decision"]["model_output"]["tool_calls"] for x in llm)
        assert all(x["decision"]["model_output"]["tool_calls"][-1]["name"] == x["method"] for x in llm)
        assert all(x["decision"]["custom_ids"] for x in llm)
        # every accepted play has its scoring breakdown, and it is the score the game gave it
        plays = [x for x in llm if x["method"] == "play" and not x.get("error")]
        assert plays and all(x["scoring"]["total"] > 0 and x["scoring"]["events"] for x in plays)
    stats = export(tmp_path / "runs", tmp_path / "sft.jsonl",
                   Filters(keep_outcomes=("successful", "local_tool"), models=("mock-model",)))
    assert stats["runs"] == 2 and stats["examples"] >= 4

    # resuming skips seeds that were already played
    out2 = asyncio.run(run_games(ports=[1], seeds=["EYEQG8GT"], model="mock-model", base_url=base_url,
                                 api_key="mock", runs_dir=tmp_path / "runs", summary=summary, label="test"))
    assert out2["played"] == 0


def _run(tmp_path: Path, seeds: list[str], **kw):
    httpd, _ = start(0, served_model=kw.pop("served_model", None))
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    try:
        with ServerPool("jackdaw", n=2) as pool:
            return asyncio.run(run_games(ports=pool.ports, seeds=seeds, model="mock-model", base_url=base_url,
                                         api_key="mock", runs_dir=tmp_path / "runs", summary=tmp_path / "s.jsonl",
                                         label="t", **kw))
    finally:
        httpd.shutdown()


def test_budget_cap_holds_and_stopped_games_are_retried(tmp_path: Path):
    seeds = ["EYEQG8GT", "NCWEFXTT", "8VGHMZ3D", "2SK6J4UZ"]
    assert _run(tmp_path, seeds[:1])["played"] == 1  # one finished game, cheap
    pricey = Prices(input=1000.0, output=0.0, cached_input=0.0)  # each call costs a few dollars
    out = _run(tmp_path, seeds, prices=pricey, max_cost=20.0)
    rows = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
    call_cost = max(r["cost_usd"] / max(1, r["tokens"]["calls"]) for r in rows if r["tokens"].get("calls"))
    assert out["stopped_on_budget"]
    assert out["total_spent_usd"] <= 20.0 + 2 * call_cost  # at most one call in flight per game
    assert any(r["error_kind"] == "infra" and "budget exhausted" in r["error"] for r in rows)
    assert sum(r["seed"] == seeds[0] for r in rows) == 1  # the finished seed was not replayed
    # a later run with more budget plays every seed that has not finished, and only those
    out2 = _run(tmp_path, seeds, prices=Prices(), max_cost=float("inf"))
    assert out2["played"] == 3


def test_dead_server_is_taken_out_of_use(tmp_path: Path):
    httpd, _ = start(0)
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    try:
        with ServerPool("jackdaw", n=2) as pool:
            pool.procs[0].terminate()
            pool.procs[0].wait()
            out = asyncio.run(run_games(ports=pool.ports, seeds=["EYEQG8GT", "NCWEFXTT", "8VGHMZ3D"],
                                        model="mock-model", base_url=base_url, api_key="mock",
                                        runs_dir=tmp_path / "runs", summary=tmp_path / "s.jsonl", label="t",
                                        port_alive=pool.alive))
    finally:
        httpd.shutdown()
    rows = [json.loads(line) for line in (tmp_path / "s.jsonl").read_text().splitlines()]
    assert out["servers_lost"] == 1 and out["played"] == 3 and all(r["error"] is None for r in rows)


def test_model_mismatch_is_an_infrastructure_error(tmp_path: Path):
    _run(tmp_path, ["EYEQG8GT"], served_model="mock-model-old")
    row = json.loads((tmp_path / "s.jsonl").read_text().splitlines()[0])
    assert "model mismatch" in row["error"] and row["error_kind"] == "infra"


def test_same_model():
    assert same_model("qwen3-8b", "qwen3-8b")
    assert same_model("qwen3-8b", "Qwen/qwen3-8b")
    assert same_model("qwen3-8b", "qwen3-8b-2026-11-01")
    for served in ("qwen3", "qwen", "", "qwen3-8b-mini", "qwen3-4b"):
        assert not same_model("qwen3-8b", served), served
