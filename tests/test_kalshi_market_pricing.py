"""market_pricing.py: verifies the Kalshi-team-named ">" grammar is
converted to price_market's signed home_line convention correctly for
BOTH the home-named and away-named cases (mission section 8's spread
sign-convention derivation), and exercises TOTAL/MONEYLINE dispatch."""

from __future__ import annotations

from cfb_edge_finder.kalshi.contract_semantics import ParsedContract
from cfb_edge_finder.kalshi.market_pricing import price_parsed_contract
from cfb_edge_finder.projections.distribution import prob_away_covers, prob_home_covers, prob_over
from cfb_edge_finder.schemas.common import MarketFamily, Side
from cfb_edge_finder.schemas.projection import GameDistribution

DIST = GameDistribution(home_mean=28.0, away_mean=21.0, home_sd=10.0, away_sd=10.0, correlation=0.0)


def _spread_contract(line: float) -> ParsedContract:
    return ParsedContract(reason=None, detail="test", market_family=MarketFamily.SPREAD, line=line, operator=">")


def _total_contract(line: float, side: Side = Side.OVER) -> ParsedContract:
    return ParsedContract(
        reason=None, detail="test", market_family=MarketFamily.TOTAL, side=side, line=line, operator=">"
    )


def _moneyline_contract() -> ParsedContract:
    return ParsedContract(reason=None, detail="test", market_family=MarketFamily.MONEYLINE)


def test_spread_home_named_team_matches_prob_home_covers():
    threshold = 3.5
    result = price_parsed_contract(_spread_contract(threshold), DIST, named_team_side=Side.HOME)
    expected = prob_home_covers(DIST, home_line=-threshold)
    assert result.model_probability == expected


def test_spread_away_named_team_matches_prob_away_covers():
    threshold = 3.5
    result = price_parsed_contract(_spread_contract(threshold), DIST, named_team_side=Side.AWAY)
    expected = prob_away_covers(DIST, home_line=threshold)
    assert result.model_probability == expected


def test_spread_home_and_away_named_are_not_accidentally_identical():
    threshold = 3.5
    home_result = price_parsed_contract(_spread_contract(threshold), DIST, named_team_side=Side.HOME)
    away_result = price_parsed_contract(_spread_contract(threshold), DIST, named_team_side=Side.AWAY)
    assert home_result.model_probability != away_result.model_probability


def test_total_matches_prob_over_directly():
    result = price_parsed_contract(_total_contract(45.5), DIST, named_team_side=None)
    assert result.model_probability == prob_over(DIST, 45.5)


def test_moneyline_requires_named_team_side():
    result = price_parsed_contract(_moneyline_contract(), DIST, named_team_side=None)
    assert result.model_probability is None
    assert result.error == "unresolved_side"


def test_moneyline_prices_with_named_team_side():
    result = price_parsed_contract(_moneyline_contract(), DIST, named_team_side=Side.HOME)
    assert result.model_probability is not None
    assert 0.0 <= result.model_probability <= 1.0


def test_already_failed_parse_short_circuits():
    from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason

    failed = ParsedContract(reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED, detail="bad title")
    result = price_parsed_contract(failed, DIST, named_team_side=None)
    assert result.model_probability is None
    assert result.error == KalshiCfbCoverageReason.PARSE_UNRESOLVED.value


def test_spread_missing_named_side_fails_explicitly():
    result = price_parsed_contract(_spread_contract(3.5), DIST, named_team_side=None)
    assert result.model_probability is None
    assert result.error == "unresolved_side"
