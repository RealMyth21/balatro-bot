-- Test only: an endpoint that never answers (a loop inside one frame), for EngineClient's time limit.
assert(BB_DISPATCHER.register({
  name = "engine_test_hang", description = "test only: never answers", schema = {},
  execute = function() while true do end end,
}))
