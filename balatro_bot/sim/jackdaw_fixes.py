"""Corrections for jackdaw v0.2.6 where it differs from the real game, applied around its requests.

Both differences found so far happen when Ceremonial Dagger destroys a joker at blind select (see the
README). The real game removes the destroyed joker before the round's first hand is drawn and before
Riff-raff creates its jokers; jackdaw removes it afterwards. `FixedSimBackend` edits jackdaw's game
state just before each `select` so the outcome is the real game's:

  hand size   if the destroyed joker changes hand size (Juggler, Merry Andy, Turtle Bean, Troubadour,
              Stuntman), its bonus is taken off before the draw, so the first hand has the real size;
  Riff-raff   the destroyed joker is freed (taken out of `used_jokers`) before Riff-raff creates its
              jokers, so it can be created again, as in the game. The game's Dagger destroys in an
              event that runs before the events of the jokers to its right, and frees the joker then.

Both were checked against the exact engine on the seeds where they were found
(tests/test_jackdaw_fixes.py). Anything else jackdaw gets wrong is still caught by the checks that
compare it with the real game (Shadow in agent/tools.py, scripts/record_game.py).
"""

from __future__ import annotations

from typing import Any

from jackdaw.bridge.backend import SimBackend

# Parts of the gamestate view that live in jackdaw's session rather than its game state: a session
# rebuilt from an edited game loses them until the next shop, so they are carried over.
_SESSION_KEYS = ("shop", "packs", "vouchers")


def _hand_size_bonus(joker: dict) -> int:
    """How much this joker adds to hand size, as jackdaw takes it off when the joker leaves."""
    ab = joker.get("ability") or {}
    extra = ab.get("extra") if isinstance(ab.get("extra"), dict) else {}
    key = joker.get("center")
    if key in ("j_juggler", "j_merry_andy"):
        return int(ab.get("h_size") or 0)
    if key in ("j_turtle_bean", "j_troubadour"):
        return int(extra.get("h_size") or 0)
    if key == "j_stuntman":
        return -int(extra.get("h_size") or 0)
    return 0


def _clear_hand_size(joker: dict) -> None:
    ab = joker.get("ability") or {}
    if "h_size" in ab:
        ab["h_size"] = 0
    if isinstance(ab.get("extra"), dict) and "h_size" in ab["extra"]:
        ab["extra"]["h_size"] = 0


def sliced_by_dagger(jokers: list[dict]) -> list[tuple[int, int]]:
    """(dagger, victim) positions for each Ceremonial Dagger that destroys its right neighbour at blind
    select: not debuffed, not itself being destroyed, and the neighbour not eternal."""
    out, slicing = [], set()
    for i, j in enumerate(jokers):
        if j.get("center") != "j_ceremonial" or j.get("debuff") or i in slicing:
            continue
        n = i + 1
        if n < len(jokers) and not (jokers[n].get("stickers") or {}).get("eternal") and n not in slicing:
            slicing.add(n)
            out.append((i, n))
    return out


def fix_before_select(state: dict[str, Any]) -> list[str]:
    """Edit a jackdaw game dict (Game.to_dict) at blind select so `select` plays out as in the real
    game; returns what was changed (empty: nothing to do)."""
    jokers = state["areas"]["jokers"]
    cuts = sliced_by_dagger(jokers)
    if not cuts:
        return []
    changes = []
    keys = [j.get("center") for j in jokers]
    used = state.get("sets", {}).get("used_jokers")
    for _, n in cuts:
        victim = jokers[n]
        bonus = _hand_size_bonus(victim)
        if bonus:
            state["game"]["hand_size"] -= bonus
            _clear_hand_size(victim)
            changes.append(f"hand size {bonus:+d} from {victim['center']} taken off before the draw")
        # freed as the game frees it (no other copy left), so creations during this select may pick it
        if isinstance(used, list) and victim["center"] in used and keys.count(victim["center"]) == 1:
            used.remove(victim["center"])
            changes.append(f"{victim['center']} freed before jokers to its right act")
    return changes


def fixed_for_select(game):
    """For code that steps jackdaw games directly: the game to step a SelectBlind on (a corrected copy
    when one of the fixes applies, else the same game)."""
    from jackdaw.core import Game

    state = game.to_dict()
    return Game.from_dict(state) if fix_before_select(state) else game


class FixedSimBackend(SimBackend):
    """jackdaw's SimBackend with the corrections above applied before each `select`. describe=True also
    fills in the effect text the real game shows (balatro_bot/sim/descriptions.py), for anything a model
    reads; the analysis shadow doesn't need it."""

    def __init__(self, *args, describe: bool = False, **kwargs):
        super().__init__(*args, **kwargs)
        self.describe = describe
        self.fixes: list[str] = []  # every correction applied, for logs and tests
        self._carry: dict[str, Any] = {}  # session-only view parts lost by a rebuild, until the next shop

    def handle(self, method: str, params: dict[str, Any] | None) -> dict[str, Any]:
        if method == "select" and self.state == "BLIND_SELECT":
            self._fix_select()
        return self._with_carry(super().handle(method, params))

    def view(self) -> dict[str, Any]:
        return self._with_carry(super().view())

    def _with_carry(self, data: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(data, dict) or "state" not in data:
            return data
        if self._carry:
            if all(k in data for k in self._carry):  # the session has its own again (a new shop)
                self._carry = {}
            else:
                data = {**data, **{k: v for k, v in self._carry.items() if k not in data}}
        if self.describe and (game := self.game) is not None:
            from balatro_bot.sim.descriptions import add_effects

            data = add_effects(data, game.to_dict())
        return data

    def _fix_select(self) -> None:
        from jackdaw import _core
        from jackdaw.core import Game

        game = self.game
        if game is None:
            return
        state = game.to_dict()
        changes = fix_before_select(state)
        if changes:
            before = self.view()
            self._session = _core.BotSession.from_game(Game.from_dict(state)._g)
            self._carry = {k: before[k] for k in _SESSION_KEYS if k in before}
            self.fixes.extend(changes)
