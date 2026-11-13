"""Engine smoke tests. Need your own Balatro install and `scripts/setup.py all`; skipped otherwise
(e.g. in CI). Run locally after changing anything under engine/."""

from __future__ import annotations

import pytest

from balatro_bot.engine.client import ROOT, EngineClient, default_luajit
from balatro_bot.paths import game_src

HAVE_GAME = (game_src() / "main.lua").exists() and (ROOT / "vendor/balatrobot/balatrobot.lua").exists()
pytestmark = pytest.mark.skipif(not HAVE_GAME, reason="needs your Balatro install + scripts/setup.py all")


@pytest.fixture(scope="module")
def eng():
    try:
        client = EngineClient()
    except RuntimeError as e:  # e.g. wrong LuaJIT version
        pytest.skip(f"engine runtime unavailable ({default_luajit()}): {e}")
    yield client
    client.close()


def test_seeded_start_is_reproducible(eng):
    hands = []
    for _ in range(2):
        eng.menu()
        eng.start("BLACK", "GOLD", "TESTSEED")
        hands.append([c["key"] for c in eng.call("select")["hand"]["cards"]])
    # same seed, same deal (also matches the real game and jackdaw for this seed)
    assert hands[0] == hands[1] == ["H_A", "H_K", "D_Q", "S_9", "C_9", "C_5", "D_5", "C_4"]


def test_black_deck_gold_stake_rules(eng):
    eng.menu()
    gs = eng.start("BLACK", "GOLD", "TESTSEED")
    assert (gs["deck"], gs["stake"]) == ("BLACK", "GOLD")
    gs = eng.call("select")
    assert gs["round"]["hands_left"] == 3 and gs["round"]["discards_left"] == 2
    assert gs["jokers"]["limit"] == 6


def test_actions_and_errors(eng):
    eng.menu()
    eng.start("BLACK", "GOLD", "TESTSEED")
    eng.call("set", {"money": 50})
    gs = eng.call("select")
    gs = eng.call("play", {"cards": [0, 1, 2, 3, 4]})
    assert gs["round"]["chips"] > 0
    with pytest.raises(Exception, match="INVALID_STATE|BAD_REQUEST"):
        eng.call("buy", {"card": 0})  # not in the shop
    debug = eng.call("engine_debug_state")
    assert debug["rng"]["seed"] == "TESTSEED"
