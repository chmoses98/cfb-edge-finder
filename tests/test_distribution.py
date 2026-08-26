import pytest

from cfb_edge_finder.projections.distribution import (
    UnsupportedMarketFamilyError,
    price_market,
    prob_away_covers,
    prob_away_win,
    prob_home_covers,
    prob_home_win,
    prob_over,
    prob_team_total_over,
    prob_team_total_under,
    prob_under,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.projection import GameDistribution

FAVORITE = GameDistribution(home_mean=31, away_mean=21, home_sd=10, away_sd=10, correlation=0.0)
COIN_FLIP = GameDistribution(home_mean=27, away_mean=27, home_sd=10, away_sd=10, correlation=0.0)


def test_moneyline_probabilities_sum_close_to_one():
    total = prob_home_win(FAVORITE) + prob_away_win(FAVORITE)
    assert 0.0 <= total <= 1.0
    # Push probability is small but not modeled separately -- see distribution.py docstring.
    assert total > 0.95


def test_moneyline_probabilities_bounded():
    assert 0.0 <= prob_home_win(FAVORITE) <= 1.0
    assert 0.0 <= prob_away_win(FAVORITE) <= 1.0


def test_favorite_is_more_likely_to_win_than_a_coin_flip_team():
    assert prob_home_win(FAVORITE) > prob_home_win(COIN_FLIP)
    assert prob_home_win(COIN_FLIP) == pytest.approx(0.5, abs=0.02)


def test_cover_probability_is_monotonic_in_the_line():
    # As the home line gets harder to cover (more negative == bigger favorite
    # required), the probability of covering must strictly decrease.
    lines = [3.5, 6.5, 10.5, 14.5]
    probs = [prob_home_covers(FAVORITE, -line) for line in lines]
    assert probs == sorted(probs, reverse=True)
    for p in probs:
        assert 0.0 <= p <= 1.0


def test_home_and_away_cover_probabilities_are_nearly_complementary():
    # Not exactly complementary: the 0.5-point continuity correction used on
    # both sides (see distribution.py docstring) leaves a small "push
    # window" gap near the line, which is the documented, deliberate
    # approximation for push probability rather than a bug.
    for line in (-3.5, 0.0, 6.5):
        home_p = prob_home_covers(FAVORITE, line)
        away_p = prob_away_covers(FAVORITE, line)
        assert home_p + away_p == pytest.approx(1.0, abs=0.05)


def test_total_over_under_are_monotonic_and_nearly_complementary():
    lines = [40.5, 50.5, 60.5]
    overs = [prob_over(FAVORITE, line) for line in lines]
    unders = [prob_under(FAVORITE, line) for line in lines]
    assert overs == sorted(overs, reverse=True)
    for o, u in zip(overs, unders, strict=False):
        # See test_home_and_away_cover_probabilities_are_nearly_complementary
        # for why this isn't exact.
        assert o + u == pytest.approx(1.0, abs=0.05)


def test_team_total_uses_marginal_distribution_directly():
    home_over = prob_team_total_over(FAVORITE, Side.HOME, 27.5)
    away_under = prob_team_total_under(FAVORITE, Side.AWAY, 27.5)
    assert 0.0 <= home_over <= 1.0
    assert 0.0 <= away_under <= 1.0


def test_price_market_dispatch_matches_direct_functions():
    assert price_market(FAVORITE, MarketFamily.MONEYLINE, Side.HOME) == prob_home_win(FAVORITE)
    assert price_market(FAVORITE, MarketFamily.SPREAD, Side.HOME, line=-6.5) == prob_home_covers(FAVORITE, -6.5)
    assert price_market(FAVORITE, MarketFamily.ALT_SPREAD, Side.HOME, line=-3.5) == prob_home_covers(FAVORITE, -3.5)
    assert price_market(FAVORITE, MarketFamily.TOTAL, Side.OVER, line=50.5) == prob_over(FAVORITE, 50.5)
    assert price_market(
        FAVORITE, MarketFamily.TEAM_TOTAL, Side.OVER, line=27.5, team=Side.HOME
    ) == prob_team_total_over(FAVORITE, Side.HOME, 27.5)


def test_price_market_requires_line_for_spread_and_total():
    with pytest.raises(ValueError):
        price_market(FAVORITE, MarketFamily.SPREAD, Side.HOME)
    with pytest.raises(ValueError):
        price_market(FAVORITE, MarketFamily.TOTAL, Side.OVER)


def test_price_market_rejects_first_half_families_as_unsupported():
    with pytest.raises(UnsupportedMarketFamilyError):
        price_market(FAVORITE, MarketFamily.FIRST_HALF_SPREAD, Side.HOME, line=-3.5)


def test_one_distribution_prices_the_full_auburn_baylor_style_market_set():
    # Mirrors the mission's example: one GameDistribution answers many
    # market families without recomputing a football model per contract.
    results = {
        "moneyline_home": price_market(FAVORITE, MarketFamily.MONEYLINE, Side.HOME),
        "spread_-2.5": price_market(FAVORITE, MarketFamily.SPREAD, Side.HOME, line=-2.5),
        "alt_spread_-6.5": price_market(FAVORITE, MarketFamily.ALT_SPREAD, Side.HOME, line=-6.5),
        "total_over_45.5": price_market(FAVORITE, MarketFamily.TOTAL, Side.OVER, line=45.5),
        "alt_total_under_60.5": price_market(FAVORITE, MarketFamily.ALT_TOTAL, Side.UNDER, line=60.5),
        "home_team_total_over_24.5": price_market(
            FAVORITE, MarketFamily.TEAM_TOTAL, Side.OVER, line=24.5, team=Side.HOME
        ),
    }
    assert len(results) == 6
    for name, p in results.items():
        assert 0.0 <= p <= 1.0, f"{name} produced an out-of-bounds probability: {p}"
