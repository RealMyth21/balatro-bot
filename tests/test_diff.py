"""State comparison helpers (balatro_bot.validation.diff). No game needed."""

from balatro_bot.validation.diff import first_diff, normalize, rules_view, same_value


def gs(**over):
    base = {
        "state": "SHOP", "money": 10, "ante_num": 2,
        "cards": {"count": 2, "limit": 52, "cards": [
            {"id": 7, "key": "H_A", "value": {"rank": "A", "suit": "H", "effect": "+11 chips"}},
            {"id": 3, "key": "S_2", "value": {"rank": "2", "suit": "S", "effect": "+2 chips"}},
        ]},
        "packs": {"cards": [{"id": 1, "key": "p_buffoon_normal_1"}]},
        "blinds": {"small": {"name": "Small Blind", "tag_effect": "Adds one Voucher to the next shop"}},
    }
    base.update(over)
    return base


def test_ignored_differences():
    a = gs()
    b = gs()
    b["cards"]["cards"].reverse()             # deck order outside a round
    b["cards"]["limit"] = 53                  # Steamodded deck-limit bookkeeping
    b["packs"]["cards"][0]["key"] = "p_buffoon_normal_2"   # cosmetic art variant
    for c in b["cards"]["cards"]:
        c["id"] += 100                        # per-process sort_id counters
    assert first_diff(normalize(a), normalize(b)) is None


def test_real_differences_are_reported():
    d = first_diff(normalize(gs()), normalize(gs(money=11)), labels=("engine", "jackdaw"))
    assert d == "$.money: engine=10 jackdaw=11"
    d = first_diff(normalize(gs()), normalize(gs(ante_num=3)))
    assert d.startswith("$.ante_num")


def test_rules_view_drops_text_only():
    a, b = gs(), gs()
    b["blinds"]["small"]["tag_effect"] = "Voucher Tag"
    b["cards"]["cards"][0]["value"]["effect"] = "Ace of Hearts"
    assert first_diff(normalize(a), normalize(b)) is not None
    assert first_diff(rules_view(a), rules_view(b)) is None


def test_same_value():
    assert same_value(1.0000000000001, 1.0)
    assert not same_value(1.01, 1.0)
    assert same_value(None, 0)
    assert same_value({"mult": 4, "extra": 1}, {"mult": 4.0})
    assert not same_value({"mult": 4}, {"mult": 5})
