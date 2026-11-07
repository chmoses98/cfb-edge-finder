"""Deterministic Kalshi price arithmetic for an analyst's probability (research_packet.pricing)."""

from __future__ import annotations

import inspect

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
    assert ceiling < low


def test_bet_up_to_is_monotone_in_the_lower_bound():
    values = [pr.bet_up_to_price(p / 100, SERIES) for p in range(5, 96, 5)]
    assert all(b >= a for a, b in zip(values, values[1:], strict=False))


def test_bet_up_to_refuses_degenerate_probabilities():
    assert pr.bet_up_to_price(0.0, SERIES) is None
    assert pr.bet_up_to_price(1.0, SERIES) is None


# ---- sizing is advisory and analyst-owned ----------------------------------------------------------------
def test_no_stake_is_ever_assigned_without_an_analyst_proposal():
    out = pr.evaluate(
        side="YES", ask=0.30, series_ticker=SERIES, probability_low=0.70, probability_point=0.75, probability_high=0.80
    )
    assert out["clears_at_current_ask"] is True
    stake = out["stake"]
    assert stake["status"] == "NOT_ASSIGNED" and stake["units"] is None and stake["assigned_by_tool"] is False
    assert stake["advisory"] is True


def test_the_tool_contains_no_edge_to_stake_rule():
    src = inspect.getsource(pr)
    for gone in ("STAKE_TIERS", "UNCALIBRATED_UNIT_CAP", "MAX_SIZABLE_RANGE_WIDTH", "stake_units"):
        assert gone not in src
    assert not hasattr(pr, "stake_units")


def test_a_proposed_stake_is_only_checked_against_mechanical_owner_limits():
    ok = pr.stake_check(2.0, 0.70, 0.55, quoted_contracts=100000, price=0.55)
    assert ok["status"] == "ADVISORY_PROPOSAL" and ok["units"] == 2.0 and ok["dollars"] == 150.0
    clamped = pr.stake_check(8.0, 0.70, 0.55, quoted_contracts=100000, price=0.55)
    assert clamped["units"] == pr.MAX_UNITS_PER_BET == 5.0 and clamped["status"] == "ADVISORY_PROPOSAL_ADJUSTED"
    assert "5U" in clamped["reasons"][0]


def test_a_lower_bound_below_break_even_gets_no_stake_whatever_is_proposed():
    out = pr.stake_check(3.0, 0.52, 0.55, quoted_contracts=100000, price=0.55)
    assert out["status"] == "NO_STAKE" and out["units"] == 0.0


def test_quoted_size_limits_a_proposed_stake():
    out = pr.stake_check(2.0, 0.70, 0.55, quoted_contracts=50, price=0.55)  # $27.50 of depth
    assert out["units"] == 0.0 and out["status"] == "NO_STAKE"
    part = pr.stake_check(3.0, 0.70, 0.55, quoted_contracts=300, price=0.55)  # $165 of depth = 2.2U
    assert part["units"] == 2.0 and "quoted size" in part["reasons"][0]


def test_unknown_fee_means_no_stake():
    assert pr.stake_check(1.0, 0.7, None)["status"] == "NO_STAKE"


def test_qualification_language_separates_an_opinion_from_validated_positive_ev():
    out = pr.evaluate(
        side="NO",
        ask=0.20,
        series_ticker=SERIES,
        probability_low=0.9,
        probability_point=0.92,
        probability_high=0.95,
        proposed_units=1,
    )
    assert out["qualified_positive_ev"] is False
    q = out["qualification"]
    assert q["tier"] == "ANALYST_RECOMMENDATION_WITH_STATED_UNCERTAINTY"
    assert q["empirically_validated_positive_ev"] is False
    assert "reliability table" in q["required_for_validated_positive_ev"]
    assert "unchanged" in q["automated_approval_gates"]


def test_inverted_range_is_rejected():
    with pytest.raises(ValueError):
        pr.evaluate(
            side="YES", ask=0.5, series_ticker=SERIES, probability_low=0.6, probability_point=0.5, probability_high=0.7
        )
