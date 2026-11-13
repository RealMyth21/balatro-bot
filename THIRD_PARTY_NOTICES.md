# Third-party software and game content

## Balatro (not included)

Balatro is © LocalThunk, published by Playstack. **This repository contains no Balatro code or
assets.** The headless engine loads the game's Lua source at runtime from *your own installed
copy* (`BALATRO_SRC`), the same way mod loaders do. Do not commit or redistribute files from your
copy, including lovely's dumps (`runtime/mods/lovely/dump`). This project is not affiliated with
or endorsed by LocalThunk or Playstack.

## Included in this repository

| Component | Path | License |
|---|---|---|
| [rxi/json.lua](https://github.com/rxi/json.lua) | `engine/lua/vendor/json.lua` | MIT, © 2020 rxi (license header kept in the file) |
| Short excerpts of [balatrobot](https://github.com/coder/balatrobot) source, as find/replace pairs | `engine/balatrobot_fixes.json` | MIT, © 2025 Coder |
| The hand order constant from [jackdaw](https://github.com/TylerFlar/jackdaw-balatro) `crates/jackdaw-core/src/hand_eval.rs` (`PAIRS_ORDER`) | `engine/lua/determinism.lua` | MIT, jackdaw contributors |

## Fetched or installed separately (not committed; pinned)

| Component | Version | Used for | License |
|---|---|---|---|
| [coder/balatrobot](https://github.com/coder/balatrobot) | v1.5.2 (`scripts/setup.py balatrobot`) | Action API endpoints and gamestate view, run inside the engine; real-game mod (with the fixes above) | MIT, © 2025 Coder |
| [jackdaw-balatro](https://github.com/TylerFlar/jackdaw-balatro) | v0.2.6 release wheels (`uv sync --extra sim`, hashes in `uv.lock`) | Fast simulator backend; its policies drive the comparison scripts | MIT |
| [BalatroLLM](https://github.com/coder/balatrollm) | v1.1.1 (git tag; optional `llm` extra) | LLM harness, extended by `balatro_bot/agent/llm_runner.py` | MIT, © Coder |
| [LuaJIT](https://github.com/LuaJIT/LuaJIT) | 2.1.1700008891, commit 43d0a19 (`scripts/setup.py luajit`, Windows/Linux only) | Engine runtime | MIT, © Mike Pall |

## Used from your machine (never distributed)

| Component | Used for | License |
|---|---|---|
| The LuaJIT bundled with your Balatro install (macOS `Lua.framework`) | Engine runtime via `engine/host/bhost.c` | MIT |
| [lovely-injector](https://github.com/ethangreen-dev/lovely-injector) and [Steamodded](https://github.com/Steamodded/smods) from your Balatro `Mods` folder | Real-game harness only (balatrobot requires them) | lovely: MIT; Steamodded: GPL-3.0 (linked from your install, never copied) |
