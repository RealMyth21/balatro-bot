"""Parallel servers, analysis tools and the search player. Need jackdaw (`uv sync --extra sim`), not the game."""

from __future__ import annotations

import json
import random
from concurrent.futures import ThreadPoolExecutor

import pytest

jackdaw = pytest.importorskip("jackdaw")

import httpx  # noqa: E402

from balatro_bot.agent.tools import Shadow, run_tool  # noqa: E402
from balatro_bot.search.round_search import RoundSearchPolicy, determinize  # noqa: E402
from balatro_bot.serve_pool import ServerPool  # noqa: E402


def rpc(port, method, params=None):
    body = {"jsonrpc": "2.0", "method": method, "id": 1}
    if params is not None:
        body["params"] = params
    return httpx.post(f"http://127.0.0.1:{port}", json=body, timeout=60).json()


def test_parallel_jackdaw_servers_play_independent_identical_games():
    def game(port):
        rpc(port, "menu")
        rpc(port, "start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"})
        rpc(port, "select", {"reasoning": "extra fields are ignored"})
        r = rpc(port, "play", {"cards": [0, 1, 2, 3, 4]})["result"]
        return [c["key"] for c in r["hand"]["cards"]], r["round"]["chips"]

    with ServerPool("jackdaw", n=3, base_port=13500) as pool:
        assert len(set(pool.ports)) == 3
        with ThreadPoolExecutor(3) as ex:
            results = list(ex.map(game, pool.ports))
    assert results[0] == results[1] == results[2]
    assert results[0][1] == 56  # same as the real game and the exact engine for this seed


def _shadow_at_first_hand():
    sh = Shadow()
    for m, p in (("menu", None), ("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"}), ("select", None)):
        sh.apply(m, p)
    return sh, sh.sim.view()


def test_tools_give_ranges_and_probabilities():
    sh, view = _shadow_at_first_hand()
    plays = json.loads(run_tool(sh, view, "score_plays", {"top": 5}))
    assert plays["chips_needed"] == 300 and len(plays["plays"]) == 5
    assert all(p["min_score"] <= p["max_score"] for p in plays["plays"])
    cands = {"candidates": [{"discard": [5, 6, 7]}, {"play": [99]}]}
    odds = json.loads(run_tool(sh, view, "estimate_clear_chance", cands))
    assert 0.0 <= odds["results"][0]["clear_chance"] <= 1.0
    assert odds["results"][-1]["error"] == "not a legal action now"


def test_tools_refuse_when_the_shadow_drifts():
    sh, view = _shadow_at_first_hand()
    drifted = dict(view, money=view["money"] + 100)
    out = json.loads(run_tool(sh, drifted, "score_plays", {}))
    assert "shadow drifted on money" in out["error"]


def test_tools_only_answer_during_a_hand():
    sh = Shadow()
    sh.apply("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"})
    out = json.loads(run_tool(sh, sh.sim.view(), "score_plays", {}))
    assert "only work while choosing" in out["error"]


def test_sampled_worlds_hide_the_future_but_keep_the_present():
    from jackdaw.core import Game
    from jackdaw.engine.actions import SelectBlind

    g = Game.new(back="b_black", stake=8, seed="TESTSEED")
    g.step(SelectBlind())
    snap = g.to_dict()
    worlds = [determinize(snap, random.Random(i)) for i in range(5)]
    assert all([c.front for c in w.hand] == [c.front for c in g.hand] for w in worlds)
    assert all(w.dollars == g.dollars and w.ante == g.ante for w in worlds)
    assert len({w.to_dict()["rng"]["seed"] for w in worlds}) == 5          # the seed is resampled
    assert len({w.to_dict()["areas"]["deck"][-1]["front"] for w in worlds}) > 1  # next draw varies


def test_search_policy_returns_legal_actions():
    from jackdaw.core import Game
    from jackdaw.engine.actions import GamePhase

    g = Game.new(back="b_black", stake=8, seed="TESTSEED")
    pol = RoundSearchPolicy(worlds=4)
    for _ in range(30):
        if g.phase == GamePhase.GAME_OVER:
            break
        g.step(pol.act(g))
    assert pol.stats["searched"] > 0
