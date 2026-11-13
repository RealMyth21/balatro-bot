"""Round search: decides plays and discards by simulating possible worlds (jackdaw).

At each play/discard decision it samples worlds consistent with what a player can see: the unseen
draw pile is reshuffled, every RNG stream is re-drawn, and the seed (from which not-yet-used streams
are derived) is replaced. So no seeded future leaks into the decision. Each candidate play/discard is
applied in each world and the round is finished with a greedy rollout; the candidate with the
highest estimated chance of clearing the blind is chosen (ties: more progress, then cheaper clears).

Everything else (blind select, shop, packs, consumables, rearranging) is delegated to jackdaw's
smart agent (`jackdaw.cli.smart_agent`), which this policy extends rather than replaces.

Known limitation: face-down cards already in hand are visible to the worlds.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from jackdaw.cli.smart_agent import smart_agent
from jackdaw.core import Game, IllegalActionError
from jackdaw.engine.actions import Discard, GamePhase, PlayHand

SEED_ALPHABET = "123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"


def determinize(snapshot: dict[str, Any], rng: random.Random) -> Game:
    """A world consistent with the player's information: same visible state, resampled hidden state."""
    d = dict(snapshot)
    areas = dict(d["areas"])
    deck = list(areas["deck"])
    rng.shuffle(deck)
    areas["deck"] = deck
    d["areas"] = areas
    new_seed = "".join(rng.choice(SEED_ALPHABET) for _ in range(8))
    r = dict(d["rng"])
    r["streams"] = {k: round(rng.random(), 13) for k in r["streams"]}
    r["seed"] = new_seed
    r["hashed_seed"] = round(rng.random(), 13)
    d["rng"] = r
    run = dict(d["run"])
    run["seed"] = new_seed  # jackdaw requires run.seed == rng.seed
    d["run"] = run
    return Game.from_dict(d)


def _subsets(n: int, sizes: range) -> list[tuple[int, ...]]:
    return [c for k in sizes for c in combinations(range(n), k)]


def ranked_plays(game: Game, rolls: str | None = "low", sizes: range | None = None) -> list[tuple[float, tuple]]:
    """(score, card indices) for every legal play, best first. rolls='low' never leaks chance rolls."""
    n = len(game.hand)
    out = []
    for c in _subsets(n, sizes or range(1, min(5, n) + 1)):
        try:
            out.append((game.preview_play(c, rolls=rolls).total, c))
        except IllegalActionError:
            continue
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def _fast_best_play(world: Game) -> tuple | None:
    """Greedy play inside a rollout. The world is already a sample, so its own rolls are fair game.
    5-card subsets cover most best plays; small hands fall back to every subset."""
    n = len(world.hand)
    if n == 0:
        return None
    sizes = range(5, 6) if n >= 5 else range(1, n + 1)
    plays = ranked_plays(world, rolls=None, sizes=sizes)
    if not plays and n >= 5:  # e.g. a boss that forbids 5-card plays
        plays = ranked_plays(world, rolls=None, sizes=range(1, 5))
    return plays[0][1] if plays else None


def _rollout(world: Game, target: float, max_steps: int = 12) -> tuple[bool, float, int, int]:
    """Finish the round greedily. Returns (cleared, progress, hands_left, discards_left)."""
    steps = 0
    while world.phase == GamePhase.SELECTING_HAND and steps < max_steps:
        play = _fast_best_play(world)
        if play is None:
            break
        try:
            world.step(PlayHand(card_indices=play))
        except IllegalActionError:
            break
        steps += 1
    cleared = world.phase != GamePhase.SELECTING_HAND and world.phase != GamePhase.GAME_OVER
    progress = 1.0 if cleared else min(1.0, float(world.chips) / target) if target else 0.0
    return cleared, progress, world.hands_left, world.discards_left


_FACE_VALUES = {"Jack": 11, "Queen": 12, "King": 13, "Ace": 14}


def _rank_value(card) -> int:
    r = card.rank
    if r in _FACE_VALUES:
        return _FACE_VALUES[r]
    return int(r) if r and str(r).isdigit() else 0


def candidate_actions(game: Game, n_plays: int = 4, n_discards: int = 4) -> list[Any]:
    plays = ranked_plays(game, rolls="low")
    cands: list[Any] = [PlayHand(card_indices=c) for _, c in plays[:n_plays]]
    if game.discards_left > 0:
        hand = list(game.hand)
        forced = {i for i, c in enumerate(hand) if getattr(c, "forced", False)}
        by_value = sorted(range(len(hand)), key=lambda i: _rank_value(hand[i]))
        seen: set[tuple] = set()
        discards: list[tuple] = []
        for _, keep in plays[:3]:  # keep a strong play, throw the rest (lowest first)
            rest = [i for i in by_value if i not in keep and i not in forced][:5]
            discards.append(tuple(sorted(rest)))
        suits: dict[str, list[int]] = {}
        for i, c in enumerate(hand):
            suits.setdefault(str(c.suit), []).append(i)
        major = max(suits.values(), key=len) if suits else []
        if 2 <= len(major) < 5:  # chase a flush
            discards.append(tuple(sorted([i for i in by_value if i not in major and i not in forced][:5])))
        discards.append(tuple(sorted([i for i in by_value if i not in forced][:5])))  # five lowest
        for d in discards:
            if d and d not in seen and len(discards) and len([x for x in cands if isinstance(x, Discard)]) < n_discards:
                seen.add(d)
                cands.append(Discard(card_indices=d))
    return cands


@dataclass
class RoundSearchPolicy:
    """Play/discard by search over sampled worlds; everything else by jackdaw's smart agent."""

    worlds: int = 16
    n_plays: int = 4
    n_discards: int = 4
    seed: int = 0
    stats: dict = field(default_factory=lambda: {"searched": 0, "delegated": 0})

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    def evaluate(self, game: Game, cands: list[Any]) -> list[dict]:
        """Estimate each candidate over sampled worlds: chance to clear the blind, mean progress
        (share of the blind's chips reached) and mean hands/discards left. Illegal candidates get
        p_clear=None."""
        target = float(game.blind.chips) if game.blind else 0.0
        snap = game.to_dict()
        worlds = [determinize(snap, self._rng) for _ in range(self.worlds)]
        out = []
        for cand in cands:
            stats = {"cleared": 0, "progress": 0.0, "hands_left": 0.0, "discards_left": 0.0}
            legal = True
            for w in worlds:
                trial = w.clone()
                try:
                    trial.step(cand)
                except IllegalActionError:
                    legal = False
                    break
                cleared, progress, hands_left, discards_left = _rollout(trial, target)
                stats["cleared"] += cleared
                stats["progress"] += progress
                stats["hands_left"] += hands_left
                stats["discards_left"] += discards_left
            n = len(worlds)
            out.append({"action": cand, "p_clear": stats["cleared"] / n if legal else None,
                        "progress": stats["progress"] / n if legal else None,
                        "hands_left": stats["hands_left"] / n if legal else None,
                        "discards_left": stats["discards_left"] / n if legal else None, "worlds": n})
        return out

    def act(self, game: Game) -> Any:
        legal = game.legal_actions()
        default = smart_agent(game, legal)
        if game.phase != GamePhase.SELECTING_HAND or not isinstance(default, (PlayHand, Discard)):
            self.stats["delegated"] += 1
            return default
        self.stats["searched"] += 1
        cands = candidate_actions(game, self.n_plays, self.n_discards)
        if len(cands) <= 1:
            return cands[0] if cands else default
        best, best_val = default, -1.0
        for r in self.evaluate(game, cands):
            if r["p_clear"] is None:
                continue
            val = r["p_clear"] + 0.01 * r["progress"] + 0.001 * r["hands_left"] + 0.0001 * r["discards_left"]
            if val > best_val:
                best, best_val = r["action"], val
        return best
