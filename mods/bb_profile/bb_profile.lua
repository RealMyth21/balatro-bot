-- Bot-run helper mod for the REAL game (loaded only from runtime/mods, never the user's Mods folder).
-- 1. Forces bot games onto a dedicated save profile (env BOT_PROFILE, default 2). The launcher
--    restores the user's settings.jkr afterwards, so their default profile is unchanged.
-- 2. Loads the same determinism shim the headless engine uses (see engine/lua/determinism.lua),
--    so a seed + action sequence replays identically in both.
local target = tonumber(os.getenv("BOT_PROFILE") or "2")
if G and G.SETTINGS and G.PROFILES and G.SETTINGS.profile ~= target then
  G.PROFILES[target] = G.PROFILES[target] or {}
  G:load_profile(target)
  sendInfoMessage("Switched to bot profile " .. target, "BB.PROFILE")
else
  sendInfoMessage("Already on bot profile " .. tostring(target), "BB.PROFILE")
end

assert(SMODS.load_file("determinism.lua"))()
sendInfoMessage("Determinism shim installed", "BB.PROFILE")
