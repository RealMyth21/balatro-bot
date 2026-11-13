"""End-to-end LLM pipeline: BalatroLLM + our runner + mock model + parallel jackdaw servers.
Needs the `llm` and `sim` extras (no game files, no API key)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

pytest.importorskip("balatrollm")
pytest.importorskip("jackdaw")

from balatro_bot.agent.llm_runner import run_games  # noqa: E402
from balatro_bot.agent.mock_llm import start  # noqa: E402
from balatro_bot.data.sft import Filters, export  # noqa: E402
from balatro_bot.serve_pool import ServerPool  # noqa: E402


def test_parallel_games_with_tools_logs_and_export(tmp_path: Path):
    httpd, _ = start(0)
    base_url = f"http://127.0.0.1:{httpd.server_address[1]}/v1"
    summary = tmp_path / "llm.jsonl"
    try:
        with ServerPool("jackdaw", n=2, base_port=13600) as pool:
            out = asyncio.run(run_games(
                ports=pool.ports, seeds=["EYEQG8GT", "NCWEFXTT"], model="mock-model", base_url=base_url,
                api_key="mock", runs_dir=tmp_path / "runs", summary=summary, label="test", tools=True,
                drop_params=("seed",)))
    finally:
        httpd.shutdown()
    assert out["played"] == 2
    rows = [json.loads(line) for line in summary.read_text().splitlines()]
    assert {r["seed"] for r in rows} == {"EYEQG8GT", "NCWEFXTT"}
    assert all(r["error"] is None and r["cost_usd"] > 0 for r in rows)
    outcomes = [json.loads(line)["outcome"] for p in (tmp_path / "runs").rglob("outcomes.jsonl")
                for line in p.read_text().splitlines()]
    assert "local_tool" in outcomes and "successful" in outcomes
    requests = [json.loads(line)["body"] for p in (tmp_path / "runs").rglob("requests.jsonl")
                for line in p.read_text().splitlines()]
    assert all("seed" not in r for r in requests)  # --drop-param worked
    stats = export(tmp_path / "runs", tmp_path / "sft.jsonl", Filters(keep_outcomes=("successful", "local_tool")))
    assert stats["runs"] == 2 and stats["examples"] >= 4

    # resuming skips seeds that were already played
    out2 = asyncio.run(run_games(ports=[1], seeds=["EYEQG8GT"], model="mock-model", base_url=base_url,
                                 api_key="mock", runs_dir=tmp_path / "runs", summary=summary, label="test"))
    assert out2["played"] == 0
