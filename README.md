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
  (`balatro_bot/search/`) that picks plays and discards by simulating possible worlds without peeking
  at the seed. Runs many games in parallel and reports win rates with confidence intervals.
- **LLM pipeline** (`scripts/run_llm.py`): plays games through
  [BalatroLLM](https://github.com/coder/balatrollm) against those servers in parallel, with:
  - optional analysis tools for the model (exact score ranges, chance to clear the blind);
  - a check that the provider served the requested model;
  - cost accounting and a budget cap;
  - a mock model for testing without an API key.
- **Fine-tuning data** (`scripts/export_sft.py`): logged LLM games become chat-format training examples.
- **RL** (`scripts/rl_ppo.py`): runs jackdaw's own MaskablePPO train/evaluate examples on Black Deck /
  Gold Stake. The scripts are unmodified; the launcher only sets the deck and stake.

> **No game files are included.** You need your own copy of Balatro. See
> [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md). Not affiliated with LocalThunk or Playstack.

## Status

| Check | Result |
|---|---|
| Engine vs real game (balatrobot), Black Deck / Gold Stake | identical on 25 seeds (scripted and random policies) |
| jackdaw v0.2.6 vs engine, Black Deck / Gold Stake | identical on 200 seeds through game over (antes 1–5) |
| Late game (antes 6–8), RNG streams + hidden joker state every step | see [engine/README.md](engine/README.md) |

Speed (Apple M2): engine ~160 ms/action (a reference, not a training environment); jackdaw
~1.1 ms/action through the same API with a Python policy, ~250k–420k steps/s as a native vector env.

Baselines on Black Deck / Gold Stake (1,000 paired `dev` seeds): every policy so far wins **0%**
(95% CI 0–0.4%).

| Policy | Mean ante reached | Died on ante 1 | Best ante |
|---|---|---|---|
| random (jackdaw) | 1.01 | 99% | 3 |
| smart agent (jackdaw) | 1.37 | 72% | 4 |
| search (`balatro_bot/search`) | 1.91 | 38% | 5 |

Search versus the smart agent: +0.54 ante (95% CI +0.49..+0.60).

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
uv run python scripts/run_llm.py --label kimi --n 10 --parallel 4 --max-cost 25     # needs MOONSHOT_API_KEY
uv run python scripts/export_sft.py --min-ante 3                                    # fine-tuning dataset
uv run python scripts/rl_ppo.py fetch                                               # jackdaw's PPO examples
uv run python scripts/rl_ppo.py train --deck BLACK --stake GOLD -- --timesteps 2000000
uv run python scripts/compare_jackdaw.py --seeds 100 --policy smart                  # jackdaw vs engine
uv run python scripts/compare_jackdaw.py --seeds 40 --assist-money 100 --assist-chips  # reach ante 8
uv run python scripts/validate_engine.py --seeds 10                                   # engine vs real game
uv run python scripts/bench_jackdaw.py --mode vector --envs 256
```

BalatroLLM isn't on PyPI; the `llm` extra installs it from its v1.1.1 git tag (commit pinned in `uv.lock`).

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
- **Two balatrobot requests can hang forever:** `pack` after a booster opened by a skip tag, and `buy`
  of a Celestial pack whose first card is Black Hole. Both are fixed in the engine and in the real-game
  mod copy (`engine/balatrobot_fixes.json`).
- **Text fields differ by design** between the real game (localized descriptions) and jackdaw (names);
  comparisons skip them. An LLM agent on jackdaw needs its own descriptions.

## Acknowledgements and references

This project builds directly on:

- **[Balatro](https://store.steampowered.com/app/2379780/Balatro/)** by LocalThunk, published by Playstack. The engine runs
  the game's own code from your copy; none of it is in this repository.
- **[balatrobot](https://github.com/coder/balatrobot)** (Coder; S1M0N38 and contributors, MIT). Its
  JSON-RPC endpoints and gamestate view run unmodified inside the engine. Two request-completion
  fixes are listed in `engine/balatrobot_fixes.json`.
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
