-- Test instrumentation: ENGINE_HOOKS=engine/hooks/engine_add.lua adds an `engine_add` endpoint that
-- puts a joker, consumable or (in the shop) voucher into the run with the game's own functions.
-- balatrobot's `add` needs Steamodded, which the engine doesn't run; tests use this one to build
-- states that are rare in play (legendary jokers, secret planets) and compare them with jackdaw.

assert(BB_DISPATCHER.register({
  name = "engine_add",
  description = "test only: add a card by key with the game's own create_card",
  schema = { key = { type = "string", required = true, description = "j_*, c_* or v_* key" } },
  requires_state = { G.STATES.SELECTING_HAND, G.STATES.SHOP },

  execute = function(args, send_response)
    local center = G.P_CENTERS[args.key]
    if not center then
      send_response({ message = "unknown key " .. tostring(args.key), name = BB_ERROR_NAMES.BAD_REQUEST })
      return
    end
    local area, before
    if center.set == "Voucher" then
      if G.STATE ~= G.STATES.SHOP then
        send_response({ message = "vouchers can only be added in the shop", name = BB_ERROR_NAMES.INVALID_STATE })
        return
      end
      area = G.shop_vouchers
      before = #area.cards
      local card = Card(area.T.x + area.T.w / 2, area.T.y, G.CARD_W, G.CARD_H, G.P_CARDS.empty, center,
        { bypass_discovery_center = true, bypass_discovery_ui = true })
      card.shop_voucher = true
      create_shop_card_ui(card, "Voucher", area)
      card:start_materialize()
      area:emplace(card)
    else
      area = center.set == "Joker" and G.jokers or G.consumeables
      before = #area.cards
      local card = create_card(center.set, area, nil, nil, nil, nil, args.key)
      card:add_to_deck()
      area:emplace(card)
    end
    G.E_MANAGER:add_event(Event({
      trigger = "condition",
      blocking = false,
      func = function()
        if #area.cards == before + 1 and G.STATE_COMPLETE == true and not G.CONTROLLER.locked then
          send_response(BB_GAMESTATE.get_gamestate())
          return true
        end
        return false
      end,
    }))
  end,
}))
