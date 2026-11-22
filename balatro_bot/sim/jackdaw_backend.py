"""jackdaw (Rust Balatro simulator, MIT, pinned v0.2.6) behind the same interface as our other
backends (call/start/menu/gamestate, raises balatro_bot.game.client.RPCError).

jackdaw implements BalatroBot v1.5.2's API in-process (SimBackend), so requests and gamestate
views have the same shape as the real game's and our exact engine's.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any

from balatro_bot.game.client import CallTiming, RPCError


@dataclass
class JackdawClient:
    timings: list[CallTiming] = field(default_factory=list)
    record_timings: bool = False

    def __post_init__(self):
        from balatro_bot.sim.jackdaw_fixes import FixedSimBackend

        self.sim = FixedSimBackend(describe=True)  # corrected jackdaw, with the game's effect text

    def call(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        from jackdaw.bridge.backend import RPCError as JdError

        t0 = time.perf_counter() if self.record_timings else 0.0
        try:
            out = self.sim.handle(method, params)
        except JdError as e:
            raise RPCError(method, e.code, e.data.get("name", "?"), e.message) from None
        finally:
            if self.record_timings:
                self.timings.append(CallTiming(method, time.perf_counter() - t0))
        return out

    def gamestate(self) -> dict:
        return self.call("gamestate")

    def menu(self) -> dict:
        return self.call("menu")

    def start(self, deck: str, stake: str, seed: str | None = None) -> dict:
        p = {"deck": deck, "stake": stake}
        if seed:
            p["seed"] = seed
        return self.call("start", p)

    def close(self) -> None:
        pass

    def __enter__(self) -> JackdawClient:
        return self

    def __exit__(self, *exc) -> None:
        self.close()
