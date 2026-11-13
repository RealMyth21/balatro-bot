"""Metrics for evaluation results (JSONL, one game per line: seed, policy, won, ante, ...).

- win rate with a Wilson 95% interval (sensible near 0% and with small n)
- antes reached: mean and distribution
- paired comparison of two policies on the seeds both played: win-rate difference with an exact
  McNemar test, and mean-ante difference with a paired bootstrap 95% interval
"""

from __future__ import annotations

import json
import math
import random
from collections import Counter
from pathlib import Path


def load(path: str | Path) -> list[dict]:
    p = Path(path)
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    den = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / den
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / den
    return (max(0.0, centre - half), min(1.0, centre + half))


def summarize(rows: list[dict]) -> dict:
    n = len(rows)
    wins = sum(bool(r.get("won")) for r in rows)
    antes = [int(r.get("ante") or 0) for r in rows]
    lo, hi = wilson(wins, n)
    return {
        "games": n,
        "wins": wins,
        "win_rate": wins / n if n else 0.0,
        "win_rate_ci95": (lo, hi),
        "mean_ante": sum(antes) / n if n else 0.0,
        "ante_hist": dict(sorted(Counter(antes).items())),
        "mean_cost_usd": (sum(r.get("cost_usd", 0.0) for r in rows) / n) if n else 0.0,
    }


def _binom_two_sided(k: int, n: int) -> float:
    """Exact two-sided binomial test p-value for k successes in n trials at p=0.5."""
    if n == 0:
        return 1.0
    probs = [math.comb(n, i) / 2**n for i in range(n + 1)]
    pk = probs[k]
    return min(1.0, sum(p for p in probs if p <= pk + 1e-15))


def paired(a_rows: list[dict], b_rows: list[dict], boot: int = 2000, seed: int = 0) -> dict:
    a = {r["seed"]: r for r in a_rows}
    b = {r["seed"]: r for r in b_rows}
    common = sorted(set(a) & set(b))
    n = len(common)
    if n == 0:
        return {"common_seeds": 0}
    a_only = sum(1 for s in common if a[s].get("won") and not b[s].get("won"))
    b_only = sum(1 for s in common if b[s].get("won") and not a[s].get("won"))
    diffs = [int(a[s].get("ante") or 0) - int(b[s].get("ante") or 0) for s in common]
    rng = random.Random(seed)
    means = sorted(sum(rng.choice(diffs) for _ in range(n)) / n for _ in range(boot))
    return {
        "common_seeds": n,
        "win_rate_a": sum(bool(a[s].get("won")) for s in common) / n,
        "win_rate_b": sum(bool(b[s].get("won")) for s in common) / n,
        "wins_only_a": a_only,
        "wins_only_b": b_only,
        "mcnemar_p": _binom_two_sided(min(a_only, b_only), a_only + b_only),
        "mean_ante_diff": sum(diffs) / n,
        "mean_ante_diff_ci95": (means[int(0.025 * boot)], means[int(0.975 * boot) - 1]),
    }


def format_summary(name: str, s: dict) -> str:
    lo, hi = s["win_rate_ci95"]
    cost = f", ${s['mean_cost_usd']:.2f}/game" if s.get("mean_cost_usd") else ""
    return (f"{name}: {s['wins']}/{s['games']} wins = {100 * s['win_rate']:.1f}% "
            f"(95% CI {100 * lo:.1f}-{100 * hi:.1f}%), mean ante {s['mean_ante']:.2f}{cost}, "
            f"antes {s['ante_hist']}")


def format_paired(a: str, b: str, p: dict) -> str:
    if not p.get("common_seeds"):
        return f"{a} vs {b}: no common seeds"
    lo, hi = p["mean_ante_diff_ci95"]
    return (f"{a} vs {b} on {p['common_seeds']} shared seeds: win rate {100 * p['win_rate_a']:.1f}% vs "
            f"{100 * p['win_rate_b']:.1f}% (only-{a} wins {p['wins_only_a']}, only-{b} wins {p['wins_only_b']}, "
            f"McNemar p={p['mcnemar_p']:.3f}); mean ante diff {p['mean_ante_diff']:+.2f} (95% CI {lo:+.2f}..{hi:+.2f})")
