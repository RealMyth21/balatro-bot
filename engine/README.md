# Exact headless engine

Runs the real game's Lua code (from your own copy of Balatro 1.0.1o) with no window, graphics, audio
or saves, and drives it with [balatrobot](https://github.com/coder/balatrobot)'s action API. Replies
use the same JSON game state as the real-game harness and jackdaw, so agents run unchanged on any of
them. It is a **reference** for checking faster simulators, not a training environment (~160 ms per
action).

## Setup

```bash
uv run python scripts/setup.py balatrobot   # pinned balatrobot (MIT) into vendor/
uv run python scripts/setup.py host         # macOS: builds engine/host/bhost-game against the game's LuaJIT
# Windows/Linux (untested): extract-src (game source from Balatro.exe) + luajit (builds the game's LuaJIT)
uv run pytest tests/test_engine_smoke.py     # checks the engine boots and reproduces a known seed
```

No save files are needed: the game's own Unlock All is applied in memory (`ENGINE_UNLOCK_ALL=1`,
shown identical to a real Unlock All profile). To use one of your own profiles instead:
`scripts/setup.py profile --slot N`, then `ENGINE_UNLOCK_ALL=0 ENGINE_SAVE=runtime/engine_save`.

## How it works

- `lua/love_stub.lua`: a stand-in for the LÖVE framework. Nothing is drawn or played, and no files
  are written.
- `lua/boot.lua`: loads the game. It advances the game's timers exactly like balatrobot's real-game
  fast headless mode (4.99/60 s per frame, GAMESPEED 10). It applies render-only speed-ups: positions
  snap, the per-frame GC busy loop is removed, and the reply's game state is built once. It then
  loads balatrobot's endpoints and serves JSON lines on stdin/stdout. Before replying, it lets the
  game settle: no new events pending, and nothing changed for 3 frames.
- `lua/determinism.lua`: Balatro's macOS LuaJIT seeds its string hash per launch, so `pairs()` order
  varies between launches. Two rules, To Do List's target hand and the Orbital Tag's hand, depend on
  it (so does the most-played-hand tie-break). The shim pins `pairs(G.GAME.hands)` to LuaJIT 2.0.5's
  order, which is the one jackdaw uses. The real-game harness loads the same shim.
- `balatrobot_fixes.json`: two balatrobot bugs where a request never answers. Each is fixed in
  memory here and in the real-game mod copy:
  - a booster opened by a skip tag (it closes back to blind select, not the shop);
  - a Celestial pack whose first card is Black Hole (balatrobot waited for a hand that is never dealt).
- `host/bhost.c`: a 20-line host linked against the LuaJIT that ships with the game.

## Things that matter for fidelity

- **LuaJIT version.** Seeded picks use LuaJIT's `math.random`, which changed after the game's
  version. On Homebrew's LuaJIT 2.1.1794386141, 10 of 20 seeds diverged (deck shuffles, shop rolls).
  The engine refuses to start on a LuaJIT other than 2.1.1700008891 unless `ENGINE_ALLOW_ANY_LUAJIT=1`.
- **Timing is part of the rules.** With delays removed (`ENGINE_INSTANT=1`, experimental), booster
  packs break. A booster's cards are placed about 1.3 s after opening, after the state machine has
  rebuilt the pack area.
- **Settling.** Some events wait on per-frame progress, such as a UI panel sliding into place. Too
  short a settle window left The Manacle's hand size unrestored.

## Validation (Black Deck / Gold Stake)

| Comparison | Result |
|---|---|
| vs the real game (balatrobot), scripted + random policies | 25/25 seeds identical (earlier timing; few packs) |
| vs jackdaw v0.2.6, jackdaw's smart policy | 100/100 seeds identical through game over, up to ante 5 |
| vs jackdaw v0.2.6, random policy | 100/100 identical (short games) |
| vs jackdaw, assisted to ante 8 (money top-ups, score set just short of the blind on the last hand), RNG streams + hidden joker state checked every step | in progress |

Fields that legitimately differ are not compared (see `balatro_bot/validation/diff.py`):
- **Card ids:** per-process counters.
- **Booster art variant:** picked with unseeded `math.random`.
- **The deck's size limit:** Steamodded bookkeeping, used only by two unlocks.
- **Text, in jackdaw comparisons only:** jackdaw uses names instead of descriptions.

## Not for redistribution

The engine loads game code from your own copy at runtime. Never commit or publish the game's files,
including lovely's dumps (`runtime/mods/lovely/dump`).
