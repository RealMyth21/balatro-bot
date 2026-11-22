"""Engine smoke tests. Need your own Balatro install and `scripts/setup.py all`; skipped otherwise
(e.g. in CI). Run locally after changing anything under engine/."""

from __future__ import annotations

import time

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


def test_engine_recovers_from_an_error_inside_a_game_event(monkeypatch):
    from balatro_bot.game.client import RPCError

    monkeypatch.setenv("ENGINE_HOOKS", str(ROOT / "tests/data/crash_event_hook.lua"))
    with EngineClient() as e:
        e.start("BLACK", "GOLD", "TESTSEED")
        e.call("select")
        with pytest.raises(RPCError, match="boom inside an event"):
            e.call("test_crash_event")
        with pytest.raises(RPCError, match="game in progress was lost"):  # never a silently reset game
            e.call("gamestate")
        assert e.restarts == 1
        e.start("BLACK", "GOLD", "TESTSEED")  # the fresh engine plays on
        assert [c["key"] for c in e.call("select")["hand"]["cards"]][:3] == ["H_A", "H_K", "D_Q"]


def test_bot_profile_comes_from_the_environment(monkeypatch):
    from balatro_bot.engine.client import engine_env

    monkeypatch.setenv("BOT_PROFILE", "3")
    assert engine_env()["BOT_PROFILE"] == "3"
    assert engine_env(profile=2)["BOT_PROFILE"] == "2"
    monkeypatch.delenv("BOT_PROFILE")
    assert engine_env()["BOT_PROFILE"] == "2"


def test_a_stuck_engine_is_stopped_and_replaced(monkeypatch):
    """A request that never comes back (a loop inside one frame, which the engine's own frame limit
    can't catch) fails after call_timeout with INTERNAL_ERROR; the next call gets a fresh engine."""
    from balatro_bot.game.client import RPCError

    monkeypatch.setenv("ENGINE_HOOKS", str(ROOT / "tests/data/hang_hook.lua"))
    with EngineClient(call_timeout=3) as eng:
        eng.menu()
        eng.start("BLACK", "GOLD", "HANGTEST")
        t0 = time.perf_counter()
        with pytest.raises(RPCError) as err:
            eng.call("engine_test_hang")
        assert err.value.name == "INTERNAL_ERROR" and "did not answer" in err.value.message
        assert time.perf_counter() - t0 < 60
        assert eng.menu()["state"] == "MENU" and eng.restarts == 1
