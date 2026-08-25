"""Milestone D closure, items 3/5/6: fee-schedule calculation tests
against the CURRENT VERIFIED July 2026 Kalshi schedule -- rounding,
cents-vs-dollars unit safety, multiplier handling, and provenance.

*** SOURCE OF THE "OFFICIAL" VALUES BELOW ***
The user supplied this schedule's content directly (document: "Fee
Schedule for July 2026 -- 7.7.26 Update", effective 2026-07-07, from
https://kalshi.com/docs/kalshi-fee-schedule.pdf), after this repo's own
prior live-fetch attempts (both plain HTTP and a warmed-up headless
browser) were genuinely blocked by Kalshi's bot-mitigation checkpoint --
see fee_schedule.py's module docstring for that history. These tests
verify the calculation machinery reproduces that formula's own hand-
derived values exactly, at the price points the closure mission
specifies (10/25/50/75/90 cents, at 1 and 100 contracts)."""

from __future__ import annotations

from decimal import Decimal

import pytest

from cfb_edge_finder.kalshi.fee_schedule import (
    CFB_SERIES_MULTIPLIERS,
    DEFAULT_MULTIPLIER,
    KALSHI_FEE_SCHEDULE_2026_07_07_MAKER,
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_cents,
    calculate_fee_dollars,
    get_taker_multiplier,
)

# --- official table reproduction: 1 contract, taker ------------------------


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
def test_taker_fee_one_contract_official_table(price_cents, expected_cents):
    assert calculate_fee_cents(price_cents, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER) == expected_cents


# --- official table reproduction: 100 contracts, taker ----------------------


@pytest.mark.parametrize(
    "price_cents,expected_cents",
    [
        (10, 63),  # 0.07*100*0.09 = 0.63 dollars = 63c exactly
        (25, 132),  # 0.07*100*0.1875 = 1.3125 dollars = 131.25c -> 132c
        (50, 175),  # 0.07*100*0.25 = 1.75 dollars = 175c exactly
        (75, 132),  # symmetric with 25c
        (90, 63),  # symmetric with 10c
    ],
)
def test_taker_fee_100_contracts_official_table(price_cents, expected_cents):
    assert calculate_fee_cents(price_cents, 100, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER) == expected_cents


def test_taker_fee_symmetric_around_50_cents():
    for price_cents in (10, 25, 40):
        for contracts in (1, 100):
            low = calculate_fee_cents(price_cents, contracts, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)
            high = calculate_fee_cents(100 - price_cents, contracts, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)
            assert low == high


# --- maker schedule -----------------------------------------------------


def test_maker_rate_is_one_quarter_the_taker_rate():
    assert KALSHI_FEE_SCHEDULE_2026_07_07_MAKER.rate == KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.rate * Decimal("0.25")


def test_maker_fee_at_50_cents_single_contract():
    # 0.0175*0.50*0.50 = 0.004375 -> 0.4375c -> round up to 1c
    assert calculate_fee_cents(50, 1, KALSHI_FEE_SCHEDULE_2026_07_07_MAKER) == 1


# --- rounding is always UP, never down or nearest --------------------------


def test_rounding_is_always_up_never_down_or_nearest():
    fee = calculate_fee_cents(10, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)
    assert fee == 1
    assert fee != 0  # DOWN would give 0c -- structurally wrong, a free trade


def test_exact_cent_amount_is_not_over_rounded():
    # 175c exactly -- ROUND_CEILING on an exact value must be a no-op.
    assert calculate_fee_cents(50, 100, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER) == 175


# --- cents-vs-dollars unit safety ------------------------------------------


def test_calculate_fee_dollars_matches_cents_exactly():
    cents = calculate_fee_cents(50, 10, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)
    dollars = calculate_fee_dollars(50, 10, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)
    assert dollars == Decimal(cents) / Decimal(100)
    assert isinstance(dollars, Decimal)


def test_fee_dollars_never_a_float_type():
    result = calculate_fee_dollars(33, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)
    assert not isinstance(result, float)


# --- guard rails: impossible prices/quantities ------------------------------


@pytest.mark.parametrize("bad_price", [0, 100, -5, 150])
def test_out_of_range_price_cents_raises(bad_price):
    with pytest.raises(ValueError):
        calculate_fee_cents(bad_price, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)


@pytest.mark.parametrize("bad_contracts", [0, -1])
def test_non_positive_contracts_raises(bad_contracts):
    with pytest.raises(ValueError):
        calculate_fee_cents(50, bad_contracts, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)


# --- multiplier handling (mission closure item 6) ---------------------------


def test_kxncaafgame_multiplier_is_one_and_listed():
    entry = CFB_SERIES_MULTIPLIERS["KXNCAAFGAME"]
    assert entry.taker_multiplier == Decimal(1)
    assert entry.maker_multiplier == Decimal(1)
    assert entry.listed_as_non_standard is True


def test_kxncaafspread_and_kxncaaftotal_absent_from_non_standard_table():
    assert "KXNCAAFSPREAD" not in CFB_SERIES_MULTIPLIERS
    assert "KXNCAAFTOTAL" not in CFB_SERIES_MULTIPLIERS


def test_get_taker_multiplier_for_listed_series():
    multiplier, evidence = get_taker_multiplier("KXNCAAFGAME")
    assert multiplier == Decimal(1)
    assert "non-standard-fee table" in evidence


def test_get_taker_multiplier_falls_back_to_default_for_unlisted_series():
    for series in ("KXNCAAFSPREAD", "KXNCAAFTOTAL"):
        multiplier, evidence = get_taker_multiplier(series)
        assert multiplier == DEFAULT_MULTIPLIER
        assert series in evidence
        assert "default" in evidence.lower()


def test_multiplier_scales_the_fee_linearly():
    base = calculate_fee_cents(50, 100, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, multiplier=Decimal(1))
    doubled = calculate_fee_cents(50, 100, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, multiplier=Decimal(2))
    assert doubled == base * 2


def test_all_current_cfb_series_produce_identical_fees_at_default_multiplier():
    # KXNCAAFGAME's LISTED multiplier (1) and KXNCAAFSPREAD/TOTAL's
    # FALLBACK default multiplier (1) are numerically identical -- so
    # today, all three CFB series produce the same fee amount, even
    # though only one is explicitly listed in the schedule's table.
    for series in ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL"):
        multiplier, _evidence = get_taker_multiplier(series)
        assert calculate_fee_cents(50, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, multiplier) == 2


# --- provenance: verified, not legacy/unverified ---------------------------


def test_schedules_are_explicitly_marked_verified_current():
    assert KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.verification_status == "VERIFIED_CURRENT"
    assert KALSHI_FEE_SCHEDULE_2026_07_07_MAKER.verification_status == "VERIFIED_CURRENT"


def test_schedules_carry_the_correct_effective_date():
    assert KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.effective_date == "2026-07-07"
    assert KALSHI_FEE_SCHEDULE_2026_07_07_MAKER.effective_date == "2026-07-07"


def test_schedules_carry_the_official_source_document():
    for schedule in (KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, KALSHI_FEE_SCHEDULE_2026_07_07_MAKER):
        assert "kalshi-fee-schedule.pdf" in schedule.source
        assert "7.7.26" in schedule.source


def test_fee_type_distinguishes_taker_from_maker():
    assert KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.fee_type == "taker"
    assert KALSHI_FEE_SCHEDULE_2026_07_07_MAKER.fee_type == "maker"


def test_calculation_version_is_present_and_shared_across_schedules():
    assert KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.calculation_version
    assert (
        KALSHI_FEE_SCHEDULE_2026_07_07_TAKER.calculation_version
        == KALSHI_FEE_SCHEDULE_2026_07_07_MAKER.calculation_version
    )


def test_legacy_unverified_constants_no_longer_exist():
    import cfb_edge_finder.kalshi.fee_schedule as fee_schedule_module

    assert not hasattr(fee_schedule_module, "LEGACY_UNVERIFIED_TAKER_SCHEDULE")
    assert not hasattr(fee_schedule_module, "LEGACY_UNVERIFIED_MAKER_SCHEDULE")
