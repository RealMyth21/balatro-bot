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
2. `kimi-tools`: the same, plus `score_plays` and `estimate_clear_chance` while playing a hand and
   `evaluate_options` in shops and packs (`balatro_bot/agent/tools.py`).

`evaluate_options` plays each option's imagined futures to the end of the run with a default player
(`--option-rollout search`, or the ~10x faster but weaker `smart`), so its numbers reflect that
player. It reports the raw chance of reaching each ante and of winning, plus a score that weighs
later antes more; the weights are a setting (`--option-weights`, default doubling per ante), not part
of the measurement.
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
- **Face-down cards** (The House, The Wheel, The Mark, The Fish) are hidden, as in the prompt (BalatroLLM
  prints "the card is face down"). The game's own "remaining cards" view counts them as still in the
  deck, so sampled worlds draw them again from that pool (under The Mark, from the face cards, since
  only face cards are flipped). With face-down cards in hand, `score_plays` gives average scores and
  no hand type for plays that include them. Jokers flipped by Amber Acorn are reshuffled among
  themselves.
- Hidden state is put in a canonical order before sampling, so every decision and tool answer is a
  function of what the player sees: `tests/test_hidden_info.py` gives the same visible state several
  different hidden truths and requires identical answers.
- Simplification: the game re-sorts the hand by rank after each draw, so a face-down card's slot hints
  at its rank. Sampled worlds ignore that hint (they know slightly less than an attentive player).
- balatrobot's raw game state *does* include face-down cards' identities (flagged `state.hidden`).
  Any prompt other than BalatroLLM's must hide them itself.

## Recording results
- Each row records its configuration: the policy label (`search[adaptive=True,worlds=16]`, LLM labels
  plus model), deck and stake. Resuming skips only seeds finished under the same configuration.
- **Errors are sorted by cause** (`balatro_bot/eval/report.py`):
  - the policy's own failure (BalatroLLM gives up after 3 invalid actions in a row, a step cap) is a
    loss at the ante reached;
  - infrastructure errors (provider outage, model mismatch, budget stop, a game server's internal
    error, content jackdaw can't run) are left out of the statistics, reported as a count, and retried
    on the next run.
- If a seed was played more than once, its last scored game counts.
- **Budget:** `--max-cost` covers everything spent under a configuration, earlier runs included. It is
  checked before every LLM call, and a new game starts only if the running games are expected to fit.
  Calls that time out may still be billed; the provider's dashboard has the exact total.

## Pinned versions
Balatro 1.0.1o; balatrobot v1.5.2 (with the 4 fixes in `engine/balatrobot_fixes.json`); jackdaw 0.2.6; BalatroLLM
v1.1.1; LuaJIT 2.1.1700008891 (the game's own); determinism shim for `pairs()` order.

## Known limitations of BalatroLLM v1.1.1 (as used here)
- **Never skips blinds**, and cashes out automatically. Skip-tag strategy is out of scope for these
  arms.
- **Prompts** are its default strategy text. A Black/Gold-specific strategy can go in `strategies/`.
- **Pacing:** one decision per LLM call (plus the local tool loop when enabled), and a 0.5 s pause per
  decision.
