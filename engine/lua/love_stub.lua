-- Minimal stand-in for the LÖVE framework so Balatro's game logic runs under plain LuaJIT.
-- Nothing is drawn, played or saved; files are read from the game source and a read-only
-- sandbox save folder.

local M = {}

local function noop() end

-- An object on which every method exists and returns 1 (fonts, images, canvases, shaders...).
-- One shared function per return value: the game calls these every frame, and creating a new
-- closure per lookup showed up as ~4% of engine time.
local DUMMY
local function ret_one() return 1, 1 end
DUMMY = setmetatable({}, {
  __index = function() return ret_one end,
  __call = function() return DUMMY end,
})

local function fallback(tbl, ret)
  local function f() return ret end
  return setmetatable(tbl, { __index = function() return f end })
end

local function path_kind(p)
  local f = io.open(p, "rb")
  if not f then return nil end
  local ok, _, code = f:read(0)
  f:close()
  if ok == nil and code == 21 then return "directory" end -- EISDIR
  if ok == nil then
    -- read(0) returns nil at EOF for empty files; treat as file
    return "file"
  end
  return "file"
end

function M.install(opts)
  local SRC, SAVE = opts.src, opts.save
  local W, H = 1920, 1080
  local love = {}

  love.graphics = fallback({
    getWidth = function() return W end,
    getHeight = function() return H end,
    getDimensions = function() return W, H end,
    getPixelWidth = function() return W end,
    getPixelHeight = function() return H end,
    getDPIScale = function() return 1 end,
    isActive = function() return false end,
    isCreated = function() return true end,
    getStats = function() return {} end,
  }, DUMMY)

  love.window = fallback({
    getMode = function() return W, H, { fullscreen = false, vsync = 0, display = 1, highdpi = false } end,
    getDesktopDimensions = function() return W, H end,
    getFullscreenModes = function() return { { width = W, height = H } } end,
    getDisplayCount = function() return 1 end,
    getDisplayName = function() return "headless" end,
    toPixels = function(x) return x end,
    fromPixels = function(x) return x end,
    getTitle = function() return "Balatro" end,
    isOpen = function() return true end,
    setMode = function() return true end,
    updateMode = function() return true end,
    getDPIScale = function() return 1 end,
  }, true)

  local t0 = os.clock()
  love.timer = {
    getTime = function() return os.clock() - t0 end,
    step = function() return 1 / 60 end,
    getDelta = function() return 1 / 60 end,
    getFPS = function() return 60 end,
    sleep = noop,
  }

  love.system = fallback({
    getOS = function() return "Linux" end, -- keeps JIT on and skips Steam
    getProcessorCount = function() return 1 end,
    getClipboardText = function() return "" end,
    getPowerInfo = function() return "unknown" end,
  }, nil)

  local channels = {}
  local function channel()
    return { push = noop, supply = noop, clear = noop, pop = noop, demand = noop, peek = noop,
             getCount = function() return 0 end, performAtomic = noop }
  end
  love.thread = {
    newThread = function() return { start = noop, wait = noop, isRunning = function() return false end,
                                     getError = noop } end,
    getChannel = function(name) channels[name] = channels[name] or channel(); return channels[name] end,
    newChannel = channel,
  }

  love.audio = fallback({ newSource = function() return DUMMY end }, nil)
  love.mouse = fallback({
    getPosition = function() return 0, 0 end,
    getX = function() return 0 end,
    getY = function() return 0 end,
    isVisible = function() return false end,
    isDown = function() return false end,
  }, nil)
  love.keyboard = fallback({ isDown = function() return false end }, nil)
  love.joystick = fallback({ getJoysticks = function() return {} end, getJoystickCount = function() return 0 end }, nil)
  love.touch = { getTouches = function() return {} end }
  love.event = { pump = noop, quit = noop, push = noop, poll = function() return function() return nil end end }
  love.data = {
    compress = function(_, _, s) return s end,
    decompress = function() error("compressed save files are not supported by the engine sandbox") end,
  }
  love.arg = { parseGameArguments = function() return {} end }
  love.math = fallback({}, 0)
  love.image = fallback({}, DUMMY)
  love.sound = fallback({}, DUMMY)
  love.video = fallback({}, DUMMY)
  love.font = fallback({}, DUMMY)

  local function resolve(p)
    local sp = SAVE .. "/" .. p
    if path_kind(sp) then return sp end
    local gp = SRC .. "/" .. p
    if path_kind(gp) then return gp end
    return nil
  end

  love.filesystem = {
    getInfo = function(p)
      local r = resolve(p)
      if not r then return nil end
      return { type = path_kind(r), size = 0, modtime = 0 }
    end,
    read = function(p)
      local r = resolve(p)
      if not r then return nil, "not found" end
      local f = io.open(r, "rb")
      local s = f:read("*a")
      f:close()
      return s, #s
    end,
    -- writes are dropped: the engine never persists anything, so many processes can share the sandbox
    write = function() return true end,
    append = function() return true end,
    remove = function() return true end,
    createDirectory = function() return true end,
    getDirectoryItems = function() return {} end,
    getSourceBaseDirectory = function() return SRC end,
    getSaveDirectory = function() return SAVE end,
    getIdentity = function() return "Balatro" end,
    setIdentity = noop,
    load = function(p)
      local s = love.filesystem.read(p)
      return s and loadstring(s, "@" .. p) or nil
    end,
  }

  love.handlers = setmetatable({}, { __index = function() return noop end })
  _G.love = love
  return love
end

M.DUMMY = DUMMY
return M
