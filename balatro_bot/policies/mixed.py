"""Mixed policy for coverage: sensible hands (so runs survive into later antes), random choices
everywhere else (skips, shop buys incl. packs/vouchers/consumables, rerolls, sells, pack picks,
consumable use). Used for engine validation and throughput benchmarks."""

from __future__ import annotations

import random
from dataclasses import dataclass, field

from balatro_bot.policies.random_policy import RandomPolicy
from balatro_bot.policies.scripted import ScriptedPolicy


@dataclass
class MixedPolicy:
    rng: random.Random = field(default_factory=random.Random)
    name: str = "mixed"

    def __post_init__(self):
        self._rand = RandomPolicy(rng=self.rng)
        self._script = ScriptedPolicy()

    def act(self, gs: dict) -> tuple[str, dict | None]:
        if gs["state"] == "SELECTING_HAND":
            cons = (gs.get("consumables") or {}).get("cards", [])
            if cons and self.rng.random() < 0.2:
                hand = gs["hand"]["cards"]
                k = self.rng.randint(1, min(2, len(hand)))
                return "use", {"consumable": self.rng.randrange(len(cons)),
                               "cards": sorted(self.rng.sample(range(len(hand)), k))}
            return self._script.act(gs)
        return self._rand.act(gs)
