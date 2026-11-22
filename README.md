# balatro-bot

Tools for building and evaluating Balatro bots, with a focus on **exactness**:

- **Exact headless engine** (`engine/`): runs the real game's Lua code from *your own copy* of
  Balatro 1.0.1o, with no window, graphics, audio or saves, and drives it with
  [balatrobot](https://github.com/coder/balatrobot)'s action API. Same JSON game state as the real
  game. Use it as a reference to validate faster simulators.
- **Real-game harness** (`balatro_bot/game/`): launches headless real-game instances with an isolated
  mod folder and a dedicated save profile, and restores your settings afterwards.
- **jackdaw backend** (`balatro_bot/sim/`): the same interface over
  [jackdaw](https://github.com/TylerFlar/jackdaw-balatro), a fast Rust simulator (MIT).
- **Differential testing** (`scripts/`): compare any two backends action by action, including RNG
  streams and hidden joker state.
- **Parallel game servers** (`scripts/serve.py`): N headless games, one per port, served over balatrobot's
  HTTP API from the exact engine or jackdaw, so any balatrobot client (such as BalatroLLM) can drive
  them.
- **Baselines** (`scripts/eval_fast.py`): jackdaw's smart agent, a random policy, and a search player
  (`balatro_bot/search/`) that picks plays and discards by simulating possible worlds. It never peeks
  at the seed, the draw order or face-down cards: every decision is a function of what a player sees
  (tested). Runs many games in parallel and reports win rates with confidence intervals.
- **LLM pipeline** (`scripts/run_llm.py`): plays games through
  [BalatroLLM](https://github.com/coder/balatrollm) against those servers in parallel, with:
  - optional analysis tools for the model, answering only from what a player can see:
    - while playing a hand: exact scores (guaranteed, best case, average), the chance to clear the blind,
      the best joker order and the best order to play cards in (`optimize_jokers`, `optimize_hand`);
    - shop and pack decisions are left to the model's own judgment;
  - a check that the server served the requested model;
  - token accounting (and costs with a budget cap, if a paid server is ever used);
  - infrastructure failures (outages, dead servers) kept out of the results and retried;
  - a mock model for testing without a GPU.
- **Fine-tuning data** (`scripts/export_sft.py`): logged LLM games become chat-format training examples.
- **Game records and a replay viewer** (`viewer/index.html`): every LLM game writes a record of every
  step, the model's reasoning and each analysis tool's full answer; `scripts/record_game.py` records
  non-LLM players. Step through a game ante by ante, see the board, the deck and the decision, and keep
  notes on any step.
- **RL** (`scripts/rl_ppo.py`): runs jackdaw's own MaskablePPO train/evaluate examples on Black Deck /
  Gold Stake. The scripts are unmodified; the launcher only sets the deck and stake.

> **No game files are included.** You need your own copy of Balatro. See
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Not affiliated with LocalThunk or Playstack.

## Status

| Check | Result |
|---|---|
| Engine vs real game (balatrobot), Black Deck / Gold Stake | identical in all 45 seed runs (25 scripted/random, 20 pack-heavy mixed) |
| jackdaw v0.2.6 vs engine, Black/Gold, RNG streams + hidden joker state every step | 400/400 seeds identical through game over (random, smart policies) |
| Same, assisted to ante 8 (money top-ups, score set just short of the blind) | 78/80 identical (62 runs won); 2 jackdaw differences, below |
| Same, other decks/stakes (Red/White, Plasma/Gold, Abandoned/Blue, Erratic/Gold) | 160/160 identical |
| Same, collector policy (buys, opens and uses everything) assisted to ante 8 | 99/100 identical after a balatrobot fix (80 runs won); 1 jackdaw difference |
| Content exercised by those runs | all 28 bosses, 24 tags, 32 vouchers, 15 booster kinds, editions, enhancements, seals, stickers; 144/150 jokers (not Cavendish or 5 legendaries); 50/52 consumables (not Eris, The Soul) |

Details in [engine/README.md](engine/README.md).

Speed (Apple M2): engine ~120–160 ms/action (a reference, not a training environment); jackdaw
~1.1 ms/action through the same API with a Python policy, ~250k–420k steps/s as a native vector env.

Baselines on Black Deck / Gold Stake (1,000 paired `dev` seeds): every policy so far wins **0%**
(95% CI 0–0.4%).

| Policy | Mean ante reached | Died on ante 1 | Best ante |
|---|---|---|---|
| random (jackdaw) | 1.01 | 99% | 3 |
| smart agent (jackdaw) | 1.37 | 72% | 4 |
| search (`balatro_bot/search`) | 1.89 | 36% | 5 |

Search versus the smart agent: +0.52 ante (95% CI +0.47..+0.57). The search decides only on what a
player sees (no face-down cards, draw order or seed); it takes ~3 s per game on one core.

How results are produced is described in [docs/EVAL_PROTOCOL.md](docs/EVAL_PROTOCOL.md); what the LLM
and fine-tuning stages need is in [docs/COMPUTE_PLAN.md](docs/COMPUTE_PLAN.md).

Tested on macOS (Apple Silicon). Windows and Linux paths are implemented but untested.

## Setup

Requires Python 3.13+, [uv](https://docs.astral.sh/uv/), and Balatro installed through Steam.

```bash
uv sync --group dev --extra sim --extra llm --extra train   # sim: jackdaw; llm: BalatroLLM; train: PPO
uv run python scripts/setup.py all          # balatrobot (pinned) + engine host (macOS) or game source
uv run python scripts/setup.py real-game    # only if you will drive the real game
uv run pytest                               # unit tests + engine smoke tests (skipped without the game)
```

Paths default to the standard Steam locations; override with `BALATRO_GAME_DIR`, `BALATRO_SRC`
and `BALATRO_SAVE_DIR` (see `balatro_bot/paths.py`).

## Use

```python
from balatro_bot.engine.client import EngineClient        # exact engine (your game's code)
from balatro_bot.sim.jackdaw_backend import JackdawClient  # fast simulator

with EngineClient() as eng:
    eng.start("BLACK", "GOLD", "MYSEED")
    gs = eng.call("select")
    gs = eng.call("play", {"cards": [0, 1, 2, 3, 4]})
    print(gs["round"]["chips"], gs["money"])
```

Both accept balatrobot v1.5.2 requests (`start`, `select`, `skip`, `play`, `discard`, `cash_out`,
`buy`, `sell`, `reroll`, `pack`, `use`, `rearrange`, `next_round`, `gamestate`, `set`).

```bash
uv run python scripts/eval_fast.py --policy smart search --n 400 --workers 6        # baselines
uv run python scripts/serve.py --backend engine --n 8                               # 8 game servers
uv run python scripts/run_llm.py --mock --n 4 --parallel 4                          # LLM pipeline, mock model
uv run python scripts/run_llm.py --model Qwen/Qwen3-8B --n 10                       # model served on the local GPU (vLLM)
bash scripts/gpu/run_on_gpu.sh                                                     # all of it on an NVIDIA GPU (docs/GPU_RUN.md)
uv run python scripts/export_sft.py --min-ante 3                                    # fine-tuning dataset
uv run python scripts/rl_ppo.py fetch                                               # jackdaw's PPO examples
uv run python scripts/rl_ppo.py train --deck BLACK --stake GOLD -- --timesteps 2000000
uv run python scripts/compare_jackdaw.py --seeds 100 --policy smart                  # jackdaw vs engine
uv run python scripts/compare_jackdaw.py --seeds 40 --assist-money 100 --assist-chips  # reach ante 8
uv run python scripts/compare_jackdaw.py --seeds 100 --policy collector --assist-money 100 --assist-chips \
    --coverage-out runtime/logs/coverage.json                                        # rare content
uv run python scripts/validate_engine.py --seeds 10                                   # engine vs real game
uv run python scripts/bench_jackdaw.py --mode vector --envs 256
```

BalatroLLM isn't on PyPI; the `llm` extra installs it from its v1.1.1 git tag (commit pinned in `uv.lock`).

## Reviewing games

Every LLM game writes `record.jsonl` next to BalatroLLM's logs (`runtime/llm_runs/...`): one line per
request sent to the game, in order, with who decided it (the model, a policy or the harness), the
resulting game state, for each played hand how it scored (hand, level, chips x mult, which cards scored
and every joker/card/edition effect in order, computed on an identical copy of the game), and for model
decisions the reasoning, every analysis tool call with its full result, the model's final answer (the
target for fine-tuning), tokens, and the ids of the exact prompts in BalatroLLM's `requests.jsonl`.
Records are written as the game goes, so a game in progress can be opened.

```bash
uv run python scripts/record_game.py --policy search --assist-money 60 --assist-chips   # record a bot game
uv run python scripts/view_record.py                                                    # open the viewer
```

The viewer (`viewer/index.html`, no dependencies) is laid out like a run-history mod:
- **left:** the run (won/lost, assisted, seed, player) and every action in plain words, grouped by blind
  and shop ("Played Flush +852", "Bought Blueprint −$10"), with search;
- **middle:** what happened (for a hand: `chips × mult = score`, the cards with the non-scoring ones faded,
  and a step-by-step scoring table), then the board before the action: money, hands, discards, blind
  progress, the three blinds with effects and skip tags, jokers and consumables with their effects,
  editions and stickers, the hand as cards, shop/packs/voucher with prices, the deck as counts, hand levels;
- **right:** who decided (model, bot, assist or the harness), the reasoning, each analysis tool's answer as
  a table with the chosen option marked, the model's final answer, tokens and time, invalid answers, and
  your own notes;
- **Summary** (`S`): result, a blind-by-blind table (target, scored, best hand, hands and discards used),
  money over the run, best hands, final build and vouchers, every shop, jokers bought and sold, and model
  statistics.

Keys: `↑`/`↓` step, `Shift` jumps between decisions, `[`/`]` between blinds and shops, `S` summary,
`N` note, `/` search. Notes stay in your browser and can be exported. The page also opens a record
dropped onto it (`.jsonl` or `.jsonl.gz`), without the script. The layout takes ideas from
[Antelytics](https://thunderstore.io/c/balatro/p/Chrono/Antelytics/) (made for multiplayer; this one is
for single-player runs); no code or art is shared.

## Things worth knowing

- **Balatro on macOS is not seed-reproducible for two rules.** Its LuaJIT (2.1.1700008891) seeds
  string hashing per launch, so `pairs()` order changes between launches; To Do List's target hand
  and Orbital Tag's hand depend on it. `engine/lua/determinism.lua` pins the order to LuaJIT 2.0.5's
  (the one jackdaw uses) in both the engine and real-game runs.
- **Animation timing is part of the rules.** Removing delays breaks booster packs (cards are placed
  ~1.3 s after opening, after the state machine rebuilds the pack area), so the engine advances the
  game's own timers like balatrobot's fast headless mode.
- **The engine needs the game's exact LuaJIT (2.1.1700008891).** Seeded picks use `math.random`, which
  changed in later LuaJIT releases (Homebrew's 2.1.1794386141 diverged on half of 20 seeds). macOS uses
  the game's own library; elsewhere `scripts/setup.py luajit` builds that version.
- **Four balatrobot requests can hang forever:** `pack` after a booster opened by a skip tag, `buy`
  of a Celestial pack whose first card is Black Hole, `pack` (taking a card) from such a pack, and
  `sell` of Invisible Joker. All are fixed in the engine and in the real-game mod copy
  (`engine/balatrobot_fixes.json`).
- **balatrobot's game state includes face-down cards' identities** (flagged `state.hidden`).
  BalatroLLM's prompt hides them; any other prompt or tool must too. The game itself counts face-down
  hand cards as still in the deck, which is how the search and tools treat them.
- **jackdaw v0.2.6 differs from the game in two cases found so far**, both when Ceremonial Dagger
  destroys a joker at blind select (assisted Black/Gold runs; the real game was replayed to confirm):
  - destroying a joker that changes hand size: jackdaw deals the first hand with the old size (Turtle
    Bean: 14 cards instead of 9, seed `JDA01010`; Troubadour: 10 instead of 8, `JDA02028`; Merry Andy
    under The Manacle: 6 instead of 7, `JDC04019`, not yet replayed on the real game);
  - with Riff-raff: the game frees the destroyed joker before Riff-raff creates its jokers, so it can
    create the same joker again (Abstract Joker on seed `JDA02034`, in balatrobot's fast mode and at
    normal speed); jackdaw still counts it as owned and draws a different one (Mail-In Rebate).

  Everything else matched; see the validation table.
- **Text fields differ by design** between the real game (localized descriptions) and jackdaw (names);
  comparisons skip them. An LLM agent on jackdaw needs its own descriptions.

## Acknowledgements and references

This project builds directly on:

- **[Balatro](https://store.steampowered.com/app/2379780/Balatro/)** by LocalThunk, published by Playstack. The engine runs
  the game's own code from your copy; none of it is in this repository.
- **[balatrobot](https://github.com/coder/balatrobot)** (Coder; S1M0N38 and contributors, MIT). Its
  JSON-RPC endpoints and gamestate view run inside the engine, with four request-completion fixes
  (`engine/balatrobot_fixes.json`).
- **[jackdaw](https://github.com/TylerFlar/jackdaw-balatro)** (TylerFlar and contributors, MIT): the
  fast simulator behind `balatro_bot/sim/`, the game servers and the analysis tools. The comparison
  scripts and baselines use its own `SmartPolicy`, `RandomPolicy` and `smart_agent`. The search player
  is built on its `clone`, `preview_play` and snapshot API. `engine/lua/determinism.lua` uses jackdaw's `PAIRS_ORDER` (LuaJIT 2.0.5's hand order).
  Not comparing text fields follows jackdaw's own lockstep validation.
- **[BalatroLLM](https://github.com/coder/balatrollm)** (Coder; S1M0N38 and contributors, MIT): the
  LLM harness (prompts, strategies, client, logs), extended in `balatro_bot/agent/llm_runner.py`. Its run
  logs are also what BalatroBench analyzes.
- **[LuaJIT](https://github.com/LuaJIT/LuaJIT)** (Mike Pall, MIT): the engine runs on the version the game
  ships, 2.1.1700008891.
- **[rxi/json.lua](https://github.com/rxi/json.lua)** (MIT), vendored in `engine/lua/vendor/`.
- **[lovely-injector](https://github.com/ethangreen-dev/lovely-injector)** (MIT) and
  **[Steamodded](https://github.com/Steamodded/smods)** (GPL-3.0): required by balatrobot for the
  real-game harness. They are used from your own install and never copied here.

Related work, with no code used here:
- [balatro-headless](https://github.com/bapbapbapbap/balatro-headless) independently runs the game's
  Lua headless in a similar way.
- [Attol8/balatro-ai](https://github.com/Attol8/balatro-ai) (AGPL-3.0) gives an LLM precomputed exact
  analysis.

## License

MIT for this repository's code; third-party licenses in [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).
