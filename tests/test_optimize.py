"""optimize_jokers / optimize_hand / average scores: predictions match what the game does when the
suggested rearrange is applied and the play is made."""

from __future__ import annotations

import json

import pytest

pytest.importorskip("jackdaw")

from jackdaw import _core  # noqa: E402
from jackdaw.bridge.backend import SimBackend  # noqa: E402
from jackdaw.core import Game  # noqa: E402

from balatro_bot.agent.tools import score_plays, tools_for_state  # noqa: E402
from balatro_bot.search.optimize import optimize_hand, optimize_jokers  # noqa: E402
from balatro_bot.search.round_search import _preview_total  # noqa: E402


def _game(adds: list[dict]) -> Game:
    jd = SimBackend()
    jd.handle("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"})
    jd.handle("select", None)
    for a in adds:
        jd.handle("add", a)
    return jd.game


ORDER_MATTERS = [{"key": "j_cavendish"}, {"key": "j_joker"}, {"key": "j_hanging_chad"},
                 {"key": "H_K", "enhancement": "BONUS"}, {"key": "S_K"}]


def _apply(game: Game, rearrange: dict | None) -> Game:
    if not rearrange:
        return game
    s = _core.BotSession.from_game(game._g)
    ok, text = s.call("rearrange", json.dumps(rearrange))
    assert ok, text
    return Game(s.game())


def test_joker_order_found_and_true():
    g = _game(ORDER_MATTERS)
    out = optimize_jokers(g)
    assert out["best_order"].index("j_joker") < out["best_order"].index("j_cavendish")  # +Mult before x-Mult
    assert out["gain_guaranteed"] > 0 and out["rearrange"]
    best = out["best_play_with_best_order"]
    real = _preview_total(_apply(g, out["rearrange"])._g, tuple(best["cards"]), "low")
    assert real == best["min"]  # the prediction is what the game scores after the rearrange


def test_card_order_found_and_true():
    g = _game(ORDER_MATTERS)
    out = optimize_hand(g)
    top = out["plays"][0]
    assert top["rearrange"] and top["gain_guaranteed_vs_hand_order"] > 0
    real = _preview_total(_apply(g, top["rearrange"])._g, tuple(top["cards"]), "low")
    assert real == top["min"]
    unordered = _preview_total(g._g, tuple(top["cards"]), "low")
    assert real - unordered == top["gain_guaranteed_vs_hand_order"]
    for p in out["plays"]:
        assert p["min"] <= p["avg"] <= p["max"] or p["min"] == p["max"]


def test_average_sits_between_guaranteed_and_best_case():
    g = _game([{"key": "H_7", "enhancement": "LUCKY"}, {"key": "S_7", "enhancement": "LUCKY"}])
    plays = score_plays(g, top=20, detail=True)["plays"]  # the record's full answer has the average score
    assert all(p["min_score"] <= p["avg_score"] <= p["max_score"] for p in plays)
    assert any(p["min_score"] < p["max_score"] for p in plays)  # the Lucky cards make some plays uncertain


def test_face_down_jokers_cannot_be_ordered():
    g = _game(ORDER_MATTERS)
    d = g.to_dict()
    for j in d["areas"]["jokers"]:
        j["facing"] = "back"
    assert "face down" in optimize_jokers(Game.from_dict(d))["error"]


def test_many_jokers_use_local_search():
    adds = [{"key": k, "edition": "NEGATIVE"} for k in
            ("j_joker", "j_cavendish", "j_jolly", "j_zany", "j_mad", "j_crazy", "j_droll")]
    out = optimize_jokers(_game(adds))
    assert out["method"].startswith("local search") and len(out["best_order"]) == 7
    assert out["gain_guaranteed"] >= 0


def test_offered_while_playing_a_hand():
    names = {t["function"]["name"] for t in tools_for_state("SELECTING_HAND")}
    assert {"optimize_jokers", "optimize_hand"} <= names
    for state in ("SHOP", "SMODS_BOOSTER_OPENED", "BLIND_SELECT"):  # shop and pack choices are the model's own
        assert tools_for_state(state) == []


def test_tool_switches():
    from balatro_bot.agent.tools import TOOL_NAMES, parse_tools

    assert parse_tools("baseline") == ("score_plays",)
    assert parse_tools("none") == () and parse_tools(None) == () and parse_tools(False) == ()
    assert set(parse_tools("all")) == TOOL_NAMES == set(parse_tools(True))
    assert parse_tools("score_plays, optimize_jokers,score_plays") == ("score_plays", "optimize_jokers")
    with pytest.raises(ValueError):
        parse_tools("score_plays,bogus")


def test_score_preview_shows_min_max_and_average_money():
    g = _game([{"key": "H_7", "enhancement": "LUCKY"}, {"key": "S_7", "enhancement": "LUCKY"},
               {"key": "D_7", "enhancement": "LUCKY"}])
    lucky = {i for i, c in enumerate(g.hand) if c.key == "m_lucky"}
    plays = score_plays(g, top=20)["plays"]
    assert all(set(p) == {"cards", "hand", "min_score", "max_score", "avg_money"} for p in plays)
    three = next(p for p in plays if lucky <= set(p["cards"]))
    assert three["avg_money"] == pytest.approx(3 * 20 / 15, abs=0.8)  # each Lucky card: $20 one time in 15
    detail = score_plays(g, top=20, detail=True)["plays"]
    assert all({"avg_score", "money"} <= set(p) for p in detail)  # the record keeps the full answer
