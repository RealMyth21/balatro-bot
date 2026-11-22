# Exact headless engine

Runs the real game's Lua code (from your own copy of Balatro 1.0.1o) with no window, graphics, audio
or saves, and drives it with [balatrobot](https://github.com/coder/balatrobot)'s action API. Replies
use the same JSON game state as the real-game harness and jackdaw, so agents run unchanged on any of
them. It is a **reference** for checking faster simulators, not a training environment (~120–160 ms
per action).

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
- `balatrobot_fixes.json`: four balatrobot bugs where a request never answers. Each is fixed in
  memory here and in the real-game mod copy:
  - a booster opened by a skip tag (it closes back to blind select, not the shop);
  - buying, and taking a card from, a Celestial pack whose first card is Black Hole (balatrobot
    judged the pack by its first card and waited for a hand that is never dealt);
  - selling Invisible Joker (it duplicates a joker, so the joker count never drops).
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

## Errors

- An endpoint that rejects a request (bad indices, wrong state) answers with balatrobot's error and
  the game is unchanged, as in the real game.
- An error inside a game event, or an action that never finishes, leaves the game state undefined.
  The engine answers that request with `INTERNAL_ERROR` and exits. `EngineClient` starts a fresh
  engine on the next call; a call that needs the lost game gets `INTERNAL_ERROR` ("the game in progress
  was lost"), never a silently reset game, while `health`, `menu` and `start` work as usual. The LLM
  runner counts such games as infrastructure errors and replays them later.
- A reply that can't be encoded (e.g. NaN in a game value) becomes an `INTERNAL_ERROR` instead of
  ending the process.
- A request that never comes back is stopped by `EngineClient` after `ENGINE_CALL_TIMEOUT` seconds
  (default 120): the engine's own limit counts frames, so a loop inside one frame would otherwise wait
  forever. The stuck engine's stack is sampled to `runtime/logs/engine_hangs/` (macOS `sample`; it
  shows the native frames, so it tells game code from the host), the engine is stopped, and the request
  fails with `INTERNAL_ERROR` as above. This happened once, under heavy load (five engines at once),
  and didn't come back in eight runs under the same load.

## Debug hooks

`ENGINE_HOOKS=<file.lua>` runs extra Lua after boot, for instrumentation and tests only.
`engine/hooks/engine_add.lua` adds an `engine_add` endpoint that puts a joker, consumable or (in the
shop) voucher into the run with the game's own `create_card`; balatrobot's `add` needs Steamodded, which
the engine doesn't run. `tests/test_jackdaw_fixes.py` uses it to check jackdaw's card text on items that
rarely come up in play.

## Validation

Black Deck / Gold Stake unless noted. "Deep" means every RNG stream and every joker, consumable and
hand card's hidden ability table were compared after every action, besides the full game state.

| Comparison | Result |
|---|---|
| vs the real game (balatrobot), scripted + random policies | 25/25 seeds identical (earlier timing; few packs) |
| vs the real game, pack-heavy mixed policy (twice: before and after the latest engine changes) | 12/12 and 8/8 identical, incl. the state after the last action |
| vs jackdaw v0.2.6, random policy, deep | 200/200 identical |
| vs jackdaw v0.2.6, jackdaw's smart policy, deep | 200/200 identical (to ante 5) |
| vs jackdaw, assisted to ante 8 (money top-ups, score set just short of the blind on the last hand), deep | 78/80 identical, 62 runs won; 2 differences, both jackdaw's (below) |
| vs jackdaw, random policy on Red/White, Plasma/Gold, Abandoned/Blue, Erratic/Gold | 160/160 identical |
| vs jackdaw, collector policy (buys, opens, uses, sells, skips), assisted to ante 8, deep | 100 seeds, 42,724 steps: 97 identical; 2 balatrobot hangs, identical after the fix; 1 jackdaw difference |

Differences are replayed on the real game (`scripts/replay_divergence.py SEED --real fast|normal`); the
engine matched it every time (all but `JDC04019`, the Merry Andy case, which is still to be replayed). Differences in jackdaw v0.2.6, all when
Ceremonial Dagger destroys a joker at blind select:
- a joker that changes hand size (Turtle Bean, Troubadour, Merry Andy): jackdaw deals the first hand
  with the old hand size;
- with Riff-raff: the game frees the destroyed joker before Riff-raff creates jokers, so the same
  joker can come back; jackdaw counts it as still owned and draws another.

Content the runs exercised is reported by `scripts/compare_jackdaw.py` (`--coverage-out` accumulates
it); `--policy collector` buys, opens and uses everything it can to reach rare content.

Fields that legitimately differ are not compared (see `balatro_bot/validation/diff.py`):
- **Card ids:** per-process counters.
- **Booster art variant:** picked with unseeded `math.random`.
- **The deck's size limit:** Steamodded bookkeeping, used only by two unlocks.
- **Text, in jackdaw comparisons only:** jackdaw uses names instead of descriptions.

## Not for redistribution

The engine loads game code from your own copy at runtime. Never commit or publish the game's files,
including lovely's dumps (`runtime/mods/lovely/dump`).
