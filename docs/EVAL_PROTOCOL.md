# Evaluation protocol

Target: **Black Deck, Gold Stake**, Balatro 1.0.1o. Primary metric: win rate (beat the ante-8 boss).

## Seeds
- `dev` (`balatro_bot/eval/seeds.py`): tuning, debugging, prompt and tool changes.
- `heldout`: final numbers only. Never look at heldout results while changing anything.
- Seeds use the game's own alphabet, so any seed can be typed into the real game to replay it.
- Every configuration plays the **same seeds**, so comparisons are paired.

## Where games run
| Player | Backend | Why |
|---|---|---|
| LLM (Kimi via BalatroLLM) | exact engine (`scripts/serve.py --backend engine`) | real description text in prompts; speed irrelevant (latency-bound) |
| Analysis tools for the LLM | jackdaw shadow in lockstep | exact score ranges and clear odds |
| Fast policies (smart, search, PPO) | jackdaw | 1000s of games per hour |
| Spot checks | real game via balatrobot | ground truth; slow and occasionally hangs |

## Metrics (`balatro_bot/eval/report.py`)
- **Win rate**, with a Wilson 95% interval. Wilson stays meaningful near 0% and at small n.
- **Ante reached**, as a mean and a distribution. At low win rates it's the more sensitive metric.
- **Cost per game** in USD, from logged token usage × provider prices.
- **Paired comparisons:**
  - win-rate difference with an exact McNemar test on the shared seeds;
  - mean-ante difference with a bootstrap 95% interval.

How precise a win rate is (95% interval half-width, at a true rate of 5%):

| games | ±   |
|---|---|
| 100 | ~4.3 pts |
| 400 | ~2.1 pts |
| 1000 | ~1.4 pts |

## Arms
1. `kimi-notools`: BalatroLLM's default strategy, game actions only.
2. `kimi-tools`: the same, plus `score_plays` and `estimate_clear_chance` (`balatro_bot/agent/tools.py`).
3. Baselines: jackdaw `smart`, `search` (`balatro_bot/search`), `random`.

## Procedure
1. Run the mock pipeline end to end: `scripts/run_llm.py --mock` (no key, no cost).
2. Pilot: 10 dev seeds per arm with `--max-cost` set. From the pilot, read tokens, latency and cost
   per game, plus any request parameters Moonshot rejects.
3. Scale on dev. Pick n from the table above and the measured cost per game.
4. Final: heldout seeds, once per arm, with the configuration frozen beforehand.

## Leakage rules (seeded games know the future)
- Tools never reveal upcoming randomness. Scores are given as guaranteed minimum and best case
  (`rolls="low"`/`"high"`), never the actual roll.
- Searches and odds resample hidden state in every sampled world: the unseen deck order, every RNG
  stream, and the seed itself, from which streams not used yet are derived.
- No tool exposes the draw order.
- Known gap: face-down cards already in hand are visible to sampled worlds.

## Pinned versions
Balatro 1.0.1o; balatrobot v1.5.2 (with `engine/balatrobot_fixes.json`); jackdaw 0.2.6; BalatroLLM
v1.1.1; LuaJIT 2.1.1700008891 (the game's own); determinism shim for `pairs()` order.

## Known limitations of BalatroLLM v1.1.1 (as used here)
- **Never skips blinds**, and cashes out automatically. Skip-tag strategy is out of scope for these
  arms.
- **Prompts** are its default strategy text. A Black/Gold-specific strategy can go in `strategies/`.
- **Pacing:** one decision per LLM call (plus the local tool loop when enabled), and a 0.5 s pause per
  decision.
