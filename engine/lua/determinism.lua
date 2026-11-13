-- Determinism shim, loaded by BOTH the headless engine and the real game during bot runs.
--
-- Balatro's LuaJIT (2.1.1700008891) seeds its string hash from the OS at startup, so
-- `pairs()` over string-keyed tables visits keys in a different order on every launch.
-- Two game rules build a list that way before picking from it with the seeded RNG:
--   * To Do List's target hand        (card.lua, Card:set_ability / end-of-round reroll)
--   * Orbital Tag's hand              (UI_definitions.lua, create_UIBox_blind_tag)
-- (plus the most-played-hand tie-break.) All iterate G.GAME.hands. On the macOS build those picks
-- are effectively random per launch even for a fixed seed. This shim makes pairs(G.GAME.hands)
-- follow the order LuaJIT 2.0.5 visits them in (no hash seed), as used by builds with LuaJIT 2.0.5
-- and by jackdaw (crates/jackdaw-core/src/hand_eval.rs PAIRS_ORDER, which reports matching the
-- live game's To Do List on every seed tried). A seed + action sequence then replays identically,
-- and matches jackdaw. Every other seeded pick is from an array or sorted first: unaffected.

if BB_DETERMINISM_INSTALLED then return end
BB_DETERMINISM_INSTALLED = true

local raw_pairs = pairs

-- pairs(G.GAME.hands) order under LuaJIT 2.0.5 (unseeded string hash)
local PAIRS_ORDER = {
  "Flush House", "Full House", "Flush", "Pair", "High Card", "Straight Flush",
  "Straight", "Two Pair", "Flush Five", "Five of a Kind", "Three of a Kind", "Four of a Kind",
}

local function ordered_hands(t)
  local order = {}
  local seen = {}
  for _, k in ipairs(PAIRS_ORDER) do
    if t[k] ~= nil then order[#order + 1] = k; seen[k] = true end
  end
  local extra = {}
  for k in raw_pairs(t) do
    if not seen[k] then extra[#extra + 1] = k end
  end
  table.sort(extra, function(a, b) return tostring(a) < tostring(b) end)
  for _, k in ipairs(extra) do order[#order + 1] = k end
  local i = 0
  return function()
    i = i + 1
    local k = order[i]
    if k ~= nil then return k, t[k] end
  end, t, nil
end

function pairs(t)
  local game = G and G.GAME
  if game and t == game.hands then
    return ordered_hands(t)
  end
  return raw_pairs(t)
end
