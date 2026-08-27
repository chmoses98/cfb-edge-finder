"""Mission sections 3-5, 9, 10, 23: the grouping hierarchy, exact
equivalence, and the refusal to infer equivalence from anything but
settlement semantics.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.expression.exposure import ExposureDirection, build_exposure, overlapping_exposures
from cfb_edge_finder.expression.taxonomy import (
    ContractSemantics,
    CorrelationClass,
    MarketDimension,
    classify_pair,
    truth_condition_key,
)
from cfb_edge_finder.schemas.common import MarketFamily, Side

GAME = "cfb-2026-wk01-a-at-b"
OTHER = "cfb-2026-wk01-c-at-d"


def ml(team, ticker="ML", game=GAME):
    return ContractSemantics(ticker, game, MarketFamily.MONEYLINE, team, None, None, None, "confirmed_live")


def spread(team, threshold, ticker="SP", game=GAME, op=">"):
    return ContractSemantics(ticker, game, MarketFamily.SPREAD, team, None, threshold, op, "confirmed_live")


def total(threshold, ticker="TOT", game=GAME, op=">", side=Side.OVER):
    return ContractSemantics(ticker, game, MarketFamily.TOTAL, None, side, threshold, op, "confirmed_live")


# --- Exact equivalence: the moneyline pair -------------------------------


def test_home_yes_and_away_no_are_the_same_event():
    """Settlement partitions every final score into exactly one winner,
    so 'home wins' and 'not away wins' are identical conditions."""
    home_yes = truth_condition_key(ml(Side.HOME, "H"), Side.YES)
    away_no = truth_condition_key(ml(Side.AWAY, "A"), Side.NO)
    assert home_yes == away_no
    assert classify_pair(ml(Side.HOME, "H"), ml(Side.AWAY, "A")) is CorrelationClass.EXACT_EQUIVALENT


def test_away_yes_and_home_no_are_the_same_event():
    assert truth_condition_key(ml(Side.AWAY, "A"), Side.YES) == truth_condition_key(ml(Side.HOME, "H"), Side.NO)


def test_the_two_winner_events_are_distinct():
    assert truth_condition_key(ml(Side.HOME), Side.YES) != truth_condition_key(ml(Side.AWAY), Side.YES)


def test_moneyline_equivalence_does_not_cross_games():
    assert truth_condition_key(ml(Side.HOME, "H", GAME), Side.YES) != truth_condition_key(
        ml(Side.HOME, "H2", OTHER), Side.YES
    )
    assert classify_pair(ml(Side.HOME, "H", GAME), ml(Side.AWAY, "A", OTHER)) is CorrelationClass.UNRELATED_GAME


# --- Non-equivalence --------------------------------------------------------


def test_winner_and_spread_are_not_equivalent():
    """A team can win by 1 and fail to cover -3.5. Same dimension family
    (both read the margin) but different events."""
    assert truth_condition_key(ml(Side.HOME), Side.YES) != truth_condition_key(spread(Side.HOME, 3.5), Side.YES)
    assert classify_pair(ml(Side.HOME), spread(Side.HOME, 3.5)) is CorrelationClass.SAME_MARGIN_DIMENSION_NESTED


def test_different_spread_rungs_are_not_equivalent():
    assert truth_condition_key(spread(Side.HOME, 3.5), Side.YES) != truth_condition_key(
        spread(Side.HOME, 7.5), Side.YES
    )
    assert classify_pair(spread(Side.HOME, 3.5, "a"), spread(Side.HOME, 7.5, "b")) is (
        CorrelationClass.SAME_MARGIN_DIMENSION_NESTED
    )


def test_cross_team_spreads_are_nested_not_equivalent():
    """'away margin > 1.5' is 'home margin < -1.5'; the complement of
    'home margin > 1.5' is 'home margin <= 1.5'. Not the same set, and
    deliberately not claimed as one."""
    home = truth_condition_key(spread(Side.HOME, 1.5, "h"), Side.NO)
    away = truth_condition_key(spread(Side.AWAY, 1.5, "a"), Side.YES)
    assert home != away
    assert classify_pair(spread(Side.HOME, 1.5, "h"), spread(Side.AWAY, 1.5, "a")) is (
        CorrelationClass.SAME_MARGIN_DIMENSION_NESTED
    )


def test_different_total_rungs_are_nested():
    assert classify_pair(total(45.5, "a"), total(52.5, "b")) is CorrelationClass.SAME_TOTAL_DIMENSION_NESTED


def test_winner_and_total_are_different_dimensions():
    assert classify_pair(ml(Side.HOME), total(45.5)) is CorrelationClass.SAME_GAME_DIFFERENT_DIMENSION


def test_spread_and_total_are_different_dimensions():
    assert classify_pair(spread(Side.HOME, 3.5), total(45.5)) is CorrelationClass.SAME_GAME_DIFFERENT_DIMENSION


# --- Same ticker, opposite sides are complements, not equivalents -------


def test_yes_and_no_of_one_ticker_are_complementary_events():
    contract = spread(Side.HOME, 3.5)
    assert truth_condition_key(contract, Side.YES) != truth_condition_key(contract, Side.NO)
    assert "home_margin>3.5" in truth_condition_key(contract, Side.YES)
    assert "home_margin<=3.5" in truth_condition_key(contract, Side.NO)


# --- Unresolved semantics are never equated (section 23) ----------------


@pytest.mark.parametrize(
    "bad",
    [
        ContractSemantics("X", GAME, MarketFamily.SPREAD, Side.HOME, None, None, ">"),          # no threshold
        ContractSemantics("X", GAME, MarketFamily.SPREAD, None, None, 3.5, ">"),                # no team
        ContractSemantics("X", GAME, MarketFamily.SPREAD, Side.HOME, None, 3.5, ">="),          # wrong operator
        ContractSemantics("X", GAME, MarketFamily.MONEYLINE, None, None, None, None),           # no team
        ContractSemantics("X", GAME, MarketFamily.TOTAL, None, None, 45.5, ">="),               # wrong operator
        ContractSemantics("X", GAME, MarketFamily.TOTAL, None, Side.UNDER, 45.5, ">"),          # unmodelled side
        ContractSemantics("X", GAME, None, None, None, None, None),                             # no family
    ],
)
def test_incomplete_semantics_never_produce_a_truth_condition(bad):
    assert bad.semantics_resolved is False
    assert truth_condition_key(bad, Side.YES) is None
    assert classify_pair(bad, ml(Side.HOME)) is CorrelationClass.EQUIVALENCE_UNRESOLVED


def test_equivalence_is_never_inferred_from_ticker_naming():
    """Two contracts with confusingly similar tickers but different
    thresholds must not be equated."""
    a = spread(Side.HOME, 3.5, "KXNCAAFSPREAD-XYZ-TEAM4")
    b = spread(Side.HOME, 4.5, "KXNCAAFSPREAD-XYZ-TEAM4B")
    assert classify_pair(a, b) is not CorrelationClass.EXACT_EQUIVALENT


def test_moneyline_and_spread_share_the_margin_dimension():
    """A moneyline IS a margin contract at threshold 0, so it belongs to
    the same thesis group as that team's spread rungs (mission section
    9). Treating the winner as its own dimension would hide that they
    move together."""
    assert ml(Side.HOME).dimension is MarketDimension.MARGIN
    assert spread(Side.HOME, 3.5).dimension is MarketDimension.MARGIN
    assert total(45.5).dimension is MarketDimension.TOTAL


def test_moneyline_key_is_expressed_in_margin_language():
    assert truth_condition_key(ml(Side.HOME), Side.YES).endswith("home_margin>0")
    assert truth_condition_key(ml(Side.AWAY), Side.YES).endswith("home_margin<=0")


def test_executable_side_must_be_yes_or_no():
    with pytest.raises(ValueError):
        truth_condition_key(ml(Side.HOME), Side.HOME)


# --- Exposure primitives (section 20) -----------------------------------


def test_no_on_a_team_is_exposure_to_the_opponent():
    home_no = build_exposure(ml(Side.HOME), Side.NO)
    assert home_no.team_exposure is Side.AWAY
    assert home_no.direction is ExposureDirection.TEAM_FAVORABLE


def test_the_four_overlapping_positions_are_recognised_as_one_game():
    """Team A ML YES, Team B ML NO, Team A -3.5 YES, Team A -7.5 YES."""
    exposures = [
        build_exposure(ml(Side.HOME, "H"), Side.YES),
        build_exposure(ml(Side.AWAY, "A"), Side.NO),
        build_exposure(spread(Side.HOME, 3.5, "S1"), Side.YES),
        build_exposure(spread(Side.HOME, 7.5, "S2"), Side.YES),
    ]
    grouped = overlapping_exposures(exposures)
    assert list(grouped) == [GAME]
    assert len(grouped[GAME]) == 4
    # All four lean the same way on the home team...
    assert {e.team_exposure for e in exposures} == {Side.HOME}
    # ...and the first two are literally the same event.
    assert exposures[0].equivalence_group_key == exposures[1].equivalence_group_key
    # ...while the spread rungs are distinct events.
    assert len({e.equivalence_group_key for e in exposures}) == 3


def test_totals_carry_no_team_lean():
    over = build_exposure(total(45.5), Side.YES)
    under = build_exposure(total(45.5), Side.NO)
    assert over.team_exposure is None and under.team_exposure is None
    assert over.direction is ExposureDirection.HIGHER_TOTAL
    assert under.direction is ExposureDirection.LOWER_TOTAL


def test_unresolved_semantics_have_no_equivalence_group():
    bad = ContractSemantics("X", GAME, MarketFamily.SPREAD, Side.HOME, None, None, ">")
    assert build_exposure(bad, Side.YES).equivalence_group_key is None
