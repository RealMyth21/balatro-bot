"""evaluate_options: shop/pack plans compared over shared imagined futures played to the end."""

from __future__ import annotations

import copy
import json
import random

import pytest

pytest.importorskip("jackdaw")

from jackdaw.bridge.backend import SimBackend  # noqa: E402
from jackdaw.cli.smart_agent import smart_agent  # noqa: E402
from jackdaw.core import Game  # noqa: E402
from jackdaw.engine.actions import GamePhase  # noqa: E402

from balatro_bot.agent.tools import Shadow, run_tool, tools_for_state  # noqa: E402
from balatro_bot.search.option_eval import ANTES, OptionEvaluator, RunScore  # noqa: E402

FAST = dict(worlds=6, rollout="smart")  # the smart agent finishes futures in milliseconds


def _shop(seed: str = "NCWEFXTT", ante: int = 2) -> Game:
    g = Game.new(back="b_black", stake=8, seed=seed)
    while not (g.phase == GamePhase.SHOP and g.ante >= ante):
        g.step(smart_agent(g, g.legal_actions()))
        assert g.phase != GamePhase.GAME_OVER
    return g


def _strip(result: dict) -> dict:
    return {k: v for k, v in result.items() if k != "seconds"}


def test_default_shortlist_and_sane_numbers():
    out = OptionEvaluator(**FAST).evaluate(_shop())
    assert out["phase"] == "SHOP" and out["baseline"] == [{"next_round": {}}]
    opts = [o for o in out["options"] if "score" in o]
    assert len(opts) >= 2 and not [o for o in out["options"] if "error" in o]
    for o in opts:
        reach = [o["reach_ante"][str(a)] for a in ANTES]
        assert reach == sorted(reach, reverse=True) and o["win_chance"] <= reach[-1]  # can't skip an ante
        assert 0 <= o["score"] <= 100 and o["mean_ante"] >= 1
    scores = [o["score"] for o in opts]
    assert scores == sorted(scores, reverse=True)  # ranked best first


def test_deterministic_and_fair():
    g = _shop()
    a = _strip(OptionEvaluator(**FAST).evaluate(g))
    assert a == _strip(OptionEvaluator(**FAST).evaluate(g))
    # same visible shop, different hidden truth (deck order): the answer must not change
    d = copy.deepcopy(g.to_dict())
    random.Random(1).shuffle(d["areas"]["deck"])
    assert a == _strip(OptionEvaluator(**FAST).evaluate(Game.from_dict(d)))


def test_custom_plans_errors_and_rerolls():
    g = _shop()
    out = OptionEvaluator(**FAST).evaluate(g, [
        {"steps": [{"next_round": {}}]},
        {"steps": [{"buy": {"card": 9}}]},          # no such card: not possible in any future
        {"steps": [{"dance": {}}]},                  # not a shop action
        [{"reroll": {}}, {"buy": {"card": 0}}],      # a bare list works too
    ])
    by = {json.dumps(o["steps"]): o for o in out["options"]}
    assert "not possible now" in by[json.dumps([{"buy": {"card": 9}}])]["error"]
    assert any("not a shop/pack/blind action" in o.get("error", "") for o in out["options"])
    rr = by[json.dumps([{"reroll": {}}, {"buy": {"card": 0}}])]
    assert "score" in rr or "not possible now" in rr["error"]  # reroll may be unaffordable


def test_pack_picks():
    from jackdaw import _core

    session = _core.BotSession.from_game(_shop()._g)  # open a pack through the API, as a player would
    opened = next((Game(session.game()) for i in range(2)
                   if session.call("buy", json.dumps({"pack": i}))[0]), None)
    if opened is None or opened.phase != GamePhase.PACK_OPENING:
        pytest.skip("no affordable pack in this shop")
    out = OptionEvaluator(**FAST).evaluate(opened)
    assert out["phase"] == "PACK_OPENING"
    assert {next(iter(o["steps"][0])) for o in out["options"]} == {"pack"}  # picks and skip


def test_run_score_weights():
    reach = {a: 1.0 if a <= 4 else 0.0 for a in ANTES}  # reached ante 4, no further
    equal = RunScore(reach_weights=(1,) * 7, win_weight=1)
    assert equal(reach, 0.0) == pytest.approx(100 * 3 / 8)  # = (mean ante - 1) / 8 with equal weights
    steep = RunScore()
    assert steep(reach, 0.0) < equal(reach, 0.0)  # steep weights care about the late antes
    assert steep({a: 1.0 for a in ANTES}, 1.0) == pytest.approx(100.0)
    with pytest.raises(ValueError):
        RunScore(reach_weights=(1, 2, 3))


def test_tool_availability_by_state():
    assert {t["function"]["name"] for t in tools_for_state("SHOP")} == {"evaluate_options"}
    assert {t["function"]["name"] for t in tools_for_state("SMODS_BOOSTER_OPENED")} == {"evaluate_options"}
    assert {t["function"]["name"] for t in tools_for_state("SELECTING_HAND")} == {"score_plays",
                                                                                  "estimate_clear_chance"}
    assert tools_for_state("BLIND_SELECT") == []

    real, shadow = SimBackend(), Shadow()
    for m, p in (("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"}), ("select", None)):
        real.handle(m, p)
        shadow.apply(m, p)
    hand_view = real.view()
    assert "only works in the shop" in json.loads(run_tool(shadow, hand_view, "evaluate_options", {}))["error"]
    shop_view = dict(hand_view, state="SHOP")
    assert "only works while choosing" in json.loads(run_tool(shadow, shop_view, "score_plays", {}))["error"]


def test_tool_answers_in_a_real_shop():
    real, shadow = SimBackend(), Shadow()
    start = {"deck": "BLACK", "stake": "GOLD", "seed": "NCWEFXTT"}
    real.handle("start", start)
    shadow.apply("start", start)
    for _ in range(200):  # jackdaw's smart agent, sent as API requests to both
        if real.view()["state"] == "SHOP":
            break
        method, params = real.request_for(smart_agent(real.game, real.game.legal_actions()))
        real.handle(method, params or None)
        shadow.apply(method, params or None)
    view = real.view()
    assert view["state"] == "SHOP"
    out = json.loads(run_tool(shadow, view, "evaluate_options", {}, evaluator=OptionEvaluator(**FAST)))
    assert "error" not in out and out["options"] and out["phase"] == "SHOP"
