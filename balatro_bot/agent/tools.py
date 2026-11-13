"""Analysis tools an LLM agent can call during a hand ("with tools" arm).

The game itself runs on any backend (normally our exact engine, which provides real description text).
A jackdaw shadow run receives every request in lockstep (shown identical to the engine) and powers:

  score_plays           exact score range of the best plays: the guaranteed minimum and the maximum
                        over chance effects (Lucky cards, Glass breaks, Bloodstone, ...). Never reveals
                        the actual upcoming rolls.
  estimate_clear_chance chance to clear the current blind for candidate plays/discards, over worlds
                        with the unseen deck reshuffled and all randomness resampled (no seed peeking).

Before every tool answer the shadow is checked against the real view; if they disagree the tools
stop answering (and say so) rather than give wrong numbers.
"""

from __future__ import annotations

import json

TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "score_plays",
        "description": ("Exact score range for the best plays from your current hand, including all jokers, "
                        "enhancements, editions, seals and the boss blind. Returns up to `top` plays with card "
                        "indices (0-based, hand order), hand type, guaranteed minimum score and best-case score, "
                        "plus the chips still needed to clear the blind. Does not take an action."),
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
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}


class Shadow:
    """A jackdaw run kept in lockstep with the real backend."""

    def __init__(self):
        from jackdaw.bridge.backend import SimBackend

        self.sim = SimBackend()
        self.ok = True
        self.reason = ""

    def apply(self, method: str, params: dict | None) -> None:
        if not self.ok:
            return
        if method in ("gamestate", "health", "screenshot", "rpc.discover"):
            return
        clean = {k: v for k, v in (params or {}).items() if k != "reasoning"}
        try:
            self.sim.handle(method, clean or None)
        except Exception as e:  # noqa: BLE001  mirrors the real error; drift is caught by check()
            if method in ("start", "menu"):
                self._fail(f"shadow could not {method}: {e}")

    def check(self, view: dict) -> bool:
        """Compare the fields the tools depend on with the real game's view."""
        if not self.ok:
            return False
        try:
            mine = self.sim.view()
        except Exception as e:  # noqa: BLE001
            return self._fail(f"shadow view failed: {e}")
        keys = lambda v, a: [c.get("key") for c in ((v.get(a) or {}).get("cards") or [])]  # noqa: E731
        rnd = lambda v: (v.get("round") or {}).get("chips")  # noqa: E731
        fields = [("state", view.get("state"), mine.get("state")),
                  ("money", view.get("money"), mine.get("money")),
                  ("ante", view.get("ante_num"), mine.get("ante_num")),
                  ("chips", rnd(view), rnd(mine)),
                  ("hand", keys(view, "hand"), keys(mine, "hand")),
                  ("jokers", keys(view, "jokers"), keys(mine, "jokers"))]
        for name, a, b in fields:
            if a != b:
                return self._fail(f"shadow drifted on {name}: game={a!r} shadow={b!r}")
        return True

    def _fail(self, reason: str) -> bool:
        self.ok, self.reason = False, reason
        return False


def _blind_info(game) -> dict:
    target = float(game.blind.chips) if game.blind else 0.0
    have = float(game.chips)
    return {"blind_chips": target, "chips_so_far": have, "chips_needed": max(0.0, target - have),
            "hands_left": game.hands_left, "discards_left": game.discards_left}


def score_plays(game, top: int = 8) -> dict:
    from balatro_bot.search.round_search import ranked_plays

    low = ranked_plays(game, rolls="low")
    top = max(1, min(int(top or 8), 20))
    plays = []
    for score, cards in low[:top]:
        high = game.preview_play(cards, rolls="high")
        plays.append({"cards": list(cards), "hand": high.hand_type, "min_score": score, "max_score": high.total})
    return {**_blind_info(game), "plays": plays}


def estimate_clear_chance(game, candidates: list[dict] | None = None, worlds: int = 32, seed: int = 0) -> dict:
    from jackdaw.engine.actions import Discard, PlayHand

    from balatro_bot.search.round_search import RoundSearchPolicy, candidate_actions

    search = RoundSearchPolicy(worlds=worlds, seed=seed)
    if candidates:
        cands = []
        for c in candidates[:10]:
            if c.get("play"):
                cands.append(PlayHand(card_indices=tuple(int(i) for i in c["play"])))
            elif c.get("discard"):
                cands.append(Discard(card_indices=tuple(int(i) for i in c["discard"])))
    else:
        cands = candidate_actions(game)
    results = []
    for r in search.evaluate(game, cands):
        a = r["action"]
        kind = "play" if isinstance(a, PlayHand) else "discard"
        entry = {kind: list(a.card_indices)}
        if r["p_clear"] is None:
            entry["error"] = "not a legal action now"
        else:
            entry.update(clear_chance=round(r["p_clear"], 3), mean_progress=round(r["progress"], 3))
        results.append(entry)
    results.sort(key=lambda e: e.get("clear_chance", -1), reverse=True)
    return {**_blind_info(game), "worlds": worlds, "results": results}


def run_tool(shadow: Shadow, view: dict, name: str, arguments: str | dict) -> str:
    """Execute a tool call; always returns a JSON string for the tool message."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments were not valid JSON"})
    if name not in TOOL_NAMES:
        return json.dumps({"error": f"unknown tool {name!r}"})
    if view.get("state") != "SELECTING_HAND":
        return json.dumps({"error": "analysis tools only work while choosing a play or discard"})
    if not shadow.check(view):
        return json.dumps({"error": f"analysis unavailable: {shadow.reason}"})
    game = shadow.sim.game
    try:
        if name == "score_plays":
            return json.dumps(score_plays(game, args.get("top", 8)))
        return json.dumps(estimate_clear_chance(game, args.get("candidates")))
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
