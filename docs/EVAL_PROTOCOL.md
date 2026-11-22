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
| LLM (open-weights model on the RTX 4090 via vLLM, played by BalatroLLM) | exact engine where Balatro is installed; otherwise jackdaw | the engine gives real description text in prompts; jackdaw gives names only |
| Analysis tools for the LLM | jackdaw shadow in lockstep | exact score ranges and clear odds |
| Fast policies (smart, search, PPO) | jackdaw | 1000s of games per hour |
| Spot checks | real game via balatrobot | ground truth; slow and occasionally hangs |

## Metrics (`balatro_bot/eval/report.py`)
- **Win rate**, with a Wilson 95% interval. Wilson stays meaningful near 0% and at small n.
- **Ante reached**, as a mean and a distribution. At low win rates it's the more sensitive metric.
- **Tokens and time per game**, from the logs (cost only if a paid server is used).
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
1. `<model>-baseline`: BalatroLLM's default strategy, game actions, and while playing a hand the score
   preview `score_plays` (minimum and maximum score and average money of the best plays, like a
   calculator a player could use). `scripts/run_baseline.sh`.
2. Other arms switch tools on or off (`--tools`, `TOOLS=` in `scripts/run_baseline.sh`): `none` (game
   actions only, comparable to public BalatroLLM results), or any of `optimize_jokers`, `optimize_hand`,
   `estimate_clear_chance` (`balatro_bot/agent/tools.py`). `estimate_clear_chance` is a search, not a
   calculator: report arms with it as LLM + search. No tool helps with shop and pack decisions: those
   stay the model's own reasoning. No tool ever reveals the actual upcoming roll.

3. Baselines: jackdaw `smart`, `search` (`balatro_bot/search`), `random`.

## Procedure
1. Run the mock pipeline end to end: `scripts/run_llm.py --mock` (no GPU).
2. Pilot on the RTX 4090: 10 dev seeds per arm (`scripts/gpu/run_on_gpu.sh`, `N=10`). From the pilot,
   read seconds and tokens per decision, games per hour, and how often the model's tool calls are valid.
3. Scale on dev. Pick n from the table above and the measured games per hour.
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
