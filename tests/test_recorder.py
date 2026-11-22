"""Game records (agent/recorder.py), the non-LLM record script, and the viewer's local server."""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from functools import partial
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest

from balatro_bot.agent.recorder import SCHEMA, Decision, GameRecorder, load

ROOT = Path(__file__).resolve().parents[1]


def test_record_lines(tmp_path: Path):
    path = tmp_path / "r.jsonl"
    with GameRecorder(path, seed="S", deck="BLACK", stake="GOLD", player={"kind": "llm"}) as rec:
        rec.step("gamestate", None, result={"state": "MENU"})  # read-only requests are not steps
        rec.step("start", {"seed": "S"}, result={"state": "BLIND_SELECT"})
        d = Decision(reasoning=["because", ""], tool_calls=[{"name": "score_plays", "arguments": {}, "result": {}}],
                     model_calls=2, cost_usd=0.01)
        rec.step("play", {"cards": [0]}, result={"state": "SELECTING_HAND"}, actor="llm", decision=d)
        rec.step("buy", {"card": 9}, error={"name": "BAD_REQUEST", "message": "no card"}, actor="llm")
        rec.note("no valid action", Decision(reasoning=["hmm"]))
        rec.end(won=False, ante=1)
    lines = load(path)
    assert [x["type"] for x in lines] == ["header", "step", "step", "step", "note", "end"]
    assert lines[0]["schema"] == SCHEMA and lines[0]["seed"] == "S"
    assert [x["i"] for x in lines if x["type"] == "step"] == [0, 1, 2]
    assert lines[2]["decision"]["reasoning"] == ["because"] and lines[2]["decision"]["model_calls"] == 2
    assert lines[3]["error"]["name"] == "BAD_REQUEST" and lines[4]["after_step"] == 2
    assert lines[-1]["steps"] == 3 and lines[-1]["won"] is False


def test_record_script_plays_a_full_game(tmp_path: Path):
    pytest.importorskip("jackdaw")
    sys.path.insert(0, str(ROOT / "scripts"))
    from record_game import record

    out = tmp_path / "g.jsonl"
    summary = record("EYEQG8GT", "smart", "jackdaw", "BLACK", "GOLD", out, max_steps=3000)
    lines = load(out)
    steps = [x for x in lines if x["type"] == "step"]
    assert summary["error"] is None and lines[-1]["type"] == "end"
    assert steps[0]["method"] == "start" and all(s["result"] for s in steps)
    assert lines[-1]["ante"] == steps[-1]["result"]["ante_num"]
    assert {s["actor"] for s in steps} <= {"auto", "policy"} and any(s["actor"] == "policy" for s in steps)
    # each play's scoring breakdown is what the game scored: the round's chips go up by its total
    prev = None
    for s in steps:
        if s["method"] == "play":
            sc = s["scoring"]
            assert sc["hand"] and sc["events"] and sc["chips"] * sc["mult"] == pytest.approx(sc["total"], rel=1e-9)
            after = s["result"]["round"]["chips"] if s["result"]["state"] == "SELECTING_HAND" else None
            if after is not None and prev is not None:
                assert after - prev["round"]["chips"] == pytest.approx(sc["total"])
        prev = s["result"]


def test_viewer_server_serves_only_viewer_and_records():
    sys.path.insert(0, str(ROOT / "scripts"))
    from view_record import ROOT as VR_ROOT
    from view_record import Handler

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), partial(Handler, directory=str(VR_ROOT)))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"

    def status(path: str) -> int:
        try:
            return urllib.request.urlopen(base + path).status
        except urllib.error.HTTPError as e:
            return e.code

    try:
        assert status("/") == 200 and status("/viewer/index.html") == 200
        assert isinstance(json.loads(urllib.request.urlopen(base + "/records.json").read()), list)
        for bad in ("/README.md", "/pyproject.toml", "/viewer/../pyproject.toml", "/viewer/%2e%2e/pyproject.toml",
                    "/runtime/records/../../pyproject.toml", "/runtime/logs/x.log"):
            assert status(bad) == 404, bad
    finally:
        httpd.shutdown()
