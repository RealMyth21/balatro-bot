# ruff: noqa: E501  (a table of descriptions, one per line)
"""Effect text for jackdaw's gamestate views, which name items but don't describe them.

The real game (and our exact engine) puts each item's effect in its view: a joker's `value.effect`,
a boss blind's `effect`, a skip tag's `tag_effect`, a playing card's chips. An LLM playing on jackdaw
needs the same information, or it plays blind: `add_effects(view, state)` fills those fields from
jackdaw's game state (`Game.to_dict`), with the current values the game would show ("Currently X2.5
Mult", the chips of each card, the odds after Oops! All 6s).

The wording is our own (the game's text is not reproduced); every number matches the real game's text
for the same state, which scripts/check_descriptions.py checks against the exact engine.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable
from functools import lru_cache
from pathlib import Path
from typing import Any


def _n(v: Any) -> str:
    """A number as the game prints it: whole numbers without a decimal point."""
    if isinstance(v, bool) or v is None:
        return str(v)
    if isinstance(v, float):
        if v == int(v):
            return str(int(v))
        return f"{v:.10g}"
    return str(v)


@lru_cache(maxsize=1)
def _data() -> dict[str, dict]:
    import jackdaw

    d = Path(jackdaw.__file__).parent / "engine" / "data"
    return {name: json.loads((d / f"{name}.json").read_text()) for name in ("centers", "tags", "blinds", "hands")}


class _Ctx:
    """What a description can draw on: the game state and a few counts the game computes on the fly."""

    def __init__(self, state: dict):
        self.state = state
        g = state.get("game") or {}
        self.p = g.get("probabilities_normal") or 1
        self.money = g.get("dollars") or 0
        self.start_size = g.get("starting_deck_size") or 52
        self.skips = g.get("skips") or 0
        self.cr = state.get("current_round") or {}
        areas = state.get("areas") or {}
        self.jokers = areas.get("jokers") or []
        self.deck_left = len(areas.get("deck") or [])
        self.playing = [c for a in ("deck", "hand", "discard", "play") for c in (areas.get(a) or [])]
        self.hands = state.get("hands") or {}
        usage = state.get("consumeable_usage") or {}
        self.tarots_used = (usage.get("totals") or {}).get("tarot") or 0
        centers = _data()["centers"]
        self.planets_used = sum(1 for k in (usage.get("by_key") or {}) if (centers.get(k) or {}).get("set") == "Planet")
        self.joker_limit = g.get("joker_slots") or 5

    def count(self, pred: Callable[[dict], bool]) -> int:
        return sum(1 for c in self.playing if pred(c))


def _rank(card: dict) -> str | None:
    front = card.get("front")
    return front.split("_", 1)[1] if front and card.get("center") != "m_stone" else None


def _odds(c: _Ctx, odds: Any) -> str:
    return f"{_n(c.p)} in {_n(odds)}"


# Each joker: (ability with the joker's config filled in, its `extra`, the context) -> text.
_J: dict[str, Callable[[dict, Any, _Ctx], str]] = {
    "j_joker": lambda a, x, c: f"+{_n(a['mult'])} Mult",
    "j_greedy_joker": lambda a, x, c: f"Played {x['suit']} give +{_n(x['s_mult'])} Mult each when scored",
    "j_lusty_joker": lambda a, x, c: f"Played {x['suit']} give +{_n(x['s_mult'])} Mult each when scored",
    "j_wrathful_joker": lambda a, x, c: f"Played {x['suit']} give +{_n(x['s_mult'])} Mult each when scored",
    "j_gluttenous_joker": lambda a, x, c: f"Played {x['suit']} give +{_n(x['s_mult'])} Mult each when scored",
    "j_jolly": lambda a, x, c: f"+{_n(a['t_mult'])} Mult if the played hand contains a {a['type']}",
    "j_zany": lambda a, x, c: f"+{_n(a['t_mult'])} Mult if the played hand contains a {a['type']}",
    "j_mad": lambda a, x, c: f"+{_n(a['t_mult'])} Mult if the played hand contains a {a['type']}",
    "j_crazy": lambda a, x, c: f"+{_n(a['t_mult'])} Mult if the played hand contains a {a['type']}",
    "j_droll": lambda a, x, c: f"+{_n(a['t_mult'])} Mult if the played hand contains a {a['type']}",
    "j_sly": lambda a, x, c: f"+{_n(a['t_chips'])} Chips if the played hand contains a {a['type']}",
    "j_wily": lambda a, x, c: f"+{_n(a['t_chips'])} Chips if the played hand contains a {a['type']}",
    "j_clever": lambda a, x, c: f"+{_n(a['t_chips'])} Chips if the played hand contains a {a['type']}",
    "j_devious": lambda a, x, c: f"+{_n(a['t_chips'])} Chips if the played hand contains a {a['type']}",
    "j_crafty": lambda a, x, c: f"+{_n(a['t_chips'])} Chips if the played hand contains a {a['type']}",
    "j_half": lambda a, x, c: f"+{_n(x['mult'])} Mult if the played hand has {_n(x['size'])} or fewer cards",
    "j_stencil": lambda a, x, c: ("X1 Mult per empty Joker slot, this Joker counting as empty (Currently X"
                                  f"{_n(c.joker_limit - len(c.jokers) + sum(j.get('center') == 'j_stencil' for j in c.jokers))})"),
    "j_four_fingers": lambda a, x, c: "Flushes and Straights can be made with 4 cards",
    "j_mime": lambda a, x, c: "Retriggers the abilities of all cards held in hand",
    "j_credit_card": lambda a, x, c: f"You can go into debt down to -${_n(x)}",
    "j_ceremonial": lambda a, x, c: ("When a Blind is selected, destroys the Joker to its right and adds twice that "
                                     f"Joker's sell value to this Joker's Mult (Currently +{_n(a['mult'])} Mult)"),
    "j_banner": lambda a, x, c: f"+{_n(x)} Chips per discard you have left",
    "j_mystic_summit": lambda a, x, c: f"+{_n(x['mult'])} Mult while you have {_n(x['d_remaining'])} discards left",
    "j_marble": lambda a, x, c: "Adds a Stone card to your deck when a Blind is selected",
    "j_loyalty_card": lambda a, x, c: (f"X{_n(x['Xmult'])} Mult on every {_n(x['every'] + 1)}th hand played ("
                                       + ("Active!" if a.get("loyalty_remaining") == 0
                                          else f"{_n(a.get('loyalty_remaining', x['every']))} remaining") + ")"),
    "j_8_ball": lambda a, x, c: f"{_odds(c, x)} chance for each scoring 8 to create a Tarot card (needs room)",
    "j_misprint": lambda a, x, c: f"Adds a random amount of Mult, from {_n(x['min'])} to {_n(x['max'])}",
    "j_dusk": lambda a, x, c: "Retriggers every played card in the last hand of the round",
    "j_raised_fist": lambda a, x, c: "Adds twice the rank of the lowest card held in hand to Mult",
    "j_chaos": lambda a, x, c: f"{_n(x)} free Reroll in each shop",
    "j_fibonacci": lambda a, x, c: f"Each scoring Ace, 2, 3, 5 or 8 gives +{_n(x)} Mult",
    "j_steel_joker": lambda a, x, c: (f"X{_n(x)} Mult for each Steel card in your full deck (Currently X"
                                      f"{_n(1 + x * c.count(lambda p: p.get('center') == 'm_steel'))} Mult)"),
    "j_scary_face": lambda a, x, c: f"Scoring face cards give +{_n(x)} Chips each",
    "j_abstract": lambda a, x, c: f"+{_n(x)} Mult for each Joker you have (Currently +{_n(x * len(c.jokers))} Mult)",
    "j_delayed_grat": lambda a, x, c: f"Earn ${_n(x)} per discard left if you used none by the end of the round",
    "j_hack": lambda a, x, c: "Retriggers each played 2, 3, 4 or 5",
    "j_pareidolia": lambda a, x, c: "Every card counts as a face card",
    "j_gros_michel": lambda a, x, c: (f"+{_n(x['mult'])} Mult; {_odds(c, x['odds'])} chance to be destroyed at the end "
                                      "of the round"),
    "j_even_steven": lambda a, x, c: f"Scoring cards of even rank (10, 8, 6, 4, 2) give +{_n(x)} Mult each",
    "j_odd_todd": lambda a, x, c: f"Scoring cards of odd rank (A, 9, 7, 5, 3) give +{_n(x)} Chips each",
    "j_scholar": lambda a, x, c: f"Scoring Aces give +{_n(x['chips'])} Chips and +{_n(x['mult'])} Mult each",
    "j_business": lambda a, x, c: f"Scoring face cards have a {_odds(c, x)} chance to give $2",
    "j_supernova": lambda a, x, c: "Adds to Mult the number of times the played poker hand was played this run",
    "j_ride_the_bus": lambda a, x, c: (f"Gains +{_n(x)} Mult for each hand in a row played without a scoring face card "
                                       f"(Currently +{_n(a['mult'])} Mult)"),
    "j_space": lambda a, x, c: f"{_odds(c, x)} chance to level up the played poker hand",
    "j_egg": lambda a, x, c: f"Its sell value goes up by ${_n(x)} at the end of each round",
    "j_burglar": lambda a, x, c: f"When a Blind is selected, gain +{_n(x)} hands and lose all discards",
    "j_blackboard": lambda a, x, c: f"X{_n(x)} Mult if every card held in hand is a Spade or a Club",
    "j_runner": lambda a, x, c: (f"Gains +{_n(x['chip_mod'])} Chips when the played hand contains a Straight "
                                 f"(Currently +{_n(x['chips'])} Chips)"),
    "j_ice_cream": lambda a, x, c: f"+{_n(x['chips'])} Chips, losing {_n(x['chip_mod'])} Chips per hand played",
    "j_dna": lambda a, x, c: ("If the first hand of the round is 1 card, adds a permanent copy of it to the deck and "
                              "draws it to hand"),
    "j_splash": lambda a, x, c: "Every played card scores",
    "j_blue_joker": lambda a, x, c: (f"+{_n(x)} Chips per card left in your deck (Currently "
                                     f"+{_n(x * c.deck_left)} Chips)"),
    "j_sixth_sense": lambda a, x, c: ("If the first hand of the round is a single 6, destroys it and creates a Spectral "
                                      "card (needs room)"),
    "j_constellation": lambda a, x, c: (f"Gains X{_n(x)} Mult each time a Planet card is used (Currently "
                                        f"X{_n(a['x_mult'])} Mult)"),
    "j_hiker": lambda a, x, c: f"Every scoring card permanently gains +{_n(x)} Chips",
    "j_faceless": lambda a, x, c: f"Earn ${_n(x['dollars'])} when you discard {_n(x['faces'])} or more face cards at once",
    "j_green_joker": lambda a, x, c: (f"+{_n(x['hand_add'])} Mult per hand played, -{_n(x['discard_sub'])} Mult per "
                                      f"discard (Currently +{_n(a['mult'])} Mult)"),
    "j_superposition": lambda a, x, c: "Creates a Tarot card if the hand contains an Ace and a Straight (needs room)",
    "j_todo_list": lambda a, x, c: (f"Earn ${_n(x['dollars'])} if the played poker hand is a "
                                    f"{a.get('to_do_poker_hand', x.get('poker_hand'))}; the hand changes every round"),
    "j_cavendish": lambda a, x, c: (f"X{_n(x['Xmult'])} Mult; {_odds(c, x['odds'])} chance to be destroyed at the end "
                                    "of the round"),
    "j_card_sharp": lambda a, x, c: f"X{_n(x['Xmult'])} Mult if this poker hand was already played this round",
    "j_red_card": lambda a, x, c: (f"Gains +{_n(x)} Mult whenever a Booster Pack is skipped (Currently "
                                   f"+{_n(a['mult'])} Mult)"),
    "j_madness": lambda a, x, c: (f"When the Small or Big Blind is selected, gains X{_n(x)} Mult and destroys a random "
                                  f"Joker (Currently X{_n(a['x_mult'])} Mult)"),
    "j_square": lambda a, x, c: (f"Gains +{_n(x['chip_mod'])} Chips if the played hand has exactly 4 cards "
                                 f"(Currently {_n(x['chips'])} Chips)"),
    "j_seance": lambda a, x, c: f"If the poker hand is a {x['poker_hand']}, creates a random Spectral card (needs room)",
    "j_riff_raff": lambda a, x, c: f"When a Blind is selected, creates {_n(x)} Common Jokers (needs room)",
    "j_vampire": lambda a, x, c: (f"Gains X{_n(x)} Mult per scoring Enhanced card, removing its Enhancement "
                                  f"(Currently X{_n(a['x_mult'])} Mult)"),
    "j_shortcut": lambda a, x, c: "Straights may have gaps of 1 rank between cards (e.g. 10 8 6 5 3)",
    "j_hologram": lambda a, x, c: (f"Gains X{_n(x)} Mult whenever a playing card is added to your deck "
                                   f"(Currently X{_n(a['x_mult'])} Mult)"),
    "j_vagabond": lambda a, x, c: f"Creates a Tarot card if a hand is played with ${_n(x)} or less",
    "j_baron": lambda a, x, c: f"Each King held in hand gives X{_n(x)} Mult",
    "j_cloud_9": lambda a, x, c: (f"Earn ${_n(x)} for each 9 in your full deck at the end of the round (Currently "
                                  f"${_n(x * c.count(lambda p: _rank(p) == '9'))})"),
    "j_rocket": lambda a, x, c: (f"Earn ${_n(x['dollars'])} at the end of the round; the payout grows by "
                                 f"${_n(x['increase'])} each time a Boss Blind is beaten"),
    "j_obelisk": lambda a, x, c: (f"Gains X{_n(x)} Mult for each hand in a row that isn't your most played poker hand "
                                  f"(Currently X{_n(a['x_mult'])} Mult)"),
    "j_midas_mask": lambda a, x, c: "Every scoring face card becomes a Gold card",
    "j_luchador": lambda a, x, c: "Sell this card to disable the current Boss Blind",
    "j_photograph": lambda a, x, c: f"The first scoring face card gives X{_n(x)} Mult",
    "j_gift": lambda a, x, c: f"Adds ${_n(x)} of sell value to every Joker and consumable at the end of the round",
    "j_turtle_bean": lambda a, x, c: (f"+{_n(x['h_size'])} hand size, shrinking by {_n(x['h_mod'])} every round"),
    "j_erosion": lambda a, x, c: (f"+{_n(x)} Mult per card your full deck has below {_n(c.start_size)} (Currently "
                                  f"+{_n(max(0, x * (c.start_size - len(c.playing))))} Mult)"),
    "j_reserved_parking": lambda a, x, c: (f"Each face card held in hand has a {_odds(c, x['odds'])} chance to give "
                                           f"${_n(x['dollars'])}"),
    "j_mail": lambda a, x, c: (f"Earn ${_n(x)} for each discarded {(c.cr.get('mail_card') or {}).get('rank', '?')}; "
                               "the rank changes every round"),
    "j_to_the_moon": lambda a, x, c: f"Earn ${_n(x)} more interest per $5 you hold at the end of the round",
    "j_hallucination": lambda a, x, c: f"{_odds(c, x)} chance to create a Tarot card when a Booster Pack is opened (needs room)",
    "j_fortune_teller": lambda a, x, c: (f"+{_n(x)} Mult per Tarot card used this run (Currently "
                                         f"+{_n(x * c.tarots_used)} Mult)"),
    "j_juggler": lambda a, x, c: f"+{_n(a['h_size'])} hand size",
    "j_drunkard": lambda a, x, c: f"+{_n(a['d_size'])} discard every round",
    "j_stone": lambda a, x, c: (f"+{_n(x)} Chips for each Stone card in your full deck (Currently "
                                f"+{_n(x * c.count(lambda p: p.get('center') == 'm_stone'))} Chips)"),
    "j_golden": lambda a, x, c: f"Earn ${_n(x)} at the end of the round",
    "j_lucky_cat": lambda a, x, c: (f"Gains X{_n(x)} Mult each time a Lucky card triggers (Currently "
                                    f"X{_n(a['x_mult'])} Mult)"),
    "j_baseball": lambda a, x, c: f"Each Uncommon Joker gives X{_n(x)} Mult",
    "j_bull": lambda a, x, c: f"+{_n(x)} Chips per $1 you have (Currently +{_n(x * max(0, c.money))} Chips)",
    "j_diet_cola": lambda a, x, c: "Sell this card to create a free Double Tag",
    "j_trading": lambda a, x, c: f"If the first discard of the round is 1 card, destroys it and earns ${_n(x)}",
    "j_flash": lambda a, x, c: f"Gains +{_n(x)} Mult per reroll in the shop (Currently +{_n(a['mult'])} Mult)",
    "j_popcorn": lambda a, x, c: f"+{_n(a['mult'])} Mult, losing {_n(x)} Mult every round",
    "j_trousers": lambda a, x, c: (f"Gains +{_n(x)} Mult if the played hand contains a Two Pair (Currently "
                                   f"+{_n(a['mult'])} Mult)"),
    "j_ancient": lambda a, x, c: (f"Each scoring card of suit {(c.cr.get('ancient_card') or {}).get('suit', '?')} gives "
                                  f"X{_n(x)} Mult; the suit changes at the end of the round"),
    "j_ramen": lambda a, x, c: f"X{_n(a['x_mult'])} Mult, losing X{_n(x)} Mult per card discarded",
    "j_walkie_talkie": lambda a, x, c: f"Each scoring 10 or 4 gives +{_n(x['chips'])} Chips and +{_n(x['mult'])} Mult",
    "j_selzer": lambda a, x, c: f"Retriggers every played card for the next {_n(x)} hands",
    "j_castle": lambda a, x, c: (f"Gains +{_n(x['chip_mod'])} Chips per discarded "
                                 f"{(c.cr.get('castle_card') or {}).get('suit', '?')} card; the suit changes every round "
                                 f"(Currently +{_n(x['chips'])} Chips)"),
    "j_smiley": lambda a, x, c: f"Scoring face cards give +{_n(x)} Mult each",
    "j_campfire": lambda a, x, c: (f"Gains X{_n(x)} Mult per card sold, resetting when a Boss Blind is beaten "
                                   f"(Currently X{_n(a['x_mult'])} Mult)"),
    "j_ticket": lambda a, x, c: f"Scoring Gold cards earn ${_n(x)} each",
    "j_mr_bones": lambda a, x, c: "Prevents a loss if you scored at least 25% of the required chips, then destroys itself",
    "j_acrobat": lambda a, x, c: f"X{_n(x)} Mult on the last hand of the round",
    "j_sock_and_buskin": lambda a, x, c: "Retriggers every played face card",
    "j_swashbuckler": lambda a, x, c: ("Adds the total sell value of your other Jokers to Mult (Currently +"
                                       + _n(sum((j.get('costs') or {}).get('sell_cost', 0) for j in c.jokers
                                                if j.get('center') != 'j_swashbuckler')) + " Mult)"),
    "j_troubadour": lambda a, x, c: f"+{_n(x['h_size'])} hand size, -{_n(-x['h_plays'])} hand every round",
    "j_certificate": lambda a, x, c: "When the round starts, adds a random playing card with a random seal to your hand",
    "j_smeared": lambda a, x, c: "Hearts and Diamonds count as one suit, and Spades and Clubs as another",
    "j_throwback": lambda a, x, c: (f"X{_n(x)} Mult per Blind skipped this run (Currently "
                                    f"X{_n(1 + x * c.skips)} Mult)"),
    "j_hanging_chad": lambda a, x, c: f"Retriggers the first scoring card {_n(x)} more times",
    "j_rough_gem": lambda a, x, c: f"Each scoring Diamond earns ${_n(x)}",
    "j_bloodstone": lambda a, x, c: f"Each scoring Heart has a {_odds(c, x['odds'])} chance to give X{_n(x['Xmult'])} Mult",
    "j_arrowhead": lambda a, x, c: f"Each scoring Spade gives +{_n(x)} Chips",
    "j_onyx_agate": lambda a, x, c: f"Each scoring Club gives +{_n(x)} Mult",
    "j_glass": lambda a, x, c: (f"Gains X{_n(x)} Mult for every Glass card destroyed (Currently "
                                f"X{_n(a['x_mult'])} Mult)"),
    "j_ring_master": lambda a, x, c: "Jokers, Tarot, Planet and Spectral cards can show up more than once",
    "j_flower_pot": lambda a, x, c: f"X{_n(x)} Mult if the hand contains a Diamond, a Club, a Heart and a Spade",
    "j_blueprint": lambda a, x, c: "Copies the ability of the Joker to its right",
    "j_wee": lambda a, x, c: (f"Gains +{_n(x['chip_mod'])} Chips each time a played 2 scores (Currently "
                              f"+{_n(x['chips'])} Chips)"),
    "j_merry_andy": lambda a, x, c: f"+{_n(a['d_size'])} discards every round, {_n(a['h_size'])} hand size",
    "j_oops": lambda a, x, c: "Doubles every listed probability (e.g. 1 in 3 becomes 2 in 3)",
    "j_idol": lambda a, x, c: (f"Each scoring {(c.cr.get('idol_card') or {}).get('rank', '?')} of "
                               f"{(c.cr.get('idol_card') or {}).get('suit', '?')} gives X{_n(x)} Mult; the card changes "
                               "every round"),
    "j_seeing_double": lambda a, x, c: (f"X{_n(x)} Mult if the hand has a scoring Club and a scoring card of another "
                                        "suit"),
    "j_matador": lambda a, x, c: f"Earn ${_n(x)} if the played hand triggers the Boss Blind's ability",
    "j_hit_the_road": lambda a, x, c: (f"Gains X{_n(x)} Mult per Jack discarded this round (Currently "
                                       f"X{_n(a['x_mult'])} Mult)"),
    "j_duo": lambda a, x, c: f"X{_n(a['x_mult'])} Mult if the played hand contains a {a['type']}",
    "j_trio": lambda a, x, c: f"X{_n(a['x_mult'])} Mult if the played hand contains a {a['type']}",
    "j_family": lambda a, x, c: f"X{_n(a['x_mult'])} Mult if the played hand contains a {a['type']}",
    "j_order": lambda a, x, c: f"X{_n(a['x_mult'])} Mult if the played hand contains a {a['type']}",
    "j_tribe": lambda a, x, c: f"X{_n(a['x_mult'])} Mult if the played hand contains a {a['type']}",
    "j_stuntman": lambda a, x, c: f"+{_n(x['chip_mod'])} Chips, -{_n(x['h_size'])} hand size",
    "j_invisible": lambda a, x, c: (f"After {_n(x)} rounds, sell it to duplicate a random Joker (Currently "
                                    f"{_n(a.get('invis_rounds', 0))}/{_n(x)})"),
    "j_brainstorm": lambda a, x, c: "Copies the ability of your leftmost Joker",
    "j_satellite": lambda a, x, c: (f"Earn ${_n(x)} at the end of the round per different Planet card used this run "
                                    f"(Currently ${_n(x * c.planets_used)})"),
    "j_shoot_the_moon": lambda a, x, c: f"Each Queen held in hand gives +{_n(x)} Mult",
    "j_drivers_license": lambda a, x, c: (f"X{_n(x)} Mult if your full deck has at least 16 Enhanced cards (Currently "
                                          f"{_n(c.count(lambda p: (p.get('center') or 'c_base') != 'c_base'))})"),
    "j_cartomancer": lambda a, x, c: "Creates a Tarot card when a Blind is selected (needs room)",
    "j_astronomer": lambda a, x, c: "Planet cards and Celestial Packs in the shop are free",
    "j_burnt": lambda a, x, c: "Levels up the poker hand of the first discard of each round",
    "j_bootstraps": lambda a, x, c: (f"+{_n(x['mult'])} Mult per ${_n(x['dollars'])} you have (Currently "
                                     f"+{_n(x['mult'] * math.floor(max(0, c.money) / x['dollars']))} Mult)"),
    "j_caino": lambda a, x, c: (f"Gains X{_n(x)} Mult when a face card is destroyed (Currently "
                                f"X{_n(a.get('caino_xmult', 1))} Mult)"),
    "j_triboulet": lambda a, x, c: f"Scoring Kings and Queens give X{_n(x)} Mult each",
    "j_yorick": lambda a, x, c: (f"Gains X{_n(x['xmult'])} Mult every {_n(x['discards'])} cards discarded "
                                 f"({_n(a.get('yorick_discards', x['discards']))} left) (Currently "
                                 f"X{_n(a['x_mult'])} Mult)"),
    "j_chicot": lambda a, x, c: "Disables the effect of every Boss Blind",
    "j_perkeo": lambda a, x, c: "At the end of the shop, creates a Negative copy of 1 random consumable you hold",
}

_ENH = {"m_bonus": "Bonus Card", "m_mult": "Mult Card", "m_wild": "Wild Card", "m_glass": "Glass Card",
        "m_steel": "Steel Card", "m_stone": "Stone Card", "m_gold": "Gold Card", "m_lucky": "Lucky Card"}
_SEAL = {"Gold": "Gold Seal", "Red": "Red Seal", "Blue": "Blue Seal", "Purple": "Purple Seal"}


def _tarot(key: str, cfg: dict, c: _Ctx) -> str:
    n = _n(cfg.get("max_highlighted", 1))
    if cfg.get("mod_conv", "").startswith("m_"):
        return f"Turns up to {n} selected cards into {_ENH[cfg['mod_conv']]}s"
    if cfg.get("suit_conv"):
        return f"Changes up to {n} selected cards to {cfg['suit_conv']}"
    return {
        "c_fool": lambda: "Creates a copy of the last Tarot or Planet card used this run (not The Fool)",
        "c_high_priestess": lambda: f"Creates up to {_n(cfg.get('planets'))} random Planet cards (needs room)",
        "c_emperor": lambda: f"Creates up to {_n(cfg.get('tarots'))} random Tarot cards (needs room)",
        "c_hermit": lambda: f"Doubles your money (at most ${_n(cfg.get('extra'))})",
        "c_wheel_of_fortune": lambda: (f"{_odds(c, cfg.get('extra'))} chance to give a random Joker Foil, Holographic or "
                               "Polychrome"),
        "c_strength": lambda: f"Raises the rank of up to {n} selected cards by 1",
        "c_hanged_man": lambda: f"Destroys up to {n} selected cards",
        "c_death": lambda: f"With {n} cards selected, turns the left card into a copy of the right one",
        "c_temperance": lambda: (f"Gives the total sell value of your Jokers (at most ${_n(cfg.get('extra'))}) (Currently $"
                         + _n(min(cfg.get("extra") or 50, sum((j.get("costs") or {}).get("sell_cost", 0)
                                                               for j in c.jokers))) + ")"),
        "c_judgement": lambda: "Creates a random Joker (needs room)",
    }.get(key, lambda: "")()


def _planet(cfg: dict, c: _Ctx) -> str:
    hand = cfg.get("hand_type", "?")
    h = {**(_data()["hands"].get(hand) or {}), **(c.hands.get(hand) or {})}
    return (f"(lvl.{_n(h.get('level', 1))}) Levels up {hand}: +{_n(h.get('l_mult'))} Mult and "
            f"+{_n(h.get('l_chips'))} chips")


def _spectral(key: str, cfg: dict, c: _Ctx) -> str:
    x = cfg.get("extra")
    return {
        "c_familiar": lambda: f"Destroys 1 random card in hand and adds {_n(x)} random Enhanced face cards to it",
        "c_grim": lambda: f"Destroys 1 random card in hand and adds {_n(x)} random Enhanced Aces to it",
        "c_incantation": lambda: f"Destroys 1 random card in hand and adds {_n(x)} random Enhanced number cards to it",
        "c_talisman": lambda: f"Adds a {_SEAL.get(x, x)} to 1 selected card",
        "c_deja_vu": lambda: f"Adds a {_SEAL.get(x, x)} to 1 selected card",
        "c_trance": lambda: f"Adds a {_SEAL.get(x, x)} to 1 selected card",
        "c_medium": lambda: f"Adds a {_SEAL.get(x, x)} to 1 selected card",
        "c_aura": lambda: "Gives 1 selected card in hand Foil, Holographic or Polychrome",
        "c_wraith": lambda: "Creates a random Rare Joker (needs room) and sets your money to $0",
        "c_sigil": lambda: "Turns every card in hand into one random suit",
        "c_ouija": lambda: "Turns every card in hand into one random rank; -1 hand size",
        "c_ectoplasm": lambda: f"Makes a random Joker Negative; -{_n((c.state.get('game') or {}).get('ecto_minus') or 1)} hand size",
        "c_immolate": lambda: f"Destroys {_n(x['destroy'])} random cards in hand and gives ${_n(x['dollars'])}",
        "c_ankh": lambda: "Copies a random Joker and destroys all your other Jokers",
        "c_hex": lambda: "Makes a random Joker Polychrome and destroys all your other Jokers",
        "c_cryptid": lambda: f"Creates {_n(x)} copies of 1 selected card",
        "c_soul": lambda: "Creates a Legendary Joker (needs room)",
        "c_black_hole": lambda: "Levels up every poker hand by 1",
    }.get(key, lambda: "")()


def _voucher(key: str, cfg: dict) -> str:
    x = cfg.get("extra")
    return {
        "v_overstock_norm": lambda: "+1 card slot in the shop",
        "v_overstock_plus": lambda: "+1 card slot in the shop",
        "v_clearance_sale": lambda: f"Everything in the shop is {_n(x)}% off",
        "v_liquidation": lambda: f"Everything in the shop is {_n(x)}% off",
        "v_hone": lambda: f"Foil, Holographic and Polychrome cards show up {_n(x)}X as often",
        "v_glow_up": lambda: f"Foil, Holographic and Polychrome cards show up {_n(x)}X as often",
        "v_reroll_surplus": lambda: f"Rerolls cost ${_n(x)} less",
        "v_reroll_glut": lambda: f"Rerolls cost ${_n(x)} less",
        "v_crystal_ball": lambda: "+1 consumable slot",
        "v_omen_globe": lambda: "Arcana Packs can contain Spectral cards",
        "v_telescope": lambda: "Celestial Packs always include the Planet card for your most played poker hand",
        "v_observatory": lambda: f"Planet cards you hold give X{_n(x)} Mult when their poker hand is played",
        "v_grabber": lambda: f"+{_n(x)} hand every round, permanently",
        "v_nacho_tong": lambda: f"+{_n(x)} hand every round, permanently",
        "v_wasteful": lambda: f"+{_n(x)} discard every round, permanently",
        "v_recyclomancy": lambda: f"+{_n(x)} discard every round, permanently",
        "v_tarot_merchant": lambda: f"Tarot cards show up {_n(cfg.get('extra_disp'))}X as often in the shop",
        "v_tarot_tycoon": lambda: f"Tarot cards show up {_n(cfg.get('extra_disp'))}X as often in the shop",
        "v_planet_merchant": lambda: f"Planet cards show up {_n(cfg.get('extra_disp'))}X as often in the shop",
        "v_planet_tycoon": lambda: f"Planet cards show up {_n(cfg.get('extra_disp'))}X as often in the shop",
        "v_seed_money": lambda: f"Interest per round can reach ${_n(x / 5)}",
        "v_money_tree": lambda: f"Interest per round can reach ${_n(x / 5)}",
        "v_blank": lambda: "Does nothing on its own",
        "v_antimatter": lambda: "+1 Joker slot",
        "v_magic_trick": lambda: "Playing cards can be bought in the shop",
        "v_illusion": lambda: "Playing cards in the shop can have an Enhancement, Edition and/or Seal",
        "v_hieroglyph": lambda: "-1 Ante, and -1 hand every round",
        "v_petroglyph": lambda: "-1 Ante, and -1 discard every round",
        "v_directors_cut": lambda: f"Reroll the Boss Blind 1 time per Ante, ${_n(x)} per reroll",
        "v_retcon": lambda: f"Reroll the Boss Blind as often as you like, ${_n(x)} per reroll",
        "v_paint_brush": lambda: "+1 hand size",
        "v_palette": lambda: "+1 hand size",
    }.get(key, lambda: "")()


_PACK_KIND = {"Arcana": ("Tarot", " to use right away"), "Celestial": ("Planet", " to use right away"),
              "Spectral": ("Spectral", " to use right away"), "Standard": ("Playing", " to add to your deck"),
              "Buffoon": ("Joker", "")}


def _booster(center: dict) -> str:
    cfg = center.get("config") or {}
    kind, tail = _PACK_KIND.get(center.get("kind"), ("", ""))
    return f"Pick {_n(cfg.get('choose'))} of up to {_n(cfg.get('extra'))} {kind} cards{tail}"


def _playing_card(card: dict, c: _Ctx) -> str:
    center = card.get("center") or "c_base"
    ab = card.get("ability") or {}
    if center == "m_stone":
        return f"+{_n(ab.get('bonus', 50) + (ab.get('perma_bonus') or 0))} Chips, no rank or suit"
    nominal = (card.get("base") or {}).get("nominal") or 0
    perma = ab.get("perma_bonus") or 0  # chips added for good (Hiker), shown apart from the rank's chips
    text = f"+{_n(nominal)} chips"
    if center == "m_bonus":
        return f"{text} +{_n(ab.get('bonus', 30) + perma)} extra chips"
    if perma:
        tail = f" +{_n(perma)} extra chips"
    else:
        tail = ""
    extra = {
        "m_mult": lambda: f"+{_n(ab.get('mult', 4))} Mult",
        "m_wild": lambda: "Counts as every suit",
        "m_glass": lambda: f"X{_n(ab.get('x_mult', 2))} Mult, {_odds(c, (ab.get('extra') or 4))} chance to break",
        "m_steel": lambda: f"X{_n(ab.get('h_x_mult', 1.5))} Mult while held in hand",
        "m_gold": lambda: f"${_n(ab.get('h_dollars', 3))} if held in hand at the end of the round",
        "m_lucky": lambda: (f"{_odds(c, 5)} chance for +{_n(ab.get('mult', 20))} Mult, {_odds(c, 15)} chance to win "
                            f"${_n(ab.get('p_dollars', 20))}"),
    }.get(center)
    return (f"{text} {extra()}" if extra else text) + tail


def _boss(key: str, c: _Ctx) -> str:
    return {
        "bl_small": lambda: "", "bl_big": lambda: "",
        "bl_arm": lambda: "Lowers the level of the played poker hand",
        "bl_club": lambda: "Every Club card is debuffed",
        "bl_eye": lambda: "No poker hand type can be played twice this round",
        "bl_final_acorn": lambda: "Turns all Jokers face down and shuffles them",
        "bl_final_bell": lambda: "1 card is always kept selected",
        "bl_final_heart": lambda: "A random Joker is disabled every hand",
        "bl_final_leaf": lambda: "Every card is debuffed until 1 Joker is sold",
        "bl_final_vessel": lambda: "A very large Blind",
        "bl_fish": lambda: "Cards drawn after each hand are face down",
        "bl_flint": lambda: "Base Chips and Mult are halved",
        "bl_goad": lambda: "Every Spade card is debuffed",
        "bl_head": lambda: "Every Heart card is debuffed",
        "bl_hook": lambda: "Discards 2 random cards from your hand after each hand played",
        "bl_house": lambda: "The first hand is drawn face down",
        "bl_manacle": lambda: "-1 hand size",
        "bl_mark": lambda: "Every face card is drawn face down",
        "bl_mouth": lambda: "Only 1 poker hand type can be played this round",
        "bl_needle": lambda: "Only 1 hand can be played",
        "bl_ox": lambda: (f"Playing a {c.cr.get('most_played_poker_hand', 'High Card')} sets your money to $0"),
        "bl_pillar": lambda: "Cards already played this Ante are debuffed",
        "bl_plant": lambda: "Every face card is debuffed",
        "bl_psychic": lambda: "Every hand must have 5 cards",
        "bl_serpent": lambda: "After each hand or discard, draws only 3 cards",
        "bl_tooth": lambda: "Lose $1 for each card played",
        "bl_wall": lambda: "An extra large Blind",
        "bl_water": lambda: "Start the round with 0 discards",
        "bl_wheel": lambda: f"{_odds(c, 7)} cards are drawn face down",
        "bl_window": lambda: "Every Diamond card is debuffed",
    }.get(key, lambda: "")()


def _tag(key: str, c: _Ctx) -> str:
    g = c.state.get("game") or {}
    return {
        "tag_boss": lambda: "Rerolls the Boss Blind",
        "tag_buffoon": lambda: "Gives a free Mega Buffoon Pack",
        "tag_charm": lambda: "Gives a free Mega Arcana Pack",
        "tag_coupon": lambda: "The cards and packs first shown in the next shop are free",
        "tag_d_six": lambda: "Rerolls in the next shop start at $0",
        "tag_double": lambda: "Copies the next Tag you take (not Double Tags)",
        "tag_economy": lambda: "Doubles your money (at most +$40)",
        "tag_ethereal": lambda: "Gives a free Spectral Pack",
        "tag_foil": lambda: "The next base-edition Joker in the shop is free and turns Foil",
        "tag_garbage": lambda: f"Gives $1 per unused discard this run (Will give ${_n(g.get('unused_discards') or 0)})",
        "tag_handy": lambda: f"Gives $1 per hand played this run (Will give ${_n(g.get('hands_played') or 0)})",
        "tag_holo": lambda: "The next base-edition Joker in the shop is free and turns Holographic",
        "tag_investment": lambda: "Gives $25 after you beat the Boss Blind",
        "tag_juggle": lambda: "+3 hand size for the next round",
        "tag_meteor": lambda: "Gives a free Mega Celestial Pack",
        "tag_negative": lambda: "The next base-edition Joker in the shop is free and turns Negative",
        "tag_orbital": lambda: "Levels up a poker hand by 3",
        "tag_polychrome": lambda: "The next base-edition Joker in the shop is free and turns Polychrome",
        "tag_rare": lambda: "The shop has a free Rare Joker",
        "tag_skip": lambda: f"Gives $5 per Blind skipped this run (Will give ${_n(5 * ((g.get('skips') or 0) + 1))})",
        "tag_standard": lambda: "Gives a free Mega Standard Pack",
        "tag_top_up": lambda: "Creates up to 2 Common Jokers (needs room)",
        "tag_uncommon": lambda: "The shop has a free Uncommon Joker",
        "tag_voucher": lambda: "Adds a Voucher to the next shop",
    }.get(key, lambda: "")()


def _item(card: dict, view_card: dict, c: _Ctx) -> str | None:
    """Effect text for one card of the state (`card`), shown as `view_card`."""
    centers = _data()["centers"]
    key = card.get("center")
    center = centers.get(key) or {}
    s = center.get("set")
    if card.get("front") or s in ("Enhanced", "Default"):
        return _playing_card(card, c)
    cfg = center.get("config") or {}
    if s == "Joker" and key in _J:
        ab = dict(cfg) if isinstance(cfg, dict) else {}
        own = card.get("ability") or {}
        ab.update({k: v for k, v in own.items() if v is not None})
        if "Xmult" in ab and not own.get("x_mult") and "x_mult" not in own:
            ab["x_mult"] = ab["Xmult"]
        ab.setdefault("x_mult", (cfg or {}).get("Xmult", 1) if isinstance(cfg, dict) else 1)
        try:
            return _J[key](ab, ab.get("extra"), c)
        except (KeyError, TypeError, ZeroDivisionError):
            return None
    if s == "Tarot":
        return _tarot(key, cfg, c)
    if s == "Planet":
        return _planet(cfg, c)
    if s == "Spectral":
        return _spectral(key, cfg, c)
    if s == "Voucher":
        return _voucher(key, cfg)
    if s == "Booster":
        return _booster(center)
    return None


def add_effects(view: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """The view with effect text filled in from jackdaw's game state; the view is not modified."""
    if not isinstance(view, dict) or "state" not in view:
        return view
    c = _Ctx(state)
    by_id = {card.get("sort_id"): card for area in (state.get("areas") or {}).values() if isinstance(area, list)
             for card in area}
    out = dict(view)
    for area in ("jokers", "consumables", "hand", "cards", "shop", "vouchers", "packs", "pack"):
        block = out.get(area)
        if not isinstance(block, dict) or not block.get("cards"):
            continue
        cards = []
        for vc in block["cards"]:
            card = by_id.get(vc.get("id"))
            if card is None and area == "vouchers":  # vouchers in the shop may not be in the state's areas
                card = {"center": vc.get("key")}
            try:
                text = _item(card, vc, c) if card is not None else None
            except Exception:  # noqa: BLE001  a description bug must never stop a game
                text = None
            if text is not None:
                vc = {**vc, "value": {**(vc.get("value") or {}), "effect": text}}
            cards.append(vc)
        out[area] = {**block, "cards": cards}
    blinds = out.get("blinds")
    if isinstance(blinds, dict):
        bl_by_name = {v.get("name"): k for k, v in _data()["blinds"].items()}
        tag_by_name = {v.get("name"): k for k, v in _data()["tags"].items()}
        nb = {}
        for slot, b in blinds.items():
            b = dict(b)
            for field, table, describe, name in (("effect", bl_by_name, _boss, b.get("name")),
                                                 ("tag_effect", tag_by_name, _tag, b.get("tag_name"))):
                if name in table:
                    try:
                        b[field] = describe(table[name], c)
                    except Exception:  # noqa: BLE001  as above
                        pass
            nb[slot] = b
        out["blinds"] = nb
    return out
