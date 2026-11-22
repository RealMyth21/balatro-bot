"""Round search: decides plays and discards by simulating possible worlds (jackdaw).

At each play/discard decision it samples worlds consistent with what a player can see: the unseen
draw pile is reshuffled, every RNG stream is re-drawn, and the seed (from which not-yet-used streams
are derived) is replaced. So no seeded future leaks into the decision. Each candidate play/discard is
applied in each world and the round is finished with a greedy rollout; the candidate with the
highest estimated chance of clearing the blind is chosen (ties: more progress, then cheaper clears).

Everything else (blind select, shop, packs, consumables, rearranging) is delegated to jackdaw's
smart agent (`jackdaw.cli.smart_agent`), which this policy extends rather than replaces.

Hidden information follows the game's own model: face-down hand cards (The House, The Wheel, The
Mark, The Fish) count as part of the unseen pool, as in the game's "remaining cards" view, so the
worlds draw them again together with the draw pile (under The Mark they are drawn from the face
cards, since only face cards are flipped). Jokers flipped by Amber Acorn are reshuffled among
themselves. Plays are only ranked on what the player sees: with face-down cards in hand, each play's
score is its mean over sampled worlds. Cerulean Bell's forced card is part of every candidate.

Simplification: the game re-sorts the whole hand after each draw, so a face-down card's slot hints at
its rank; the worlds ignore that hint (a player could know slightly more, never less).
"""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from itertools import combinations
from typing import Any

from jackdaw import _core  # the Rust core jackdaw.core.Game wraps (pinned jackdaw version)
from jackdaw.cli.smart_agent import smart_agent
from jackdaw.core import Game, IllegalActionError
from jackdaw.engine.actions import Discard, GamePhase, PlayHand, encode_action

SEED_ALPHABET = "123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"


_FACE_IDS = (11, 12, 13)


def _face_down(cards: list[dict]) -> list[int]:
    return [i for i, c in enumerate(cards) if c.get("facing") == "back"]


def _face_down_rule(snapshot: dict[str, Any]):
    """What the player knows about face-down hand cards beyond "unseen": under The Mark only face
    cards are flipped (all cards are face cards with Pareidolia, so then nothing is known)."""
    blind = snapshot.get("blind") or {}
    if blind.get("key") != "bl_mark" or blind.get("disabled"):
        return None
    if any(j.get("center") == "j_pareidolia" for j in snapshot["areas"].get("jokers") or []):
        return None
    return lambda c: c.get("center") != "m_stone" and (c.get("base") or {}).get("id") in _FACE_IDS


def _canon(card: dict) -> str:
    return json.dumps({k: v for k, v in card.items() if k != "facing"}, sort_keys=True)


def _json_form(obj: Any) -> Any:
    """A Game.to_dict() snapshot back in jackdaw's JSON form (non-finite floats as {"$f": ...})."""
    if isinstance(obj, float) and not math.isfinite(obj):
        return {"$f": "nan" if math.isnan(obj) else ("inf" if obj > 0 else "-inf")}
    if isinstance(obj, dict):
        return {k: _json_form(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_json_form(v) for v in obj]
    return obj


_SLOT = '"@@world:{}@@"'


class WorldSampler:
    """Samples worlds consistent with the player's information: same visible state, resampled hidden
    state. Hidden things are put in a canonical order first, so the worlds drawn for a given RNG
    state depend only on what the player can see (not on, say, the true deck order).

    Built once per decision. Each world is the snapshot's JSON with only the changed parts (deck,
    hidden hand cards and jokers, RNG, seed) spliced in, loaded straight into jackdaw's core: about
    3x faster than going through Game.to_dict/from_dict."""

    def __init__(self, snapshot: dict[str, Any] | Game):
        if isinstance(snapshot, Game):
            snapshot = json.loads(snapshot._g.snapshot())
        else:
            snapshot = _json_form(snapshot)
        areas = snapshot["areas"]
        self.hand = areas["hand"]
        self.jokers = areas.get("jokers") or []
        self.hidden = _face_down(self.hand)
        self.facing = [c.get("facing") for c in areas["deck"]]
        self.pool = sorted(list(areas["deck"]) + [self.hand[i] for i in self.hidden], key=_canon)
        self.rule = _face_down_rule(snapshot) if self.hidden else None
        self.flipped = _face_down(self.jokers)
        self.flipped_jokers = sorted((self.jokers[i] for i in self.flipped), key=_canon)
        self.rng_base = dict(snapshot["rng"])
        self.stream_keys = list(self.rng_base["streams"])
        run = dict(snapshot["run"])
        run["seed"] = "@@world:seed@@"
        self.run_json = json.dumps(run)
        self.hand_json, self.jokers_json = json.dumps(self.hand), json.dumps(self.jokers)
        d = dict(snapshot)
        d["areas"] = {**areas, "deck": "@@world:deck@@", "hand": "@@world:hand@@", "jokers": "@@world:jokers@@"}
        d["rng"], d["run"] = "@@world:rng@@", "@@world:run@@"
        self.template = json.dumps(d)

    def sample_core(self, rng: random.Random):
        """One world, as a jackdaw core game (what Game wraps)."""
        pool = list(self.pool)
        rng.shuffle(pool)
        hand_json = self.hand_json
        if self.hidden:  # face-down hand cards are drawn again from the unseen pool
            hand = list(self.hand)
            k = len(self.hidden)
            picks = list(range(k))
            if self.rule is not None:
                fits = [j for j, c in enumerate(pool) if self.rule(c)][:k]
                if len(fits) == k:  # else a card changed after it was flipped; fall back
                    picks = fits
            for slot, j in zip(self.hidden, picks, strict=True):
                hand[slot] = {**pool[j], "facing": "back"}
            taken = set(picks)
            pool = [c for j, c in enumerate(pool) if j not in taken]
            hand_json = json.dumps(hand)
        deck_json = json.dumps([{**c, "facing": f} for c, f in zip(pool, self.facing, strict=True)])
        jokers_json = self.jokers_json
        if len(self.flipped) > 1:  # Amber Acorn: the order of face-down jokers is unknown
            jokers = list(self.jokers)
            order = list(self.flipped_jokers)
            rng.shuffle(order)
            for i, c in zip(self.flipped, order, strict=True):
                jokers[i] = c
            jokers_json = json.dumps(jokers)
        new_seed = "".join(rng.choice(SEED_ALPHABET) for _ in range(8))
        r = dict(self.rng_base)
        r["streams"] = {k: round(rng.random(), 13) for k in self.stream_keys}
        r["seed"] = new_seed
        r["hashed_seed"] = round(rng.random(), 13)
        text = (self.template.replace(_SLOT.format("deck"), deck_json, 1)
                .replace(_SLOT.format("hand"), hand_json, 1)
                .replace(_SLOT.format("jokers"), jokers_json, 1)
                .replace(_SLOT.format("rng"), json.dumps(r), 1)
                .replace(_SLOT.format("run"), self.run_json.replace(_SLOT.format("seed"), json.dumps(new_seed), 1), 1))
        return _core.Game.from_snapshot(text)  # jackdaw requires run.seed == rng.seed (both new_seed)

    def sample(self, rng: random.Random) -> Game:
        return Game(self.sample_core(rng))


def determinize(snapshot: dict[str, Any], rng: random.Random) -> Game:
    """One world consistent with the player's information (see WorldSampler)."""
    return WorldSampler(snapshot).sample(rng)


_SUBSETS: dict[tuple, list[tuple[int, ...]]] = {}


def _subsets(n: int, sizes: range, forced: int | None = None) -> list[tuple[int, ...]]:
    """Card subsets of the given sizes; with a forced card (Cerulean Bell) only those containing it."""
    key = (n, sizes.start, sizes.stop, forced)
    out = _SUBSETS.get(key)
    if out is None:
        out = [c for k in sizes for c in combinations(range(n), k) if forced is None or forced in c]
        _SUBSETS[key] = out
    return out


def with_forced(cards, forced: int | None, limit: int = 5) -> tuple[int, ...]:
    """A selection that includes the forced card, replacing the last card if the selection is full."""
    cards = list(cards)
    if forced is not None and forced not in cards:
        cards = cards[: limit - 1] + [forced]
    return tuple(sorted(set(cards)))


# --- hot path on jackdaw's Rust core (jackdaw.core.Game._g) --------------------------------------
# Same steps as jackdaw's Game.preview_play (clone, set roll bound, step PlayHand), minus converting
# every result into Python view objects, which was most of the search's time; the score is read as
# the change in round chips. Results are identical; tests/test_sim_parallel_tools.py checks this.

_PLAY_JSON: dict[tuple, str] = {}
_SELECTING, _GAME_OVER = GamePhase.SELECTING_HAND.value, GamePhase.GAME_OVER.value


def _play_json(cards: tuple) -> str:
    s = _PLAY_JSON.get(cards)
    if s is None:
        s = _PLAY_JSON[cards] = json.dumps(encode_action(PlayHand(card_indices=cards)))
    return s


def _hand_info(core) -> tuple[int, int | None]:
    """(cards in hand, index of Cerulean Bell's forced card or None)."""
    hand = json.loads(core.view("hand"))
    forced = next((i for i, c in enumerate(hand) if (c.get("ability") or {}).get("forced_selection")), None)
    return len(hand), forced


_EXACT = float(2**52)  # round chips are integers; doubles add them exactly below this


def _preview_total(core, cards: tuple, rolls: str | None):
    trial = core.clone()
    if rolls is not None:
        trial.roll_bound = rolls
    try:
        trial.step(_play_json(cards))
    except IllegalActionError:
        return None
    # A play adds its score to the round's chips (checked against last_score on 11k previews,
    # incl. plays that clear the blind or end the run); reading two numbers beats a JSON view.
    before, after = core.chips, trial.chips
    if before < _EXACT and after < _EXACT:
        return after - before
    score = json.loads(trial.view("last_score"))
    return score["total"] if score else None


def _ranked_core(core, rolls: str | None, sizes: range | None = None,
                 info: tuple[int, int | None] | None = None) -> list[tuple[float, tuple]]:
    n, forced = info or _hand_info(core)
    out = []
    for c in _subsets(n, sizes or range(1, min(5, n) + 1), forced):
        total = _preview_total(core, c, rolls)
        if total is not None:
            out.append((total, c))
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def ranked_plays(game: Game, rolls: str | None = "low", sizes: range | None = None) -> list[tuple[float, tuple]]:
    """(score, card indices) for every legal play, best first, scored on the true state.
    rolls='low' never leaks chance rolls, but face-down cards are scored as what they really are:
    use visible_ranked_plays for anything a player decides on."""
    return _ranked_core(game._g, rolls, sizes)


def has_hidden(game: Game) -> bool:
    """Whether the round has hidden card identities (face-down hand cards or jokers)."""
    return any(c.face_down for c in game.hand) or any(j.face_down for j in game.jokers)


def visible_ranked_plays(game: Game, rng: random.Random, worlds: int = 8, rolls: str | None = "low",
                         sizes: range | None = None, cores: list | None = None) -> list[tuple[float, tuple]]:
    """ranked_plays using only what the player sees. With no hidden cards it is ranked_plays; with
    face-down cards a play's score is its mean over sampled worlds (held face-down cards matter too,
    e.g. for Baron or Shoot the Moon), so the true identities never leak."""
    if not has_hidden(game):
        return ranked_plays(game, rolls, sizes)
    if cores is None:
        sampler = WorldSampler(game)
        cores = [sampler.sample_core(rng) for _ in range(worlds)]
    n, forced = _hand_info(game._g)
    out = []
    for c in _subsets(n, sizes or range(1, min(5, n) + 1), forced):
        totals = [_preview_total(w, c, rolls) for w in cores]
        if all(t is not None for t in totals):
            out.append((sum(totals) / len(totals), c))
    out.sort(key=lambda t: t[0], reverse=True)
    return out


def _fast_best_play(core) -> tuple | None:
    """Greedy play inside a rollout. The world is already a sample, so its own rolls are fair game.
    5-card subsets cover most best plays; small hands fall back to every subset."""
    info = _hand_info(core)
    n = info[0]
    if n == 0:
        return None
    sizes = range(5, 6) if n >= 5 else range(1, n + 1)
    plays = _ranked_core(core, None, sizes, info)
    if not plays and n >= 5:  # e.g. a boss that forbids 5-card plays
        plays = _ranked_core(core, None, range(1, 5), info)
    return plays[0][1] if plays else None


def _rollout(core, target: float, max_steps: int = 12) -> tuple[bool, float, float, float]:
    """Finish the round greedily on a core game. Returns (cleared, progress, hands_left, discards_left)."""
    steps = 0
    while core.phase == _SELECTING and steps < max_steps:
        play = _fast_best_play(core)
        if play is None:
            break
        try:
            core.step(_play_json(play))
        except IllegalActionError:
            break
        steps += 1
    phase = core.phase
    cleared = phase != _SELECTING and phase != _GAME_OVER
    progress = 1.0 if cleared else min(1.0, float(core.chips) / target) if target else 0.0
    return cleared, progress, float(core.hands_left), float(core.discards_left)


_FACE_VALUES = {"Jack": 11, "Queen": 12, "King": 13, "Ace": 14}
_UNKNOWN_VALUE = 8  # a face-down card: about the average rank


def _rank_value(card) -> int:
    if card.face_down:
        return _UNKNOWN_VALUE
    r = card.rank
    if r in _FACE_VALUES:
        return _FACE_VALUES[r]
    return int(r) if r and str(r).isdigit() else 0


def forced_index(game: Game) -> int | None:
    """Cerulean Bell's forced card: every play and discard must include it."""
    return next((i for i, c in enumerate(game.hand) if c.ability.get("forced_selection")), None)


def candidate_actions(game: Game, n_plays: int = 4, n_discards: int = 4,
                      rng: random.Random | None = None) -> list[Any]:
    """The plays and discards worth searching, chosen from what the player sees."""
    rng = rng or random.Random(0)
    plays = visible_ranked_plays(game, rng)
    cands: list[Any] = [PlayHand(card_indices=c) for _, c in plays[:n_plays]]
    if game.discards_left > 0:
        hand = list(game.hand)
        forced = forced_index(game)
        by_value = sorted(range(len(hand)), key=lambda i: _rank_value(hand[i]))
        discards: list[tuple] = []
        for _, keep in plays[:3]:  # keep a strong play, throw the rest (lowest first)
            discards.append(with_forced([i for i in by_value if i not in keep][:5], forced))
        suits: dict[str, list[int]] = {}
        for i, c in enumerate(hand):
            if not c.face_down:
                suits.setdefault(str(c.suit), []).append(i)
        major = max(suits.values(), key=len) if suits else []
        if 2 <= len(major) < 5:  # chase a flush
            discards.append(with_forced([i for i in by_value if i not in major][:5], forced))
        discards.append(with_forced(by_value[:5], forced))  # five lowest
        seen: set[tuple] = set()
        for d in discards:
            if d and d not in seen and len(seen) < n_discards:
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
    adaptive: bool = True
    stats: dict = field(default_factory=lambda: {"searched": 0, "delegated": 0})
    last_eval: list[dict] | None = None  # the candidates' estimates behind the last searched decision

    def __post_init__(self):
        self._rng = random.Random(self.seed)

    def evaluate(self, game: Game, cands: list[Any], adaptive: bool = False, batch: int = 4) -> list[dict]:
        """Estimate each candidate over sampled worlds: chance to clear the blind, mean progress
        (share of the blind's chips reached) and mean hands/discards left. Illegal candidates get
        p_clear=None.

        adaptive=True samples worlds in batches and stops evaluating candidates that are clearly
        behind (their mean value plus a Hoeffding-style margin falls below the leader's mean minus
        it). Decisions stay sound and most are settled after a few batches; the reported numbers then
        come from fewer worlds for the dropped candidates. Exhaustive (adaptive=False) is used when the
        probabilities themselves are reported (the LLM tool)."""
        target = float(game.blind.chips) if game.blind else 0.0
        sampler = WorldSampler(game)
        acts = [json.dumps(encode_action(c)) for c in cands]
        n_c = len(cands)
        sums = [[0.0, 0.0, 0.0, 0.0] for _ in range(n_c)]  # cleared, progress, hands_left, discards_left
        counts = [0] * n_c
        legal = [True] * n_c
        alive = list(range(n_c))
        done = 0
        while done < self.worlds and alive:
            take = min(batch if adaptive else self.worlds, self.worlds - done)
            worlds = [sampler.sample_core(self._rng) for _ in range(take)]
            done += take
            for i in list(alive):
                for w in worlds:
                    trial = w.clone()
                    try:
                        trial.step(acts[i])
                    except IllegalActionError:
                        legal[i] = False
                        alive.remove(i)
                        break
                    cleared, progress, hands_left, discards_left = _rollout(trial, target)
                    s = sums[i]
                    s[0] += cleared
                    s[1] += progress
                    s[2] += hands_left
                    s[3] += discards_left
                    counts[i] += 1
            if adaptive and len(alive) > 1 and done >= 2 * batch:
                means = {i: self._value(sums[i], counts[i]) for i in alive}
                best = max(means.values())
                margin = math.sqrt(math.log(20) / (2 * min(counts[i] for i in alive)))
                alive = [i for i in alive if means[i] + margin >= best - margin]
        out = []
        for i, cand in enumerate(cands):
            n = counts[i]
            ok = legal[i] and n > 0
            out.append({"action": cand, "p_clear": sums[i][0] / n if ok else None,
                        "progress": sums[i][1] / n if ok else None,
                        "hands_left": sums[i][2] / n if ok else None,
                        "discards_left": sums[i][3] / n if ok else None, "worlds": n})
        return out

    @staticmethod
    def _value(s: list[float], n: int) -> float:
        return (s[0] + 0.01 * s[1] + 0.001 * s[2] + 0.0001 * s[3]) / n if n else -1.0

    def act(self, game: Game) -> Any:
        legal = game.legal_actions()
        default = smart_agent(game, legal)
        self.last_eval = None
        if game.phase != GamePhase.SELECTING_HAND or not isinstance(default, (PlayHand, Discard)):
            self.stats["delegated"] += 1
            return default
        self.stats["searched"] += 1
        cands = candidate_actions(game, self.n_plays, self.n_discards, self._rng)
        if len(cands) <= 1:
            return cands[0] if cands else default
        best, best_val = default, -1.0
        self.last_eval = self.evaluate(game, cands, adaptive=self.adaptive)
        for r in self.last_eval:
            if r["p_clear"] is None:
                continue
            val = r["p_clear"] + 0.01 * r["progress"] + 0.001 * r["hands_left"] + 0.0001 * r["discards_left"]
            if val > best_val:
                best, best_val = r["action"], val
        return best
