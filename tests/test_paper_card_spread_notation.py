"""Paper-card spreads use standard team notation; raw home-minus-away margins stay beside them."""
from cfb_edge_finder.research.paper_card import _home_away_of, team_spread_from_home_margin


def test_home_and_away_come_from_the_canonical_slug():
    assert _home_away_of("cfb-2026-wk01-memphis-at-unlv") == ("Unlv", "Memphis")
    assert _home_away_of("cfb-2026-wk00-texas-at-ohio-state") == ("Ohio State", "Texas")
    assert _home_away_of("not-a-slug") == ("HOME", "AWAY")


def test_projected_winner_is_named_with_a_minus_sign():
    assert team_spread_from_home_margin(4.2, "Ohio State", "Texas") == "Ohio State -4.2"
    assert team_spread_from_home_margin(-1.7, "Ohio State", "Texas") == "Texas -1.7"
    assert team_spread_from_home_margin(-1.7, "Ohio State", "Texas") != "Ohio State -1.7"
    assert team_spread_from_home_margin(21.0, "Ohio State", "Texas") == "Ohio State -21"
    assert team_spread_from_home_margin(0.04, "Ohio State", "Texas") == "Pick'em"
    assert team_spread_from_home_margin(None, "Ohio State", "Texas") == "-"
    assert team_spread_from_home_margin(float("nan"), "Ohio State", "Texas") == "-"
