"""Play one game with any policy on any backend (real game client or headless engine)."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Protocol

from balatro_bot.game.client import RPCError


class Backend(Protocol):
    def call(self, method: str, params: dict | None = None) -> dict: ...
    def menu(self) -> dict: ...
    def start(self, deck: str, stake: str, seed: str | None = None) -> dict: ...


class Policy(Protocol):
    def act(self, gs: dict) -> tuple[str, dict | None]: ...


@dataclass
class GameResult:
    seed: str
    won: bool
    ante: int
    round: int
    actions: int
    errors: int
    seconds: float
    trace: list[tuple[str, dict | None]] = field(default_factory=list)


def play_game(
    backend: Backend,
    policy: Policy,
    seed: str,
    deck: str = "BLACK",
    stake: str = "GOLD",
    max_actions: int = 3000,
    on_step: Callable[[dict, str, dict | None, dict], None] | None = None,
    keep_trace: bool = False,
) -> GameResult:
    t0 = time.perf_counter()
    backend.menu()
    gs = backend.start(deck, stake, seed)
    actions = errors = 0
    trace: list[tuple[str, dict | None]] = []
    while gs["state"] != "GAME_OVER" and actions < max_actions:
        if gs.get("won") and gs["state"] in ("ROUND_EVAL", "SHOP", "BLIND_SELECT"):
            break  # winning ante 8 is the goal; endless mode is not scored
        method, params = policy.act(gs)
        try:
            new = backend.call(method, params)
        except RPCError:
            errors += 1
            if errors > 50:
                raise
            # leave whatever sub-state we are in and keep going; if that fails too, re-read state
            try:
                if gs["state"] == "SHOP":
                    new = backend.call("next_round")
                elif gs["state"] == "SMODS_BOOSTER_OPENED":
                    new = backend.call("pack", {"skip": True})
                else:
                    raise
            except RPCError:
                new = backend.call("gamestate")
        if keep_trace:
            trace.append((method, params))
        if on_step:
            on_step(gs, method, params, new)
        gs = new
        actions += 1
    return GameResult(
        seed=seed,
        won=bool(gs.get("won")),
        ante=int(gs.get("ante_num") or 0),
        round=int(gs.get("round_num") or 0),
        actions=actions,
        errors=errors,
        seconds=time.perf_counter() - t0,
        trace=trace,
    )
