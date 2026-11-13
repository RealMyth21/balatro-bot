"""Poker-hand detection (balatro_bot.scoring.hands) against Balatro's rules. No game needed."""

from balatro_bot.scoring.hands import HandFlags, candidate_plays, classify, contained_hands


def c(code: str, enh: str | None = None, debuff: bool = False) -> dict:
    """'AH' -> Ace of Hearts; 'TS' -> Ten of Spades."""
    card = {"value": {"rank": code[0], "suit": code[1]}, "modifier": {}, "state": {}}
    if enh:
        card["modifier"]["enhancement"] = enh
    if debuff:
        card["state"]["debuff"] = True
    return card


def hand(*codes: str) -> list[dict]:
    return [c(x) for x in codes]


def name(cards, **flags) -> str:
    return classify(cards, HandFlags(**flags))[0]


def test_basic_hands():
    assert name(hand("AH")) == "High Card"
    assert name(hand("AH", "AS")) == "Pair"
    assert name(hand("AH", "AS", "KD", "KC")) == "Two Pair"
    assert name(hand("7H", "7S", "7D")) == "Three of a Kind"
    assert name(hand("7H", "7S", "7D", "7C")) == "Four of a Kind"
    assert name(hand("7H", "7S", "7D", "2C", "2H")) == "Full House"
    assert name(hand("2H", "5H", "9H", "JH", "KH")) == "Flush"
    assert name(hand("5C", "6H", "7D", "8S", "9H")) == "Straight"
    assert name(hand("5H", "6H", "7H", "8H", "9H")) == "Straight Flush"


def test_secret_hands():
    assert name([c("AH"), c("AH"), c("AS"), c("AD"), c("AC")]) == "Five of a Kind"
    assert name([c("AH")] * 5) == "Flush Five"
    assert name(hand("7H", "7H", "7H", "2H", "2H")) == "Flush House"


def test_ace_low_straight_but_no_wraparound():
    assert name(hand("AH", "2S", "3D", "4C", "5H")) == "Straight"
    assert name(hand("TH", "JS", "QD", "KC", "AH")) == "Straight"
    assert name(hand("QH", "KS", "AD", "2C", "3H")) == "High Card"


def test_scoring_cards():
    _, scoring = classify(hand("AH", "AS", "KD", "7C", "2H"))
    assert [x["value"]["rank"] for x in scoring] == ["A", "A"]
    _, scoring = classify(hand("2H", "9D", "KS"))
    assert [x["value"]["rank"] for x in scoring] == ["K"]  # high card scores only the top card


def test_stone_cards_always_score_and_have_no_rank():
    cards = [c("AH"), c("AS"), c("KD", enh="STONE")]
    n, scoring = classify(cards)
    assert n == "Pair"
    assert len(scoring) == 3


def test_joker_flags():
    four = hand("2H", "5H", "9H", "JH")
    assert name(four) == "High Card"
    assert name(four, four_fingers=True) == "Flush"
    assert name(hand("3C", "5H", "7D", "9S", "JH")) == "High Card"
    assert name(hand("3C", "5H", "7D", "9S", "JH"), shortcut=True) == "Straight"
    assert name(hand("2H", "5D", "9H", "JD", "KH"), smeared=True) == "Flush"
    _, scoring = classify(hand("AH", "AS", "KD"), HandFlags(splash=True))
    assert len(scoring) == 3


def test_wild_cards_count_for_any_suit():
    cards = [c("2H"), c("5H"), c("9H"), c("JH"), c("KS", enh="WILD")]
    assert name(cards) == "Flush"


def test_contained_hands():
    assert {"Pair", "Two Pair", "Three of a Kind", "Full House"} <= contained_hands(hand("7H", "7S", "7D", "2C", "2H"))


def test_candidate_play_count():
    assert sum(1 for _ in candidate_plays(list(range(8)))) == 218  # C(8,1..5)
