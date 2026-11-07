"""Deterministic Kalshi price arithmetic for an analyst's probability (research_packet.pricing)."""

from __future__ import annotations

import pytest

from cfb_edge_finder.expression.fair_value import fee_inclusive_break_even
from cfb_edge_finder.research_packet import pricing as pr

SERIES = "KXNCAAFSPREAD"


def test_all_in_cost_is_ask_plus_order_fee_over_contracts():
    view = pr.view_side("YES", 0.29, SERIES)
    assert view.contracts == int(75 // 0.29)
    assert view.order_fee == pr.order_fee(0.29, view.contracts, SERIES)
    assert view.all_in_per_contract == pytest.approx(0.29 + view.order_fee / view.contracts, abs=1e-6)
    assert view.all_in_per_contract > 0.29


def test_break_even_matches_the_existing_fee_machinery():
    be = pr.break_even_probability(0.55, 100, SERIES)
    assert be == pytest.approx(fee_inclusive_break_even(0.55, 100, SERIES), abs=1e-6)


def test_no_side_is_priced_at_the_no_ask_never_one_minus_yes_ask():
    # a wide book: yes_ask 0.90, yes_bid 0.70 -> no_ask 0.30, but 1 - yes_ask = 0.10
    no = pr.view_side("NO", 0.30, SERIES)
    wrong = pr.view_side("NO", 1 - 0.90, SERIES)
    assert no.all_in_per_contract > wrong.all_in_per_contract + 0.15


def test_unknown_or_unusable_price_is_none_never_free():
    for ask in (None, 0.0, 1.0, 1.2):
        v = pr.view_side("YES", ask, SERIES)
        assert v.contracts == 0 and v.order_fee is None and v.all_in_per_contract is None


def test_bet_up_to_is_the_highest_cent_whose_break_even_is_covered():
    low = 0.58
    ceiling = pr.bet_up_to_price(low, SERIES)
    assert ceiling is not None
    c = pr.contracts_for_dollars(ceiling, pr.UNIT_DOLLARS)
    assert pr.break_even_probability(ceiling, c, SERIES) <= low
    nxt = round(ceiling + 0.01, 2)
    assert pr.break_even_probability(nxt, pr.contracts_for_dollars(nxt, pr.UNIT_DOLLARS), SERIES) > low
    assert ceiling < low  # fees mean the ceiling sits below the probability


def test_bet_up_to_is_monotone_in_the_lower_bound():
    values = [pr.bet_up_to_price(p / 100, SERIES) for p in range(5, 96, 5)]
    assert all(b >= a for a, b in zip(values, values[1:]))


def test_bet_up_to_refuses_degenerate_probabilities():
    assert pr.bet_up_to_price(0.0, SERIES) is None
    assert pr.bet_up_to_price(1.0, SERIES) is None


def test_stake_zero_when_lower_bound_does_not_clear_break_even():
    out = pr.evaluate(side="YES", ask=0.60, series_ticker=SERIES, probability_low=0.55, probability_point=0.62, probability_high=0.68)
    assert out["clears_at_current_ask"] is False
    assert out["stake"]["units"] == 0.0


def test_stake_is_capped_at_the_owner_ceiling_and_lower_while_uncalibrated():
    out = pr.evaluate(side="YES", ask=0.30, series_ticker=SERIES, probability_low=0.70, probability_point=0.75, probability_high=0.80)
    assert out["clears_at_current_ask"] is True
    assert out["stake"]["units"] <= pr.UNCALIBRATED_UNIT_CAP <= pr.MAX_UNITS_PER_BET
    assert out["stake"]["dollars"] == out["stake"]["units"] * pr.UNIT_DOLLARS
    calibrated = pr.stake_units(0.80, 0.85, 0.30, calibrated=True)
    assert calibrated["units"] == pr.MAX_UNITS_PER_BET


def test_a_very_wide_range_is_not_sized():
    s = pr.stake_units(0.30, 0.80, 0.25)
    assert s["units"] == 0.0 and "too uncertain" in s["reasons"][0]


def test_quoted_size_limits_the_stake():
    s = pr.stake_units(0.70, 0.74, 0.55, quoted_contracts=50, price=0.55)  # $27.50 of depth
    assert s["units"] == 0.0 and "quoted size" in " ".join(s["reasons"])


def test_a_stated_range_never_qualifies_as_positive_ev():
    out = pr.evaluate(side="NO", ask=0.20, series_ticker=SERIES, probability_low=0.9, probability_point=0.92, probability_high=0.95)
    assert out["qualified_positive_ev"] is False


def test_inverted_range_is_rejected():
    with pytest.raises(ValueError):
        pr.evaluate(side="YES", ask=0.5, series_ticker=SERIES, probability_low=0.6, probability_point=0.5, probability_high=0.7)
