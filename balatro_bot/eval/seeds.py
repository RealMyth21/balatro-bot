"""Fixed seed lists for evaluation.

`dev` is for tuning and debugging; `heldout` is only for reporting final numbers (never tune on it).
Seeds use the game's own alphabet (no 0/O), so any seed can be typed into the real game to replay it.
They are generated deterministically, so the lists never change.
"""

from __future__ import annotations

import random

ALPHABET = "123456789ABCDEFGHIJKLMNPQRSTUVWXYZ"
_SALT = {"dev": 20261112, "heldout": 1112202611, "smoke": 7}


def seeds(split: str = "dev", n: int = 1000) -> list[str]:
    if split not in _SALT:
        raise ValueError(f"unknown split {split!r}; use one of {sorted(_SALT)}")
    rng = random.Random(_SALT[split])
    out: list[str] = []
    seen: set[str] = set()
    while len(out) < n:
        s = "".join(rng.choice(ALPHABET) for _ in range(8))
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out


def check_disjoint() -> None:
    a, b = set(seeds("dev", 5000)), set(seeds("heldout", 5000))
    assert not (a & b), "dev and heldout seed lists overlap"
