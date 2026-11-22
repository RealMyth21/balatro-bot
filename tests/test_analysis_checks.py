"""The checks that keep jackdaw's mistakes out of results: each play's predicted score is compared with
the real game's, a mismatch takes the analysis offline (noted in the record and the result row), and a
jackdaw-driven policy only sends requests the real game's API accepts in the current state."""

from __future__ import annotations

import asyncio

import pytest

pytest.importorskip("jackdaw")

from jackdaw.bridge.backend import SimBackend  # noqa: E402

from balatro_bot.agent.tools import Shadow  # noqa: E402
from balatro_bot.engine.request_states import accepted  # noqa: E402


def test_real_api_states():
    assert accepted("cash_out", "ROUND_EVAL") and not accepted("use", "ROUND_EVAL")
    assert accepted("use", "SHOP") and accepted("sell", "SELECTING_HAND")
    assert not accepted("sell", "SMODS_BOOSTER_OPENED") and accepted("rearrange", "SMODS_BOOSTER_OPENED")
    assert not accepted("use", "BLIND_SELECT") and accepted("set", "ROUND_EVAL")


def test_verify_play_counts_and_fails_on_mismatch():
    sh = Shadow()
    assert sh.verify_play(100.0, 100.0, [0, 1]) and sh.ok
    assert not sh.verify_play(100.0, 140.0, [2]) and not sh.ok
    assert sh.summary() == {"online": False, "reason": "shadow scored play [2] as 100, the game as 140",
                            "plays_checked": 2, "plays_mismatched": 1}


class _Game:
    """A stand-in for the real game: jackdaw, but every play scores `extra` more chips."""

    def __init__(self, extra: float):
        self.jd, self.extra = SimBackend(), extra

    async def call(self, method, params=None):
        view = self.jd.handle(method, params)
        if method == "play" and self.extra:
            view = {**view, "round": {**view["round"], "chips": view["round"]["chips"] + self.extra}}
        return view


class _Bot:
    _acting = False
    _action_failed = False

    def __init__(self):
        self.steps, self.notes = [], []
        self.recorder = self

    def _record_step(self, method, params, **kw):
        self.steps.append((method, kw))

    def note(self, text, decision=None, kind="invalid_answer"):
        self.notes.append((kind, text))


def _play(extra: float):
    pytest.importorskip("balatrollm")
    from balatro_bot.agent.llm_runner import _make_bot_class

    client_cls = _make_bot_class().RecordingClient
    shadow, bot = Shadow(), _Bot()
    client = client_cls(_Game(extra), bot, shadow)

    async def run():
        await client.call("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"})
        await client.call("select", None)
        await client.call("play", {"cards": [0, 1, 2, 3, 4]})

    asyncio.run(run())
    scoring = dict(bot.steps)["play"]["scoring"]
    return shadow, bot, scoring


def test_matching_play_keeps_analysis_online():
    shadow, bot, scoring = _play(0)
    assert shadow.ok and shadow.plays_checked == 1 and not bot.notes
    assert "matches_game" not in scoring


def test_mismatched_play_takes_analysis_offline():
    shadow, bot, scoring = _play(25)
    assert not shadow.ok and shadow.plays_mismatched == 1
    assert scoring["matches_game"] is False and scoring["game_total"] == scoring["total"] + 25
    assert bot.notes and bot.notes[0][0] == "analysis_offline"
