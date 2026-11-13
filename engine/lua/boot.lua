-- Headless Balatro engine.
--
-- Runs the real game's Lua code (from the user's own copy) under LuaJIT with LÖVE stubbed out,
-- animation/timer delays removed (event order preserved), and balatrobot's endpoints for actions.
-- Speaks JSON lines on stdin/stdout:  {"id":1,"method":"start","params":{...}}  ->  {"id":1,"result":{...}}
--
-- Env:
--   BALATRO_SRC   path to the game's Lua source (Balatro.love directory)          [required]
--   ENGINE_SAVE   read-only sandbox save dir (settings + bot profile)            [required]
--   BB_DIR        path to balatrobot's mod folder (contains src/lua)            [required]
--   ENGINE_LUA    path to this folder                                           [required]
--   BOT_PROFILE   profile slot to load from the sandbox (default 2)
--   ENGINE_DEBUG  1 = log game prints/debug messages to stderr

local SRC = assert(os.getenv("BALATRO_SRC"), "BALATRO_SRC not set")
local SAVE = assert(os.getenv("ENGINE_SAVE"), "ENGINE_SAVE not set")
local BB_DIR = assert(os.getenv("BB_DIR"), "BB_DIR not set")
local HERE = assert(os.getenv("ENGINE_LUA"), "ENGINE_LUA not set")
local PROFILE = tonumber(os.getenv("BOT_PROFILE") or "2")
local DEBUG = os.getenv("ENGINE_DEBUG") == "1"

package.path = SRC .. "/?.lua;" .. HERE .. "/?.lua;" .. HERE .. "/vendor/?.lua;" .. package.path

-- The random numbers (math.random behind every seeded pick) must come from the same LuaJIT the game
-- ships. Newer LuaJIT releases changed them: e.g. 2.1.1794386141 shuffles decks differently.
local KNOWN_GOOD_LUAJIT = { ["LuaJIT 2.1.1700008891"] = true }
if not KNOWN_GOOD_LUAJIT[jit.version] and os.getenv("ENGINE_ALLOW_ANY_LUAJIT") ~= "1" then
  io.stderr:write("engine: ", jit.version, " is not the game's LuaJIT (2.1.1700008891); seeded results ",
    "would differ from the real game. Use the game's LuaJIT (scripts/setup.py host / luajit), or set ",
    "ENGINE_ALLOW_ANY_LUAJIT=1 to run anyway.\n")
  os.exit(3)
end

-- ENGINE_PROFILE=<jit.p mode, e.g. "Fl"> writes a LuaJIT profile to ENGINE_PROFILE_OUT on quit
local PROFILER = os.getenv("ENGINE_PROFILE")
if PROFILER then require("jit.p").start(PROFILER, os.getenv("ENGINE_PROFILE_OUT") or "engine_profile.txt") end

local stdout = io.stdout
local function log(...)
  if DEBUG then
    local parts = {}
    for i = 1, select("#", ...) do parts[#parts + 1] = tostring(select(i, ...)) end
    io.stderr:write(table.concat(parts, "\t"), "\n")
  end
end
print = log -- the game prints freely; stdout is reserved for the protocol

require("love_stub").install({ src = SRC, save = SAVE })
local json = require("json")
package.loaded.socket = { gettime = os.clock }

-- ---------------------------------------------------------------------------------------------
-- Load the game exactly as main.lua does (main.lua also defines love.* callbacks we never call)
-- ---------------------------------------------------------------------------------------------
dofile(SRC .. "/main.lua")
dofile(HERE .. "/determinism.lua")

-- ---------------------------------------------------------------------------------------------
-- Timing. Default: the game's own event timers, advanced exactly like balatrobot's real-game
-- fast headless mode (fixed 4.99/60 s per frame, GAMESPEED 10). Some rules depend on timing
-- (e.g. a pack's cards are placed ~1.3 s after opening, by which point the state machine has
-- rebuilt the pack area), so removing delays is NOT faithful.
-- ENGINE_INSTANT=1 removes all delays (experimental; known to break booster packs).
-- ---------------------------------------------------------------------------------------------
local INSTANT = os.getenv("ENGINE_INSTANT") == "1"
if INSTANT then function Event:handle(_results)
  _results.blocking, _results.completed = self.blocking, self.complete
  if self.created_on_pause == false and G.SETTINGS.paused then _results.pause_skip = true; return end
  self.start_timer = true
  local trig = self.trigger
  if trig == "ease" then
    if not self.complete then
      self.ease.ref_table[self.ease.ref_value] = self.func(self.ease.end_val)
      self.complete = true
    end
    _results.completed, _results.time_done = true, true
    return
  elseif trig == "after" or trig == "immediate" then
    _results.time_done = true
    _results.completed = self.func()
  elseif trig == "condition" or trig == "before" then
    if not self.complete then _results.completed = self.func() end
    _results.time_done = true
  end
  if _results.completed then self.complete = true end
end end

-- ---------------------------------------------------------------------------------------------
-- Speed patches (render/bookkeeping only; game rules untouched). ENGINE_FAST=0 disables them,
-- which is how they are validated: fast and slow engines must produce identical states.
-- ---------------------------------------------------------------------------------------------
local FAST = os.getenv("ENGINE_FAST") ~= "0"
if FAST then
  -- nuGC busy-loops for 0.3 ms per frame doing incremental GC and stops the automatic collector.
  -- Use the normal incremental collector instead.
  nuGC = function() end
  collectgarbage("restart")

  -- Positions only matter for drawing. Majors snap straight to their targets (no easing, juice,
  -- or parallax); minors still follow their major so any position checks resolve immediately.
  function Moveable:move(dt)
    if self.FRAME.MOVE >= G.FRAMES.MOVE then return end
    self.FRAME.OLD_MAJOR = self.FRAME.MAJOR
    self.FRAME.MAJOR = nil
    self.FRAME.MOVE = G.FRAMES.MOVE
    if not self.created_on_pause and G.SETTINGS.paused then return end
    self:align_to_major()
    local role = self.role
    if role.role_type == "Glued" then
      if role.major then self:glue_to_major(role.major) end
    elseif role.role_type == "Minor" and role.major then
      if role.major.FRAME.MOVE < G.FRAMES.MOVE then role.major:move(dt) end
      self.STATIONARY = role.major.STATIONARY
      if (not self.STATIONARY) or self.NEW_ALIGNMENT or self.config.refresh_movement or self.juice
        or role.xy_bond == "Weak" or role.r_bond == "Weak" then
        self:move_with_major(dt)
      end
    elseif role.role_type == "Major" then
      self.STATIONARY = true
      local T, VT = self.T, self.VT
      VT.x, VT.y, VT.w, VT.h, VT.r, VT.scale = T.x, T.y, T.w, T.h, T.r, T.scale
      self.velocity.x, self.velocity.y, self.velocity.r, self.velocity.scale = 0, 0, 0, 0
      self.juice = nil
    end
    self.NEW_ALIGNMENT = false
  end
end

-- ---------------------------------------------------------------------------------------------
-- Boot
-- ---------------------------------------------------------------------------------------------
G.SETTINGS.profile = PROFILE
G.SETTINGS.tutorial_complete = true
G.SETTINGS.tutorial_progress = nil
G.SETTINGS.skip_splash = "Yes"
G.SETTINGS.language = "en-us"
G.SETTINGS.reduced_motion = true
G.SETTINGS.screenshake = false
G.F_SOUND_THREAD = false
G.F_HTTP_SCORES = false
G.F_SAVE_TIMER = 1e12
G:start_up()
G.SETTINGS.profile = PROFILE
G.SETTINGS.tutorial_complete = true

-- ENGINE_UNLOCK_ALL=1: the game's own "Unlock All" (button_callbacks.lua, G.FUNCS unlock-all
-- handler) applied in memory, so no save profile is needed. Item pools (shop, tags, packs) depend
-- on unlocked/discovered flags; this matches a profile that used Unlock All.
if os.getenv("ENGINE_UNLOCK_ALL") == "1" then
  G.PROFILES[G.SETTINGS.profile] = G.PROFILES[G.SETTINGS.profile] or {}
  G.PROFILES[G.SETTINGS.profile].all_unlocked = true
  for _, tbl in ipairs({ G.P_CENTERS, G.P_BLINDS, G.P_TAGS }) do
    for _, v in pairs(tbl) do
      if not v.demo and not v.wip then
        v.alerted, v.discovered, v.unlocked = true, true, true
      end
    end
  end
  G.P_LOCKED = {}
end

-- The game writes flags into G.FILE_HANDLER (e.g. force=true at game over); keep a table there
-- but never let a save get queued.
local function no_save()
  G.FILE_HANDLER = G.FILE_HANDLER or {}
  G.FILE_HANDLER.update_queued = false
end
no_save()

-- balatrobot headless: love.update(4.99/60) every frame; fast mode: GAMESPEED 10
local DT = 4.99 / 60
G.SETTINGS.GAMESPEED = 10

-- cheap "did the event queues change" fingerprint
local function queue_fingerprint()
  local parts = 0
  local h = 0
  for _, q in pairs(G.E_MANAGER.queues) do
    local n = #q
    parts = parts + n
    if n > 0 then h = h + n * 31 + (q[1].complete and 7 or 0) end
  end
  return parts * 1000003 + h, parts
end

-- Experimental: extra event-queue passes per frame (ENGINE_EVENT_PASSES>0). Changes how events
-- interleave with the per-frame state machine, so it is off by default.
local EVENT_PASSES = tonumber(os.getenv("ENGINE_EVENT_PASSES") or "0")
local function tick()
  if BB_GAMESTATE then BB_GAMESTATE.check_game_over() end
  if EVENT_PASSES > 0 and not G.SETTINGS.paused then
    -- Run chained events through several queue passes per frame (like a very low frame rate),
    -- instead of one link of the chain per full frame update.
    for _ = 1, EVENT_PASSES do
      local before, n = queue_fingerprint()
      if n == 0 then break end
      G.E_MANAGER:update(0, true)
      if queue_fingerprint() == before then break end
    end
  end
  G:update(DT)
  no_save()
end

for _ = 1, 2000 do
  tick()
  if G.STATE == G.STATES.MENU and G.MAIN_MENU_UI then break end
end
assert(G.STATE == G.STATES.MENU, "engine failed to reach the main menu")

-- ---------------------------------------------------------------------------------------------
-- balatrobot endpoints (MIT, vendored unmodified) with a small source transform:
-- Steamodded merges the five vanilla booster-pack states into SMODS_BOOSTER_OPENED.
-- ---------------------------------------------------------------------------------------------
local PACK_STATES = {
  [G.STATES.TAROT_PACK] = true, [G.STATES.PLANET_PACK] = true, [G.STATES.SPECTRAL_PACK] = true,
  [G.STATES.STANDARD_PACK] = true, [G.STATES.BUFFOON_PACK] = true,
}
function BB_IS_PACK_STATE() return PACK_STATES[G.STATE] == true end
local PACK_LIST = "G.STATES.TAROT_PACK, G.STATES.PLANET_PACK, G.STATES.SPECTRAL_PACK, G.STATES.STANDARD_PACK, G.STATES.BUFFOON_PACK"

-- balatrobot bug fixes shared with the real-game mod copy (engine/balatrobot_fixes.json)
local BB_FIXES = {}
do
  local f = assert(io.open(HERE .. "/../balatrobot_fixes.json", "rb"))
  BB_FIXES = json.decode(f:read("*a"))
  f:close()
end

local function replace_plain(s, old, new)
  local out, pos, n = {}, 1, 0
  while true do
    local i, j = s:find(old, pos, true)
    if not i then break end
    out[#out + 1] = s:sub(pos, i - 1)
    out[#out + 1] = new
    pos, n = j + 1, n + 1
  end
  out[#out + 1] = s:sub(pos)
  return table.concat(out), n
end

local function apply_fixes(rel, src)
  for _, fix in ipairs(BB_FIXES) do
    if fix.file == rel then
      local n
      src, n = replace_plain(src, fix.old, fix.new)
      assert(n == fix.count, "balatrobot fix no longer applies to " .. rel .. " (upstream changed?)")
    end
  end
  return src
end

local function transform(src)
  src = src:gsub("G%.STATE%s*==%s*G%.STATES%.SMODS_BOOSTER_OPENED", "BB_IS_PACK_STATE()")
  src = src:gsub("G%.STATE%s*~=%s*G%.STATES%.SMODS_BOOSTER_OPENED", "(not BB_IS_PACK_STATE())")
  src = src:gsub("G%.STATES%.SMODS_BOOSTER_OPENED", PACK_LIST)
  return src
end

local function noop() end
sendDebugMessage = DEBUG and function(m, l) log("[" .. tostring(l) .. "] " .. tostring(m)) end or noop
sendInfoMessage, sendWarnMessage, sendErrorMessage, sendTraceMessage =
  sendDebugMessage, sendDebugMessage, sendDebugMessage, sendDebugMessage

SMODS = {
  current_mod = { path = BB_DIR .. "/", version = "engine" },
  load_file = function(rel)
    local f = assert(io.open(BB_DIR .. "/" .. rel, "rb"))
    local s = f:read("*a")
    f:close()
    return loadstring(transform(apply_fixes(rel, s)), "@" .. rel)
  end,
}

BB_GAMESTATE = assert(SMODS.load_file("src/lua/utils/gamestate.lua"))()
assert(SMODS.load_file("src/lua/utils/errors.lua"))()
assert(SMODS.load_file("src/lua/core/dispatcher.lua"))()

-- report vanilla pack states under the name the real-game harness uses
do
  local orig = BB_GAMESTATE.get_gamestate
  BB_GAMESTATE.get_gamestate = function()
    local s = orig()
    if BB_IS_PACK_STATE() then s.state = "SMODS_BOOSTER_OPENED" end
    return s
  end
end

local ENDPOINTS = {
  "health", "gamestate", "menu", "start", "skip", "select", "play", "discard", "cash_out",
  "next_round", "reroll", "buy", "pack", "rearrange", "sell", "use", "set",
}
local files = {}
for i, name in ipairs(ENDPOINTS) do files[i] = "src/lua/endpoints/" .. name .. ".lua" end

local captured
local FakeServer = { send_response = function(r) captured = r end }
assert(BB_DISPATCHER.init(FakeServer, files), "failed to load balatrobot endpoints")

local ERROR_CODES = {
  INTERNAL_ERROR = -32000, BAD_REQUEST = -32001, INVALID_STATE = -32002, NOT_ALLOWED = -32003,
}

local MAX_TICKS = 5000

-- Events still queued at the end of the previous settle are background events (some UI events
-- wait forever); they are ignored when deciding whether an action has finished.
local background = setmetatable({}, { __mode = "k" })

-- Signature of what is still queued (excluding background events): nil when idle.
local function queue_signature()
  local n, ids = 0, {}
  for _, q in pairs(G.E_MANAGER.queues) do
    for _, ev in ipairs(q) do
      if not background[ev] then
        n = n + 1
        if n <= 24 then ids[n] = tostring(ev) end
      end
    end
  end
  if n == 0 then return nil end
  return n, table.concat(ids, ",")
end

-- After an action's completion condition fires, keep ticking until no new events are pending,
-- or the pending set has not changed for SETTLE_STABLE frames (~5 s of game time; a paused
-- game-over screen never drains). The bot then sees the fully resolved state, as a human would
-- after the animations finish.
local SETTLE_STABLE = tonumber(os.getenv("ENGINE_SETTLE_STABLE") or "6")
local SETTLE_IDLE = tonumber(os.getenv("ENGINE_SETTLE_IDLE") or "3")

-- Is any non-background event still counting down a delay (as opposed to waiting on a condition)?
local function timers_running()
  for _, q in pairs(G.E_MANAGER.queues) do
    for _, ev in ipairs(q) do
      if not background[ev] and not ev.complete then
        local trig = ev.trigger
        if trig == "ease" then return true end
        if (trig == "after" or trig == "before") then
          local now = G.TIMERS[ev.timer] or 0
          if not ev.start_timer or (ev.time + ev.delay > now) then return true end
        end
      end
    end
  end
  return false
end

local function settle()
  local ticks, last_sig, stable = 0, nil, 0
  while ticks < 600 do
    local n, sig = queue_signature()
    if not n then break end
    if sig == last_sig then stable = stable + 1 else stable = 0 end
    -- nothing is waiting on time and the queue has not changed for a few frames. (Conditions can
    -- still depend on per-frame progress, e.g. a UI panel sliding into place, which takes 1-2
    -- frames with snapped positions; a single unchanged frame is not enough.)
    if stable >= SETTLE_IDLE and not timers_running() then break end
    if stable >= SETTLE_STABLE then break end
    last_sig = sig
    tick()
    ticks = ticks + 1
  end
  for _, q in pairs(G.E_MANAGER.queues) do
    for _, ev in ipairs(q) do background[ev] = true end
  end
  return ticks
end

local function is_error(r)
  return r.message ~= nil and r.name ~= nil and r.state == nil
end

-- Endpoints build a full game state when they finish; the engine builds it once, after settling.
local real_get_gamestate = BB_GAMESTATE.get_gamestate
local PENDING = { state = "PENDING" }

local in_execute = false

local function handle(req)
  captured = nil
  -- Endpoints may read the gamestate while validating (buy/use look up the item); that must be
  -- the real one. Only the reply built by their completion callback is deferred.
  local defer = FAST and req.method ~= "gamestate" and req.method ~= "health"
  if defer then
    BB_GAMESTATE.get_gamestate = function()
      if in_execute then return real_get_gamestate() end
      return PENDING
    end
  end
  in_execute = true
  local ok, err = pcall(BB_DISPATCHER.dispatch, req)
  in_execute = false
  local ticks = 0
  if ok then
    while captured == nil do
      tick()
      ticks = ticks + 1
      if ticks >= MAX_TICKS then
        captured = { message = "engine: action did not complete after " .. MAX_TICKS .. " ticks (state "
          .. tostring(G.STATE) .. ")", name = "INTERNAL_ERROR" }
      end
    end
  end
  BB_GAMESTATE.get_gamestate = real_get_gamestate
  if not ok then error(err) end
  if not is_error(captured) and captured.state ~= nil then
    ticks = ticks + settle()
    captured = real_get_gamestate()
  end
  return captured, ticks
end

-- engine_debug_state: hidden state for differential testing (RNG streams, raw card abilities).
local function plain(v, depth)
  local t = type(v)
  if t == "number" or t == "string" or t == "boolean" then return v end
  if t ~= "table" or depth > 3 or getmetatable(v) ~= nil then return nil end
  local out, n = {}, 0
  for k, x in pairs(v) do
    local px = plain(x, depth + 1)
    if px ~= nil and (type(k) == "string" or type(k) == "number") then out[tostring(k)] = px; n = n + 1 end
  end
  return n > 0 and out or nil
end

local function area_abilities(area)
  local out = {}
  for i, c in ipairs((area and area.cards) or {}) do
    out[i] = {
      key = c.config and (c.config.center_key or c.config.card_key) or "?",
      ability = plain(c.ability, 1) or {},
      sell_cost = c.sell_cost, cost = c.cost,
      edition = c.edition and c.edition.type or nil, seal = c.seal,
    }
  end
  return out
end

assert(BB_DISPATCHER.register({
  name = "engine_debug_state", description = "hidden state for differential testing", schema = {},
  execute = function(_, send)
    local rng = {}
    for k, v in pairs((G.GAME and G.GAME.pseudorandom) or {}) do
      if type(v) == "number" or type(v) == "string" then rng[k] = v end
    end
    send({
      debug = true,
      rng = rng,
      jokers = area_abilities(G.jokers),
      consumables = area_abilities(G.consumeables),
      hand = area_abilities(G.hand),
      dollars = G.GAME and G.GAME.dollars,
      interest_cap = G.GAME and G.GAME.interest_cap,
      round_resets = G.GAME and plain(G.GAME.round_resets, 1),
    })
  end,
}))

-- ENGINE_HOOKS=<file.lua>: extra Lua run after boot (debug instrumentation)
if os.getenv("ENGINE_HOOKS") then dofile(os.getenv("ENGINE_HOOKS")) end

-- ---------------------------------------------------------------------------------------------
-- JSON-lines loop
-- ---------------------------------------------------------------------------------------------
stdout:write(json.encode({ ready = true, version = G.VERSION }), "\n")
stdout:flush()

for line in io.stdin:lines() do
  if line ~= "" then
    local ok, req = pcall(json.decode, line)
    local out
    if not ok or type(req) ~= "table" then
      out = { id = json.null, error = { code = -32700, message = "parse error", data = { name = "BAD_REQUEST" } } }
    elseif req.method == "engine.quit" then
      break
    else
      local good, resp, ticks = pcall(handle, req)
      if not good then
        out = { id = req.id, error = { code = -32000, message = tostring(resp), data = { name = "INTERNAL_ERROR" } } }
      elseif is_error(resp) then
        out = { id = req.id, error = { code = ERROR_CODES[resp.name] or -32000, message = resp.message,
                                       data = { name = resp.name } } }
      else
        out = { id = req.id, result = resp, ticks = ticks }
      end
    end
    stdout:write(json.encode(out), "\n")
    stdout:flush()
  end
end

if PROFILER then require("jit.p").stop() end
