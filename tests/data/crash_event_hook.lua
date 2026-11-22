-- Test hook (ENGINE_HOOKS): an endpoint that queues a game event which raises, as a buggy event would.
assert(BB_DISPATCHER.register({
  name = "test_crash_event", description = "queue an event that errors", schema = {},
  execute = function(_, send)
    G.E_MANAGER:add_event(Event({ func = function() error("boom inside an event") end }))
    G.E_MANAGER:add_event(Event({ func = function() send(BB_GAMESTATE.get_gamestate()) return true end }))
  end,
}))
