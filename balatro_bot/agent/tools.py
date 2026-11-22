"""Analysis tools an LLM agent can call during a hand ("with tools" arm).

The game itself runs on any backend (normally our exact engine, which provides real description text).
A jackdaw shadow run receives every request in lockstep (shown identical to the engine) and powers:

  score_plays           exact score range of the best plays: the guaranteed minimum and the maximum
                        over chance effects (Lucky cards, Glass breaks, Bloodstone, ...). Never reveals
                        the actual upcoming rolls.
  estimate_clear_chance chance to clear the current blind for candidate plays/discards, over worlds
                        with the unseen deck reshuffled and all randomness resampled (no seed peeking).
  evaluate_options      in the shop or an open pack: how far the run gets after each candidate plan
                        ("buy card 0, then leave"), as the chance of reaching each ante and of winning,
                        over shared imagined futures played to the end (balatro_bot/search/option_eval.py).

score_plays and estimate_clear_chance are offered while choosing a play or discard, evaluate_options in
the shop and in packs (tools_for_state).

Both answer only from what the player sees (the prompt shows face-down cards as "face down"): with
face-down cards in hand, scores are averages over what those cards could be, and hand types of plays
that include them are not given.

Before every tool answer the shadow's whole game state (minus description text) is compared with the
real view, and any shadow error on an action the real game accepted counts as drift; after drift the
tools stop answering (and say so) rather than give wrong numbers.
"""

from __future__ import annotations

import json
import random

from balatro_bot.validation.diff import first_diff, rules_view

HAND_STATES = ("SELECTING_HAND",)
OPTION_STATES = ("SHOP", "SMODS_BOOSTER_OPENED")  # BalatroLLM selects blinds itself, so not BLIND_SELECT

TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "score_plays",
        "description": ("Exact score range for the best plays from your current hand, including all jokers, "
                        "enhancements, editions, seals and the boss blind. Returns up to `top` plays with card "
                        "indices (0-based, hand order), hand type, guaranteed minimum score and best-case score, "
                        "plus the chips still needed to clear the blind. With face-down cards in hand, scores "
                        "are averages over what those cards could be. Does not take an action."),
        "parameters": {"type": "object", "properties": {
            "top": {"type": "integer", "minimum": 1, "maximum": 20, "description": "how many plays to return"}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "estimate_clear_chance",
        "description": ("Simulates many possible futures (unseen deck reshuffled, randomness resampled) to "
                        "estimate the chance of clearing the current blind if you take each candidate action and "
                        "then keep playing your best hands. Give candidates as {\"play\": [indices]} or "
                        "{\"discard\": [indices]}; omit them to evaluate an automatic shortlist. Does not take an "
                        "action."),
        "parameters": {"type": "object", "properties": {
            "candidates": {"type": "array", "maxItems": 10, "items": {"type": "object", "properties": {
                "play": {"type": "array", "items": {"type": "integer"}},
                "discard": {"type": "array", "items": {"type": "integer"}}}}}},
            "required": []}}},
    {"type": "function", "function": {
        "name": "evaluate_options",
        "description": ("In the shop or an open booster pack: compares candidate plans by how far the run gets "
                        "afterwards, playing many possible futures (unseen deck and future shops resampled) to the "
                        "end with a default player. A plan is a list of steps using the game actions, e.g. "
                        "[{\"buy\": {\"card\": 0}}, {\"next_round\": {}}] or [{\"pack\": {\"card\": 1}}]; in "
                        "the shop a plan is the whole visit (the shop is left after it). Omit candidates for an "
                        "automatic shortlist (each purchase, or each pack pick, plus doing nothing). Returns per plan "
                        "the chance to reach each ante 2-8 and to win, mean ante reached, and a score that weighs "
                        "later antes more. Does not take an action."),
        "parameters": {"type": "object", "properties": {
            "candidates": {"type": "array", "maxItems": 8, "items": {"type": "object", "properties": {
                "steps": {"type": "array", "items": {"type": "object"},
                          "description": "e.g. [{\"sell\": {\"joker\": 1}}, {\"buy\": {\"card\": 0}}]"}},
                "required": ["steps"]}}},
            "required": []}}},
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}
_STATES = {"score_plays": HAND_STATES, "estimate_clear_chance": HAND_STATES, "evaluate_options": OPTION_STATES}


def tools_for_state(state: str | None) -> list[dict]:
    """The analysis tools that apply in this game state."""
    return [t for t in TOOL_SCHEMAS if state in _STATES[t["function"]["name"]]]


class Shadow:
    """A jackdaw run kept in lockstep with the real backend."""

    def __init__(self):
        from jackdaw.bridge.backend import SimBackend

        self.sim = SimBackend()
        self.ok = True
        self.reason = ""

    def apply(self, method: str, params: dict | None) -> None:
        """Mirror a request the real game has just accepted."""
        if not self.ok:
            return
        if method in ("gamestate", "health", "screenshot", "rpc.discover"):
            return
        clean = {k: v for k, v in (params or {}).items() if k != "reasoning"}
        try:
            self.sim.handle(method, clean or None)
        except Exception as e:  # noqa: BLE001  the real game accepted it, so the shadow has drifted
            self._fail(f"shadow rejected {method} that the game accepted: {e}")

    def check(self, view: dict) -> bool:
        """Compare the shadow's whole game state with the real game's view (description text aside)."""
        if not self.ok:
            return False
        try:
            mine = self.sim.view()
        except Exception as e:  # noqa: BLE001
            return self._fail(f"shadow view failed: {e}")
        diff = first_diff(rules_view(view), rules_view(mine), labels=("game", "shadow"))
        if diff:
            return self._fail(f"shadow drifted: {diff}"[:500])
        return True

    def _fail(self, reason: str) -> bool:
        self.ok, self.reason = False, reason
        return False


def _blind_info(game) -> dict:
    from balatro_bot.search.round_search import forced_index

    target = float(game.blind.chips) if game.blind else 0.0
    have = float(game.chips)
    info = {"blind_chips": target, "chips_so_far": have, "chips_needed": max(0.0, target - have),
            "hands_left": game.hands_left, "discards_left": game.discards_left}
    face_down = [i for i, c in enumerate(game.hand) if c.face_down]
    if face_down:
        info["face_down_cards"] = face_down
    forced = forced_index(game)
    if forced is not None:
        info["forced_card"] = forced  # Cerulean Bell: every play and discard must include it
    return info


def score_plays(game, top: int = 8, worlds: int = 8, seed: int = 0) -> dict:
    from balatro_bot.search.round_search import WorldSampler, has_hidden, ranked_plays, visible_ranked_plays

    top = max(1, min(int(top or 8), 20))
    plays = []
    if not has_hidden(game):
        for score, cards in ranked_plays(game, rolls="low")[:top]:
            high = game.preview_play(cards, rolls="high")
            plays.append({"cards": list(cards), "hand": high.hand_type, "min_score": score,
                          "max_score": high.total})
        return {**_blind_info(game), "plays": plays}
    # Face-down cards: score over sampled identities (the same worlds for both bounds), and give the
    # hand type only for plays made of face-up cards (it does not depend on the hidden ones).
    sampler = WorldSampler(game)
    rng = random.Random(seed)
    cores = [sampler.sample_core(rng) for _ in range(worlds)]
    face_down = {i for i, c in enumerate(game.hand) if c.face_down}
    low = visible_ranked_plays(game, rng, worlds, rolls="low", cores=cores)
    high = dict((c, s) for s, c in visible_ranked_plays(game, rng, worlds, rolls="high", cores=cores))
    for score, cards in low[:top]:
        known = not face_down.intersection(cards)
        plays.append({"cards": list(cards),
                      "hand": game.preview_play(cards, rolls="low").hand_type if known else "unknown (face-down cards)",
                      "min_score": round(score, 1), "max_score": round(high.get(cards, score), 1)})
    return {**_blind_info(game), "plays": plays,
            "note": f"scores are averages over {worlds} possibilities for the face-down cards"}


def estimate_clear_chance(game, candidates: list[dict] | None = None, worlds: int = 32, seed: int = 0) -> dict:
    from jackdaw.engine.actions import Discard, PlayHand

    from balatro_bot.search.round_search import RoundSearchPolicy, candidate_actions, forced_index

    search = RoundSearchPolicy(worlds=worlds, seed=seed)
    forced = forced_index(game)
    if candidates:
        cands = []
        for c in candidates[:10]:
            if c.get("play"):
                cands.append(PlayHand(card_indices=tuple(int(i) for i in c["play"])))
            elif c.get("discard"):
                cands.append(Discard(card_indices=tuple(int(i) for i in c["discard"])))
    else:
        cands = candidate_actions(game, rng=random.Random(seed))
    results = []
    for r in search.evaluate(game, cands):
        a = r["action"]
        kind = "play" if isinstance(a, PlayHand) else "discard"
        entry = {kind: list(a.card_indices)}
        if r["p_clear"] is None:
            entry["error"] = (f"must include card {forced} (forced by the boss)"
                              if forced is not None and forced not in a.card_indices else "not a legal action now")
        else:
            entry.update(clear_chance=round(r["p_clear"], 3), mean_progress=round(r["progress"], 3))
        results.append(entry)
    results.sort(key=lambda e: e.get("clear_chance", -1), reverse=True)
    return {**_blind_info(game), "worlds": worlds, "results": results}


def run_tool(shadow: Shadow, view: dict, name: str, arguments: str | dict, evaluator=None) -> str:
    """Execute a tool call; always returns a JSON string for the tool message. evaluator: the
    OptionEvaluator settings for evaluate_options (worlds, rollout player, score weights)."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments were not valid JSON"})
    if name not in TOOL_NAMES:
        return json.dumps({"error": f"unknown tool {name!r}"})
    if view.get("state") not in _STATES[name]:
        where = "while choosing a play or discard" if name in ("score_plays", "estimate_clear_chance") \
            else "in the shop or an open booster pack"
        return json.dumps({"error": f"{name} only works {where}"})
    if not shadow.check(view):
        return json.dumps({"error": f"analysis unavailable: {shadow.reason}"})
    game = shadow.sim.game
    try:
        if name == "score_plays":
            return json.dumps(score_plays(game, args.get("top", 8)))
        if name == "evaluate_options":
            from balatro_bot.search.option_eval import OptionEvaluator

            return json.dumps((evaluator or OptionEvaluator()).evaluate(game, args.get("candidates")))
        return json.dumps(estimate_clear_chance(game, args.get("candidates")))
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
