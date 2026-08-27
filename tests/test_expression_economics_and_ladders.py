"""Mission sections 5-8, 14, 17, 18: fee-aware break-even, dominance,
static inconsistency, and ladder coherence.
"""

from __future__ import annotations

import pytest

from cfb_edge_finder.expression.economics import (
    DOMINATED_FLAG,
    STATIC_INCONSISTENCY_FLAG,
    build_expression_economics,
    detect_static_inconsistency,
    estimate_entry_fee,
    find_dominated_expressions,
)
from cfb_edge_finder.expression.ladders import (
    Ladder,
    LadderAnomaly,
    LadderRung,
    analyze_ladder,
    check_market_coherence,
    check_model_monotonicity,
    check_model_tie_mass,
)
from cfb_edge_finder.expression.taxonomy import MarketDimension
from cfb_edge_finder.schemas.common import Side

SERIES = "KXNCAAFGAME"
GAME = "g1"


def econ(ticker, side, price, model_p=None):
    return build_expression_economics(
        market_ticker=ticker, executable_side=side, executable_price=price,
        model_probability_for_this_side=model_p, series_ticker=SERIES,
    )


# --- Fee-aware break-even (section 14) -----------------------------------


def test_break_even_is_the_all_in_cost_not_the_raw_price():
    """Payout is exactly $1, so break-even probability == price + fee.
    Using the raw price understates it by the entire fee."""
    e = econ("T", Side.YES, 0.40)
    assert e.estimated_fee is not None and e.estimated_fee > 0
    assert e.all_in_cost == pytest.approx(0.40 + e.estimated_fee)
    assert e.fee_adjusted_break_even_probability == pytest.approx(e.all_in_cost)
    assert e.fee_adjusted_break_even_probability > 0.40, "fee was ignored in break-even"


def test_research_probability_surplus_is_model_minus_break_even():
    e = econ("T", Side.YES, 0.40, model_p=0.55)
    assert e.research_probability_surplus == pytest.approx(0.55 - e.all_in_cost)


def test_yes_and_no_fees_are_computed_independently():
    """Real corpus quotes yes=0.75 alongside no=0.91 on one ticker, so
    the two sides genuinely have different fees."""
    yes = econ("T", Side.YES, 0.75)
    no = econ("T", Side.NO, 0.91)
    assert yes.estimated_fee != no.estimated_fee


def test_unknown_fee_yields_unknown_cost_never_a_silent_zero():
    for price in (0.0, 1.0):
        e = econ("T", Side.YES, price)
        assert e.estimated_fee is None
        assert e.all_in_cost is None, "a missing fee was treated as zero, understating the cost"
        assert e.priceable is False


def test_missing_price_is_unpriceable():
    e = econ("T", Side.YES, None)
    assert e.priceable is False and e.fee_adjusted_break_even_probability is None


def test_fee_is_only_defined_inside_the_tradeable_range():
    assert estimate_entry_fee(0.5, SERIES) is not None
    assert estimate_entry_fee(0.0, SERIES) is None
    assert estimate_entry_fee(1.0, SERIES) is None


# --- Dominance (section 17) ----------------------------------------------


def test_equivalent_event_with_unequal_prices_flags_the_dearer_one():
    """Same event, same $1 payout, one costs more all in."""
    cheap = econ("ARK", Side.YES, 0.37)
    dear = econ("UTAH", Side.NO, 0.91)
    findings = find_dominated_expressions("g|MARGIN|home_margin<=0", [cheap, dear])
    assert len(findings) == 1
    f = findings[0]
    assert f.flag == DOMINATED_FLAG
    assert f.cheaper_ticker == "ARK" and f.dominated_ticker == "UTAH"
    assert f.cost_difference == pytest.approx(dear.all_in_cost - cheap.all_in_cost)


def test_equal_cost_expressions_are_not_dominated():
    a = econ("A", Side.YES, 0.70)
    b = econ("B", Side.NO, 0.70)
    assert find_dominated_expressions("k", [a, b]) == []


def test_single_expression_group_has_no_dominance():
    assert find_dominated_expressions("k", [econ("A", Side.YES, 0.5)]) == []


def test_unpriceable_expressions_are_skipped_not_assumed_expensive():
    """A missing fee is missing information, not evidence of dominance."""
    priced = econ("A", Side.YES, 0.40)
    unpriceable = econ("B", Side.NO, None)
    findings = find_dominated_expressions("k", [priced, unpriceable])
    assert findings == []


def test_dominance_uses_all_in_cost_not_raw_price():
    """Two prices close enough that the fee decides the ordering."""
    a = econ("A", Side.YES, 0.50)   # max fee at 0.50
    b = econ("B", Side.NO, 0.505)
    findings = find_dominated_expressions("k", [a, b])
    for f in findings:
        assert f.cheaper_all_in_cost < f.dominated_all_in_cost


# --- Static inconsistency (section 19) -----------------------------------


def test_complementary_pair_below_one_dollar_is_flagged():
    """E and NOT-E jointly pay exactly $1 in every world, so a combined
    cost under $1 is a guaranteed shortfall."""
    finding = detect_static_inconsistency(
        game_id=GAME, dimension="MARGIN", event_key="e", complement_key="c",
        event_expressions=[econ("A", Side.YES, 0.30)],
        complement_expressions=[econ("B", Side.YES, 0.30)],
    )
    assert finding is not None
    assert finding.flag == STATIC_INCONSISTENCY_FLAG
    assert finding.combined_cost < 1.0
    assert finding.guaranteed_shortfall == pytest.approx(1.0 - finding.combined_cost)


def test_normal_wide_book_is_not_flagged():
    """The real corpus has yes+no summing to ~1.24; that is a spread, not
    an inconsistency."""
    assert detect_static_inconsistency(
        game_id=GAME, dimension="MARGIN", event_key="e", complement_key="c",
        event_expressions=[econ("A", Side.YES, 0.70)],
        complement_expressions=[econ("B", Side.YES, 0.54)],
    ) is None


def test_fees_are_included_so_a_marginal_pair_does_not_false_positive():
    """0.49 + 0.49 = 0.98 raw looks like an inconsistency, but fees at
    0.49 push the all-in total above $1."""
    raw_sum = 0.49 + 0.49
    assert raw_sum < 1.0
    finding = detect_static_inconsistency(
        game_id=GAME, dimension="MARGIN", event_key="e", complement_key="c",
        event_expressions=[econ("A", Side.YES, 0.49)],
        complement_expressions=[econ("B", Side.YES, 0.49)],
    )
    assert finding is None, "fees were not included in the guaranteed-payoff check"


def test_unpriceable_leg_makes_the_claim_unprovable():
    assert detect_static_inconsistency(
        game_id=GAME, dimension="MARGIN", event_key="e", complement_key="c",
        event_expressions=[econ("A", Side.YES, 0.30)],
        complement_expressions=[econ("B", Side.YES, None)],
    ) is None


# --- Ladders (sections 6, 7, 18) -----------------------------------------


def _ladder(rows, dimension=MarketDimension.MARGIN):
    ladder = Ladder(GAME, dimension, "g1|MARGIN|home")
    for ticker, threshold, model_p, yes, no in rows:
        ladder.rungs.append(LadderRung(ticker, threshold, model_p, yes, no, ">", "T_24H"))
    return ladder


def test_monotonic_model_ladder_is_clean():
    ladder = _ladder([("a", 1.5, 0.80, 0.80, 0.22), ("b", 7.5, 0.65, 0.66, 0.36), ("c", 13.5, 0.50, 0.51, 0.51)])
    assert check_model_monotonicity(ladder) == []
    assert check_market_coherence(ladder) == []


def test_model_probability_rising_with_threshold_is_a_violation():
    """The harder event is a strict subset, so its probability cannot be
    larger. That is a contradiction, not a judgement call."""
    ladder = _ladder([("a", 3.5, 0.50, 0.5, 0.5), ("b", 7.5, 0.62, 0.5, 0.5)])
    findings = check_model_monotonicity(ladder)
    assert len(findings) == 1
    assert findings[0].anomaly is LadderAnomaly.MODEL_MONOTONICITY_VIOLATION
    assert findings[0].magnitude == pytest.approx(0.12)


def test_equal_adjacent_model_probabilities_are_not_a_violation():
    ladder = _ladder([("a", 3.5, 0.50, 0.5, 0.5), ("b", 7.5, 0.50, 0.5, 0.5)])
    assert check_model_monotonicity(ladder) == []


def test_harder_rung_quoted_more_expensively_is_incoherent():
    ladder = _ladder([("a", 11.5, 0.40, 0.41, 0.89), ("b", 13.5, 0.35, 0.44, 0.90)])
    findings = check_market_coherence(ladder)
    assert len(findings) == 1
    assert findings[0].anomaly is LadderAnomaly.MARKET_LADDER_INCOHERENCE
    assert findings[0].magnitude == pytest.approx(0.03)


def test_total_ladder_uses_the_same_ordering_rule():
    ladder = _ladder(
        [("a", 38.5, 0.80, 0.86, 0.30), ("b", 41.5, 0.75, 0.92, 0.40)], dimension=MarketDimension.TOTAL
    )
    assert len(check_market_coherence(ladder)) == 1


def test_ladders_are_sorted_by_threshold_not_insertion_order():
    ladder = _ladder([("late", 13.5, 0.35, 0.30, 0.7), ("early", 3.5, 0.60, 0.61, 0.4)])
    assert [r.threshold for r in ladder.sorted_rungs()] == [3.5, 13.5]
    assert check_model_monotonicity(ladder) == []


def test_duplicate_threshold_is_reported():
    ladder = _ladder([("a", 3.5, 0.5, 0.5, 0.5), ("b", 3.5, 0.5, 0.5, 0.5)])
    assert any(f.anomaly is LadderAnomaly.DUPLICATE_THRESHOLD for f in analyze_ladder(ladder))


def test_mixed_operators_make_rungs_incomparable():
    ladder = _ladder([("a", 3.5, 0.5, 0.5, 0.5)])
    ladder.rungs.append(LadderRung("b", 7.5, 0.4, 0.4, 0.6, ">=", "T_24H"))
    assert any(f.anomaly is LadderAnomaly.INCONSISTENT_SEMANTIC_OPERATOR for f in analyze_ladder(ladder))


def test_impossible_threshold_is_reported():
    ladder = _ladder([("a", 999.0, 0.5, 0.5, 0.5)])
    assert any(f.anomaly is LadderAnomaly.IMPOSSIBLE_THRESHOLD for f in analyze_ladder(ladder))


def test_negative_total_threshold_is_impossible():
    ladder = _ladder([("a", -5.0, 0.5, 0.5, 0.5)], dimension=MarketDimension.TOTAL)
    assert any(f.anomaly is LadderAnomaly.IMPOSSIBLE_THRESHOLD for f in analyze_ladder(ladder))


def test_rungs_missing_a_price_are_skipped_not_treated_as_zero():
    ladder = _ladder([("a", 3.5, 0.6, None, None), ("b", 7.5, 0.5, 0.4, 0.6)])
    assert check_market_coherence(ladder) == []


# --- Model tie mass (section 12 diagnostic) ------------------------------


def test_winner_probabilities_not_summing_to_one_is_reported():
    """Settlement makes the two winner events exhaustive, so a shortfall
    is simulated tie mass in the model."""
    finding = check_model_tie_mass(game_id=GAME, home_model_probability=0.8134, away_model_probability=0.1718)
    assert finding is not None
    assert finding.anomaly is LadderAnomaly.MODEL_TIE_MASS
    assert finding.magnitude == pytest.approx(1.0 - (0.8134 + 0.1718))


def test_complementary_winner_probabilities_are_clean():
    assert check_model_tie_mass(game_id=GAME, home_model_probability=0.75, away_model_probability=0.25) is None


def test_tie_mass_needs_both_sides():
    assert check_model_tie_mass(game_id=GAME, home_model_probability=0.75, away_model_probability=None) is None
