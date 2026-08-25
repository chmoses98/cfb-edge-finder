"""Milestone D hardening pass, mission items 2/4/6: fee-schedule
calculation tests -- rounding, cents-vs-dollars unit safety, taker/maker
distinction, and provenance completeness/honesty.

*** WHY THESE AREN'T "OFFICIAL EXAMPLE" REPRODUCTIONS ***
Both official Feb-2026 sources
(https://kalshi.com/regulatory/fee-schedule,
kalshi-fee-schedule.pdf) were genuinely unreachable this session (see
fee_schedule.py's module docstring for the full evidence trail -- two
independent live attempts, both blocked by Kalshi's own bot-mitigation
checkpoint). These tests therefore verify the CALCULATION MACHINERY
itself is correct against hand-derived values from the documented
formula -- exact arithmetic, exact rounding, exact units -- not that the
formula matches Kalshi's current live table, which remains unverified.
"""

from __future__ import annotations

from decimal import Decimal

import pytest

from cfb_edge_finder.kalshi.fee_schedule import (
    LEGACY_UNVERIFIED_MAKER_SCHEDULE,
    LEGACY_UNVERIFIED_TAKER_SCHEDULE,
    calculate_fee_cents,
    calculate_fee_dollars,
)

# --- hand-derived reference values (0.07 x C x P x (1-P), round up to the
# next cent) at representative prices ---------------------------------


@pytest.mark.parametrize(
    "price_cents,expected_cents",
    [
        (10, 1),  # 0.07*0.10*0.90 = 0.0063 -> 0.63c -> 1c
        (25, 2),  # 0.07*0.25*0.75 = 0.013125 -> 1.3125c -> 2c
        (50, 2),  # 0.07*0.50*0.50 = 0.0175 -> 1.75c -> 2c
        (75, 2),  # symmetric with 25c
        (90, 1),  # symmetric with 10c
    ],
)
def test_taker_fee_at_representative_prices_single_contract(price_cents, expected_cents):
    assert calculate_fee_cents(price_cents, 1, LEGACY_UNVERIFIED_TAKER_SCHEDULE) == expected_cents


def test_taker_and_maker_fees_are_symmetric_around_50_cents():
    for price_cents in (10, 25, 40):
        taker_low = calculate_fee_cents(price_cents, 1, LEGACY_UNVERIFIED_TAKER_SCHEDULE)
        taker_high = calculate_fee_cents(100 - price_cents, 1, LEGACY_UNVERIFIED_TAKER_SCHEDULE)
        assert taker_low == taker_high


def test_maker_fee_is_one_quarter_the_taker_rate():
    # 0.0175 / 0.07 == 0.25 exactly -- the maker schedule's rate itself,
    # not a derived assumption.
    assert LEGACY_UNVERIFIED_MAKER_SCHEDULE.rate == LEGACY_UNVERIFIED_TAKER_SCHEDULE.rate * Decimal("0.25")


def test_maker_fee_at_50_cents_single_contract():
    # 0.0175*0.50*0.50 = 0.004375 -> 0.4375c -> round up to 1c
    assert calculate_fee_cents(50, 1, LEGACY_UNVERIFIED_MAKER_SCHEDULE) == 1


# --- multiple contracts ----------------------------------------------------


def test_fee_scales_with_contract_count_and_rounds_up_only_the_final_cent():
    # 0.07*10*0.25 = 0.175 dollars = 17.5c -> round up to 18c (NOT 10x the
    # single-contract 2c figure, which would wrongly round up 10 times).
    assert calculate_fee_cents(50, 10, LEGACY_UNVERIFIED_TAKER_SCHEDULE) == 18


def test_fee_scales_with_contract_count_exact_no_rounding_needed():
    # 0.07*100*0.25 = 1.75 dollars = 175c exactly -- no fractional cent,
    # so ROUND_CEILING must be a no-op here, not accidentally add a cent.
    assert calculate_fee_cents(50, 100, LEGACY_UNVERIFIED_TAKER_SCHEDULE) == 175


# --- rounding is always UP, never down or nearest --------------------------


def test_rounding_is_always_up_never_down_or_nearest():
    # 0.07*1*0.10*0.90 = 0.63c: nearest would round to 1c too, but at a
    # price where the fractional part is small this distinguishes UP
    # from DOWN unambiguously -- 0.63c must never floor to 0c.
    fee = calculate_fee_cents(10, 1, LEGACY_UNVERIFIED_TAKER_SCHEDULE)
    assert fee == 1
    assert fee != 0  # DOWN would give 0c -- structurally wrong, a free trade


# --- cents-vs-dollars unit safety ------------------------------------------


def test_calculate_fee_dollars_matches_cents_exactly():
    cents = calculate_fee_cents(50, 10, LEGACY_UNVERIFIED_TAKER_SCHEDULE)
    dollars = calculate_fee_dollars(50, 10, LEGACY_UNVERIFIED_TAKER_SCHEDULE)
    assert dollars == Decimal(cents) / Decimal(100)
    assert isinstance(dollars, Decimal)  # never a float -- see module docstring


def test_fee_dollars_never_a_float_type():
    result = calculate_fee_dollars(33, 1, LEGACY_UNVERIFIED_TAKER_SCHEDULE)
    assert not isinstance(result, float)


# --- guard rails: impossible prices/quantities ------------------------------


@pytest.mark.parametrize("bad_price", [0, 100, -5, 150])
def test_out_of_range_price_cents_raises(bad_price):
    with pytest.raises(ValueError):
        calculate_fee_cents(bad_price, 1, LEGACY_UNVERIFIED_TAKER_SCHEDULE)


@pytest.mark.parametrize("bad_contracts", [0, -1])
def test_non_positive_contracts_raises(bad_contracts):
    with pytest.raises(ValueError):
        calculate_fee_cents(50, bad_contracts, LEGACY_UNVERIFIED_TAKER_SCHEDULE)


# --- provenance: honest, never silently claims verification ---------------


def test_legacy_schedules_are_explicitly_marked_unverified():
    assert LEGACY_UNVERIFIED_TAKER_SCHEDULE.verified is False
    assert LEGACY_UNVERIFIED_MAKER_SCHEDULE.verified is False


def test_legacy_schedules_carry_the_verification_failure_evidence():
    for schedule in (LEGACY_UNVERIFIED_TAKER_SCHEDULE, LEGACY_UNVERIFIED_MAKER_SCHEDULE):
        assert "429" in schedule.notes
        assert "kalshi.com/regulatory/fee-schedule" in schedule.notes
        assert "kalshi-fee-schedule.pdf" in schedule.notes


def test_legacy_schedules_have_no_confirmed_effective_date():
    # A genuinely unverified schedule must not carry a fabricated
    # effective_date -- None is the honest value here, never a guess at
    # "Feb 5, 2026" (that date belongs to the PDF this session could
    # never actually read).
    assert LEGACY_UNVERIFIED_TAKER_SCHEDULE.effective_date is None
    assert LEGACY_UNVERIFIED_MAKER_SCHEDULE.effective_date is None


def test_fee_type_distinguishes_taker_from_maker():
    assert LEGACY_UNVERIFIED_TAKER_SCHEDULE.fee_type == "taker"
    assert LEGACY_UNVERIFIED_MAKER_SCHEDULE.fee_type == "maker"


def test_calculation_version_is_present_and_shared_across_schedules():
    assert LEGACY_UNVERIFIED_TAKER_SCHEDULE.calculation_version
    assert LEGACY_UNVERIFIED_TAKER_SCHEDULE.calculation_version == LEGACY_UNVERIFIED_MAKER_SCHEDULE.calculation_version
