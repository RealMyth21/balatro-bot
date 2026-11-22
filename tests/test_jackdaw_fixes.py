"""Corrections to jackdaw (balatro_bot/sim/jackdaw_fixes.py) and its effect text (sim/descriptions.py).

The unit tests check the edits on jackdaw's game state; the engine tests build the situations the
corrections are for and check that corrected jackdaw now matches the real game (our exact engine)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("jackdaw")

from balatro_bot.paths import game_src  # noqa: E402
from balatro_bot.sim.descriptions import _J, add_effects  # noqa: E402
from balatro_bot.sim.jackdaw_fixes import FixedSimBackend, fix_before_select, sliced_by_dagger  # noqa: E402
from balatro_bot.validation.diff import first_diff, rules_view  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
HAVE_GAME = (game_src() / "main.lua").exists() and (ROOT / "vendor/balatrobot/balatrobot.lua").exists()


def _j(key, eternal=False, debuff=False, **ability):
    return {"center": key, "stickers": {"eternal": eternal}, "debuff": debuff, "ability": ability}


def test_which_jokers_the_daggers_destroy():
    assert sliced_by_dagger([_j("j_ceremonial"), _j("j_joker")]) == [(0, 1)]
    assert sliced_by_dagger([_j("j_ceremonial"), _j("j_joker", eternal=True)]) == []
    assert sliced_by_dagger([_j("j_joker"), _j("j_ceremonial")]) == []  # nothing to its right
    assert sliced_by_dagger([_j("j_ceremonial", debuff=True), _j("j_joker")]) == []
    # a Dagger being destroyed by the Dagger to its left does nothing itself
    assert sliced_by_dagger([_j("j_ceremonial"), _j("j_ceremonial"), _j("j_joker")]) == [(0, 1)]


def test_hand_size_and_ownership_corrected_before_select():
    state = {"game": {"hand_size": 13}, "sets": {"used_jokers": ["j_ceremonial", "j_turtle_bean"]},
             "areas": {"jokers": [_j("j_ceremonial"), _j("j_turtle_bean", extra={"h_size": 5, "h_mod": 1})]}}
    changes = fix_before_select(state)
    assert state["game"]["hand_size"] == 8 and state["areas"]["jokers"][1]["ability"]["extra"]["h_size"] == 0
    assert state["sets"]["used_jokers"] == ["j_ceremonial"] and len(changes) == 2
    stunt = {"game": {"hand_size": 6}, "sets": {"used_jokers": []},
             "areas": {"jokers": [_j("j_ceremonial"), _j("j_stuntman", extra={"h_size": 2, "chip_mod": 250})]}}
    fix_before_select(stunt)
    assert stunt["game"]["hand_size"] == 8  # Stuntman takes 2 away, so destroying it gives them back
    nothing = {"game": {"hand_size": 8}, "sets": {"used_jokers": []}, "areas": {"jokers": [_j("j_joker")]}}
    assert fix_before_select(nothing) == [] and nothing["game"]["hand_size"] == 8


def test_every_joker_has_a_description():
    import jackdaw

    centers = json.loads((Path(jackdaw.__file__).parent / "engine/data/centers.json").read_text())
    jokers = {k for k, v in centers.items() if v.get("set") == "Joker"}
    assert jokers == set(_J)


def test_descriptions_fill_the_view():
    jd = FixedSimBackend(describe=True)
    jd.handle("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"})
    jd.handle("select", None)
    for key in ("j_ride_the_bus", "j_steel_joker", "j_loyalty_card"):
        jd.handle("add", {"key": key})
    jd.handle("add", {"key": "H_K", "enhancement": "LUCKY"})
    view = jd.view()
    effects = [c["value"]["effect"] for c in view["jokers"]["cards"]]
    assert "Currently +0 Mult" in effects[0] and "Currently X1 Mult" in effects[1] and "5 remaining" in effects[2]
    lucky = view["hand"]["cards"][-1]["value"]["effect"]
    assert lucky.startswith("+10 chips") and "1 in 5" in lucky and "$20" in lucky
    assert view["blinds"]["boss"]["effect"] and view["blinds"]["small"]["tag_effect"]
    # never breaks a view, whatever the state
    assert add_effects({"state": "MENU"}, {}) == {"state": "MENU"}


TRACES = json.loads((ROOT / "tests/data/dagger_traces.json").read_text())


@pytest.mark.skipif(not HAVE_GAME, reason="needs your Balatro install + scripts/setup.py all")
@pytest.mark.parametrize("seed", sorted(TRACES))
def test_dagger_cases_match_the_game(seed):
    """The requests (from scripts/compare_jackdaw.py) that reach each known Ceremonial Dagger case, ending
    with the blind select where jackdaw went wrong: raw jackdaw differs from the real game there, and
    corrected jackdaw matches it."""
    from jackdaw.bridge.backend import SimBackend

    from balatro_bot.engine.client import EngineClient

    t = TRACES[seed]
    raw, fixed = SimBackend(), FixedSimBackend()
    with EngineClient() as eng:
        eng.menu()
        start = {"deck": t["deck"], "stake": t["stake"], "seed": seed}
        eng.start(t["deck"], t["stake"], seed)
        raw.handle("start", start)
        fixed.handle("start", start)
        for method, params in t["requests"]:
            a = eng.call(method, params or None)
            b, c = raw.handle(method, params or None), fixed.handle(method, params or None)
    assert first_diff(rules_view(a), rules_view(b), labels=("game", "jackdaw")) is not None, t["case"]
    assert first_diff(rules_view(a), rules_view(c), labels=("game", "jackdaw")) is None, t["case"]
    assert fixed.fixes


RARE_JOKERS = ["j_caino", "j_triboulet", "j_yorick", "j_chicot", "j_perkeo", "j_cavendish"]


@pytest.mark.skipif(not HAVE_GAME, reason="needs your Balatro install + scripts/setup.py all")
def test_rare_items_text_matches_the_game(monkeypatch):
    """Items that rarely come up in play (legendary jokers, Cavendish, the secret planets, Retcon), put into
    the real game with its own create_card (engine/hooks/engine_add.lua) and into jackdaw: the numbers
    in our descriptions are the game's."""
    import sys

    sys.path.insert(0, str(ROOT / "scripts"))
    from compare_jackdaw import compare_texts

    from balatro_bot.engine.client import EngineClient

    monkeypatch.setenv("ENGINE_HOOKS", str(ROOT / "engine/hooks/engine_add.lua"))
    jd, texts = FixedSimBackend(describe=True), {}
    with EngineClient() as eng:
        eng.menu()

        def both(method, params=None, jd_method=None):
            return eng.call(method, params), jd.handle(jd_method or method, params)

        eng.start("BLACK", "GOLD", "RARETEXT")
        jd.handle("start", {"deck": "BLACK", "stake": "GOLD", "seed": "RARETEXT"})
        both("select")
        for key in RARE_JOKERS:
            a, b = both("engine_add", {"key": key}, "add")
        compare_texts(a, b, texts)
        # Misprint's text in the game is animated (the API gets "  +"): check our range against its values
        both("sell", {"joker": 0})
        a, b = both("engine_add", {"key": "j_misprint"}, "add")
        extra = eng.call("engine_debug_state")["jokers"][-1]["ability"]["extra"]
        assert b["jokers"]["cards"][-1]["value"]["effect"].endswith(f"from {extra['min']} to {extra['max']}")
        for batch in (["c_planet_x", "c_ceres"], ["c_eris"]):
            for key in batch:
                a, b = both("engine_add", {"key": key}, "add")
            compare_texts(a, b, texts)
            for _ in batch:
                both("sell", {"consumable": 0})
        a, _ = both("gamestate")
        both("set", {"chips": a["blinds"]["small"]["score"]})
        both("play", {"cards": [0]})
        both("cash_out")
        a, b = both("engine_add", {"key": "v_retcon"}, "add")
        compare_texts(a, b, texts)
    expected = set(RARE_JOKERS) | {"c_planet_x", "c_ceres", "c_eris", "v_retcon"}
    assert expected <= set(texts), sorted(expected - set(texts))
    wrong = {k: v for k, v in texts.items() if v["wrong"]}
    assert not wrong, wrong
