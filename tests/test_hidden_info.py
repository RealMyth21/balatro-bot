"""Decisions and analysis tools use only what a player can see.

Face-down hand cards (The House, The Wheel, The Mark, The Fish) and the draw-pile order are hidden:
giving the same visible state a different hidden truth must not change any decision or tool answer.
Also: Cerulean Bell's forced card is in every candidate, and the tools' shadow notices any drift.
"""

from __future__ import annotations

import copy
import json
import random
from collections import Counter

import pytest

pytest.importorskip("jackdaw")

from jackdaw.cli.smart_agent import smart_agent  # noqa: E402
from jackdaw.core import Game  # noqa: E402
from jackdaw.engine.actions import Discard, GamePhase, SelectBlind  # noqa: E402

from balatro_bot.agent.tools import Shadow, estimate_clear_chance, score_plays  # noqa: E402
from balatro_bot.search.round_search import (  # noqa: E402
    RoundSearchPolicy,
    candidate_actions,
    determinize,
    forced_index,
    ranked_plays,
    visible_ranked_plays,
)

# Black Deck / Gold Stake seeds where jackdaw's smart agent meets each boss with cards face down
BOSS_SEEDS = {"bl_wheel": "FDQQQQ27", "bl_mark": "FDQQQQ28", "bl_fish": "FDQQQQ39", "bl_house": "FDQQQQ53"}


def _reach(seed: str, boss: str) -> dict:
    g = Game.new(back="b_black", stake=8, seed=seed)
    for _ in range(600):
        if g.phase == GamePhase.SELECTING_HAND and g.blind and g.blind.key == boss and any(c.face_down for c in g.hand):
            return g.to_dict()
        g.step(smart_agent(g, g.legal_actions()))
    raise AssertionError(f"{seed} never reached {boss} with face-down cards")


@pytest.fixture(scope="module")
def states() -> dict[str, dict]:
    return {boss: _reach(seed, boss) for boss, seed in BOSS_SEEDS.items()}


def _ident(c: dict) -> tuple:
    return (c["front"], c["center"], json.dumps(c.get("edition")), c.get("seal"))


def _hidden(snap: dict) -> list[int]:
    return [i for i, c in enumerate(snap["areas"]["hand"]) if c["facing"] == "back"]


def _other_truth(snap: dict, boss: str, seed: int) -> dict:
    """Same visible state, different face-down cards and deck order."""
    alt = copy.deepcopy(snap)
    ok = (lambda c: c["base"]["id"] in (11, 12, 13) and c["center"] != "m_stone") if boss == "bl_mark" else bool
    rng = random.Random(seed)
    for h in _hidden(alt):
        j = rng.choice([j for j, c in enumerate(alt["areas"]["deck"]) if ok(c)])
        a, b = alt["areas"]["hand"][h], alt["areas"]["deck"][j]
        alt["areas"]["hand"][h], alt["areas"]["deck"][j] = {**b, "facing": "back"}, {**a, "facing": b["facing"]}
    rng.shuffle(alt["areas"]["deck"])
    return alt


def test_worlds_resample_face_down_cards(states):
    for boss, snap in states.items():
        hand, hid = snap["areas"]["hand"], _hidden(snap)
        pool = Counter(_ident(c) for c in snap["areas"]["deck"]) + Counter(_ident(hand[i]) for i in hid)
        sampled = set()
        rng = random.Random(1)
        for _ in range(60):
            w = determinize(snap, rng).to_dict()
            h2, d2 = w["areas"]["hand"], w["areas"]["deck"]
            assert [_ident(h2[i]) for i in range(len(h2)) if i not in hid] == \
                   [_ident(hand[i]) for i in range(len(hand)) if i not in hid]
            assert Counter(_ident(c) for c in d2) + Counter(_ident(h2[i]) for i in hid) == pool
            sampled.update(_ident(h2[i]) for i in hid)
            if boss == "bl_mark":  # only face cards are flipped by The Mark
                assert all(h2[i]["base"]["id"] in (11, 12, 13) for i in hid)
        assert len(sampled) > 5, boss


def test_decisions_depend_only_on_visible_state(states):
    leaked = 0
    for boss, snap in states.items():
        g1 = Game.from_dict(snap)
        for t in range(2):
            g2 = Game.from_dict(_other_truth(snap, boss, t))
            leaked += ranked_plays(g1)[:3] != ranked_plays(g2)[:3]  # the true state does differ
            assert visible_ranked_plays(g1, random.Random(5)) == visible_ranked_plays(g2, random.Random(5))
            assert candidate_actions(g1, rng=random.Random(5)) == candidate_actions(g2, rng=random.Random(5))
            assert RoundSearchPolicy(worlds=8, seed=3).act(g1) == RoundSearchPolicy(worlds=8, seed=3).act(g2)
            assert score_plays(g1) == score_plays(g2)
            assert estimate_clear_chance(g1, worlds=8) == estimate_clear_chance(g2, worlds=8)
    assert leaked  # otherwise the test would prove nothing


def test_score_plays_hides_face_down_hand_types(states):
    snap = states["bl_fish"]
    out = score_plays(Game.from_dict(snap), top=20)
    hid = set(_hidden(snap))
    assert out["face_down_cards"] == sorted(hid)
    for p in out["plays"]:
        assert (p["hand"] == "unknown (face-down cards)") == bool(hid & set(p["cards"]))
        assert p["min_score"] <= p["max_score"]


def test_cerulean_bell_forced_card_in_every_candidate():
    g = Game.new(back="b_black", stake=8, seed="TESTSEED")
    g.step(SelectBlind())
    d = g.to_dict()
    d["areas"]["hand"][2]["ability"]["forced_selection"] = True
    g = Game.from_dict(d)
    assert forced_index(g) == 2
    cands = candidate_actions(g)
    assert all(2 in c.card_indices for c in cands) and any(isinstance(c, Discard) for c in cands)
    assert all(r["p_clear"] is not None for r in RoundSearchPolicy(worlds=8).evaluate(g, cands))
    assert all(2 in p["cards"] for p in score_plays(g)["plays"])
    bad = estimate_clear_chance(g, [{"play": [0, 1]}], worlds=4)["results"][0]
    assert bad["error"].startswith("must include card 2")


def test_shadow_detects_drift():
    from jackdaw.bridge.backend import SimBackend

    real = SimBackend()
    shadow = Shadow()
    for method, params in (("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"}), ("select", None)):
        real.handle(method, params)
        shadow.apply(method, params)
    assert shadow.check(real.view())
    shadow.sim.handle("add", {"key": "c_pluto"})  # the review's repro: Pluto on the shadow only
    shadow.sim.handle("use", {"consumable": 0})
    assert not shadow.check(real.view()) and "drifted" in shadow.reason

    shadow = Shadow()  # an action the game accepted but the shadow rejects is drift too
    shadow.apply("start", {"deck": "BLACK", "stake": "GOLD", "seed": "TESTSEED"})
    shadow.apply("play", {"cards": [0]})  # still at blind select in the shadow
    assert not shadow.ok and "rejected" in shadow.reason
