"""Analysis tools an LLM agent can call during a hand ("with tools" arm).

The game itself runs on any backend (normally our exact engine, which provides real description text).
A jackdaw shadow run receives every request in lockstep (shown identical to the engine) and powers:

  score_plays           score preview of the best plays: the guaranteed minimum and the maximum score
                        over chance effects (Lucky cards, Glass breaks, Bloodstone, ...) and the average
                        money each play makes. Never reveals the actual upcoming rolls.
  estimate_clear_chance chance to clear the current blind for candidate plays/discards, over worlds
                        with the unseen deck reshuffled and all randomness resampled (no seed peeking).

  optimize_jokers       the joker order that makes the best play score most, and the rearrange to apply it
  optimize_hand         the best plays with the order their cards should score in (and the rearrange)

Each tool can be switched on or off (parse_tools): the baseline offers only score_plays; shop and pack
decisions are left entirely to the model. Tools are offered only while choosing a play or discard
(tools_for_state). Scores are never the actual upcoming roll.

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

TOOL_SCHEMAS: list[dict] = [
    {"type": "function", "function": {
        "name": "score_plays",
        "description": ("Score preview for the best plays from your current hand, including all jokers, "
                        "enhancements, editions, seals and the boss blind. Returns up to `top` plays with card "
                        "indices (0-based, hand order), hand type, the minimum score (guaranteed), the maximum "
                        "score (if every chance effect such as Lucky cards goes your way) and the average money "
                        "the play makes, plus the chips still needed to clear the blind. With face-down cards in "
                        "hand, scores are averages over what those cards could be. Does not take an action."),
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
TOOL_SCHEMAS += [
    {"type": "function", "function": {
        "name": "optimize_jokers",
        "description": ("Finds the joker order that makes your best play score most (jokers trigger left to right: "
                        "x-Mult after +Mult multiplies more; Blueprint and Brainstorm copy their neighbour). Returns "
                        "the best order, the score gain, and the `rearrange` to send to apply it. Does not take an "
                        "action."),
        "parameters": {"type": "object", "properties": {}, "required": []}}},
    {"type": "function", "function": {
        "name": "optimize_hand",
        "description": ("Finds your best plays including the order the played cards score in (cards score left to "
                        "right; jokers like Hanging Chad and Photograph care which card is first). For each play: "
                        "card indices, scoring order, guaranteed/best-case/average score, and the `rearrange` of your "
                        "hand to send before playing (the play's indices stay the same). Use optimize_jokers first if "
                        "joker order matters. Does not take an action."),
        "parameters": {"type": "object", "properties": {
            "top": {"type": "integer", "minimum": 1, "maximum": 10, "description": "how many plays to return"}},
            "required": []}}},
]
TOOL_NAMES = {t["function"]["name"] for t in TOOL_SCHEMAS}
# Which tools the model is offered: named sets, or any comma-separated list of tool names (parse_tools).
# Every tool's answer is still computed for the game record (viewer_analysis), never shown to the model.
TOOLSETS = {"none": (), "baseline": ("score_plays",), "all": tuple(sorted(TOOL_NAMES))}


def parse_tools(spec) -> tuple[str, ...]:
    """The tools to offer: None/False/"" -> none, True -> all, or a string of comma-separated set names
    and tool names, e.g. "baseline", "score_plays,optimize_jokers", "all"."""
    if not spec:
        return ()
    if spec is True:
        return TOOLSETS["all"]
    names: list[str] = []
    for part in str(spec).replace(" ", "").split(","):
        if not part:
            continue
        if part in TOOLSETS:
            names += TOOLSETS[part]
        elif part in TOOL_NAMES:
            names.append(part)
        else:
            raise ValueError(f"unknown tool or tool set {part!r}: use {', '.join(sorted(TOOLSETS))} or "
                             f"{', '.join(sorted(TOOL_NAMES))}")
    return tuple(dict.fromkeys(names))
_STATES = {"score_plays": HAND_STATES, "estimate_clear_chance": HAND_STATES, "optimize_jokers": HAND_STATES,
           "optimize_hand": HAND_STATES}


def tools_for_state(state: str | None, names=None) -> list[dict]:
    """The analysis tools that apply in this game state (only `names`, if given)."""
    return [t for t in TOOL_SCHEMAS if state in _STATES[t["function"]["name"]]
            and (names is None or t["function"]["name"] in names)]


class Shadow:
    """A jackdaw run kept in lockstep with the real backend."""

    def __init__(self):
        from jackdaw.bridge.backend import SimBackend

        self.sim = SimBackend()
        self.ok = True
        self.reason = ""
        self.plays_checked = 0  # plays whose predicted score was compared with the real game's
        self.plays_mismatched = 0

    def verify_play(self, predicted: float, real: float, cards) -> bool:
        """After a play the real game accepted: did the shadow score it the same? A mismatch means
        jackdaw has a rule wrong here, so the analysis stops for the rest of the game."""
        self.plays_checked += 1
        if abs(float(predicted) - float(real)) <= 1e-6 * max(1.0, abs(float(real))):
            return True
        self.plays_mismatched += 1
        return self._fail(f"shadow scored play {list(cards)} as {predicted:g}, the game as {real:g}")

    def summary(self) -> dict:
        """For the game's result row: whether the analysis stayed online, and the plays checked."""
        return {"online": self.ok, "reason": self.reason or None, "plays_checked": self.plays_checked,
                "plays_mismatched": self.plays_mismatched}

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


def _chance_info(game, cores: list, avg_cores: list, cards: tuple) -> dict:
    """Money (guaranteed / best / average) and the Lucky and Glass odds of one play, from what the
    player sees: `cores` are the real game (nothing hidden) or worlds with face-down cards redrawn,
    `avg_cores` worlds with all randomness redrawn."""
    from jackdaw.core import Game

    def money(cs, rolls):
        vals = []
        for c in cs:
            try:
                vals.append(Game(c).preview_play(cards, rolls=rolls).dollars)
            except Exception:  # noqa: BLE001  an illegal play
                return None
        return round(sum(vals) / len(vals), 2) if vals else None

    out = {"money": {"min": money(cores, "low"), "max": money(cores, "high"), "avg": money(avg_cores, None)}}
    scoring = game._g.scoring_cards(list(cards))
    hand = game.hand
    pos = [i for i in (scoring[1] if scoring else []) if not hand[i].face_down]
    oops = 2 ** sum(1 for j in game.jokers if j.key == "j_oops")  # Oops! All 6s doubles every chance
    lucky = [i for i in pos if hand[i].key == "m_lucky" and not hand[i].debuff]
    glass = [i for i in pos if hand[i].key == "m_glass" and not hand[i].debuff]
    if lucky:
        pm, pd = min(1.0, oops / 5), min(1.0, oops / 15)
        out["lucky"] = {"cards": lucky, "chance_mult_each": round(pm, 3), "chance_money_each": round(pd, 3),
                        "chance_any_mult": round(1 - (1 - pm) ** len(lucky), 3),
                        "chance_any_money": round(1 - (1 - pd) ** len(lucky), 3)}
    if glass:
        pb = min(1.0, oops / 4)
        out["glass"] = {"cards": glass, "chance_break_each": round(pb, 3),
                        "chance_any_breaks": round(1 - (1 - pb) ** len(glass), 3)}
    return out


# Futures (unseen deck reshuffled, every chance roll redrawn) behind the average money and the chance
# of beating the minimum: 512 keeps the average money within ~$0.40 for three Lucky cards, in ~0.5 s.
AVG_WORLDS = 512


def _avg_cores(game, worlds: int, seed: int) -> list:
    from balatro_bot.search.round_search import WorldSampler

    sampler = WorldSampler(game)
    rng = random.Random(seed + 7919)
    return [sampler.sample_core(rng) for _ in range(worlds)]


def _beats_min(avg_cores: list, cards: tuple, low: float) -> float | None:
    from balatro_bot.search.round_search import _preview_total

    totals = [_preview_total(c, cards, None) for c in avg_cores]
    if any(t is None for t in totals):
        return None
    return round(sum(t > low + 1e-6 for t in totals) / len(totals), 3)


def _preview(play: dict, detail: bool) -> dict:
    """What the model sees of one play: cards, hand, minimum and maximum score, average money. The
    record (detail) keeps everything: average score, money range, Lucky/Glass odds."""
    if detail:
        return play
    return {"cards": play["cards"], "hand": play["hand"], "min_score": play["min_score"],
            "max_score": play["max_score"], "avg_money": play["money"]["avg"]}


def score_plays(game, top: int = 8, worlds: int = 8, seed: int = 0, detail: bool = False) -> dict:
    from balatro_bot.search.optimize import average_scores
    from balatro_bot.search.round_search import WorldSampler, has_hidden, ranked_plays, visible_ranked_plays

    top = max(1, min(int(top or 8), 20))
    plays = []
    if not has_hidden(game):
        ranked = ranked_plays(game, rolls="low")[:top]
        avg = average_scores(game, [c for _, c in ranked], seed=seed)
        avg_cores = _avg_cores(game, AVG_WORLDS, seed)
        for score, cards in ranked:
            high = game.preview_play(cards, rolls="high")
            plays.append(_preview({"cards": list(cards), "hand": high.hand_type, "min_score": score,
                                   "max_score": high.total, "avg_score": avg.get(cards),
                                   "chance_above_min": _beats_min(avg_cores, cards, score),
                                   **_chance_info(game, [game._g], avg_cores, cards)}, detail))
        return {**_blind_info(game), "plays": plays}
    # Face-down cards: score over sampled identities (the same worlds for both bounds), and give the
    # hand type only for plays made of face-up cards (it does not depend on the hidden ones).
    sampler = WorldSampler(game)
    rng = random.Random(seed)
    cores = [sampler.sample_core(rng) for _ in range(worlds)]
    face_down = {i for i, c in enumerate(game.hand) if c.face_down}
    low = visible_ranked_plays(game, rng, worlds, rolls="low", cores=cores)
    high = dict((c, s) for s, c in visible_ranked_plays(game, rng, worlds, rolls="high", cores=cores))
    avg = average_scores(game, [c for _, c in low[:top]], seed=seed)
    avg_cores = _avg_cores(game, AVG_WORLDS, seed)
    for score, cards in low[:top]:
        known = not face_down.intersection(cards)
        plays.append(_preview({
            "cards": list(cards),
            "hand": game.preview_play(cards, rolls="low").hand_type if known else "unknown (face-down cards)",
            "min_score": round(score, 1), "max_score": round(high.get(cards, score), 1),
            "avg_score": avg.get(cards), "chance_above_min": _beats_min(avg_cores, cards, score),
            **_chance_info(game, cores, avg_cores, cards)}, detail))
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


def analysis_snapshot(shadow: Shadow, view: dict):
    """A private copy of the shadow's game for the record's analysis, after checking the shadow against
    the real game: the analysis can then run alongside the model's own tool calls without sharing state.
    None when not choosing a play or discard; a dict with an error when the shadow is offline."""
    if view.get("state") not in HAND_STATES:
        return None
    if not shadow.check(view):
        return {"error": f"analysis unavailable: {shadow.reason}"}
    from jackdaw.core import Game

    return Game(shadow.sim.game._g.clone())


def viewer_analysis(snapshot) -> dict | None:
    """The detailed analysis of a hand decision for the game record (viewer and training data), never
    shown to the model: every tool's full answer, the clear-chance search included. `snapshot` comes
    from analysis_snapshot."""
    if snapshot is None or isinstance(snapshot, dict):
        return snapshot
    return {name: json.loads(_answer(snapshot, name, args, detail=True))
            for name, args in (("score_plays", {"top": 10}), ("estimate_clear_chance", {}),
                               ("optimize_jokers", {}), ("optimize_hand", {}))}


def run_tool(shadow: Shadow, view: dict, name: str, arguments: str | dict, detail: bool = False) -> str:
    """Execute a tool call; always returns a JSON string for the tool message. detail: the full answer
    for the game record (score_plays adds average score, money range and Lucky/Glass odds)."""
    try:
        args = json.loads(arguments) if isinstance(arguments, str) else dict(arguments or {})
    except json.JSONDecodeError:
        return json.dumps({"error": "arguments were not valid JSON"})
    if name not in TOOL_NAMES:
        return json.dumps({"error": f"unknown tool {name!r}"})
    if view.get("state") not in _STATES[name]:
        return json.dumps({"error": f"{name} only works while choosing a play or discard"})
    if not shadow.check(view):
        return json.dumps({"error": f"analysis unavailable: {shadow.reason}"})
    return _answer(shadow.sim.game, name, args, detail)


def _answer(game, name: str, args: dict, detail: bool = False) -> str:
    """One tool's answer on a game, as a JSON string."""
    try:
        if name == "score_plays":
            return json.dumps(score_plays(game, args.get("top", 8), detail=detail))
        if name in ("optimize_jokers", "optimize_hand"):
            from balatro_bot.search.optimize import optimize_hand, optimize_jokers

            if name == "optimize_jokers":
                return json.dumps({**_blind_info(game), **optimize_jokers(game)})
            top = max(1, min(int(args.get("top") or 5), 10))
            return json.dumps({**_blind_info(game), **optimize_hand(game, top=top)})
        return json.dumps(estimate_clear_chance(game, args.get("candidates")))
    except Exception as e:  # noqa: BLE001
        return json.dumps({"error": f"{type(e).__name__}: {e}"})
