"""Boundary and property tests for the disconnected sizing arithmetic.

The point of testing this now, while it is wired to nothing, is that a
fee ceiling and a Kelly denominator are both easy to get subtly wrong and
impossible to notice later.
"""

from __future__ import annotations

import math
from decimal import Decimal

import pytest

from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_cents,
)
from cfb_edge_finder.sizing.kelly_math import (
    MAX_TRADEABLE_PRICE_CENTS,
    MIN_TRADEABLE_PRICE_CENTS,
    PAYOUT_CENTS,
    SizingDomainError,
    effective_cost_per_contract,
    expected_value_cents,
    fee_adjusted_break_even,
    full_kelly_fraction,
    order_cost_cents,
    scaled_kelly_fraction,
    size_position,
    taker_fee_cents,
    validate_probability,
)

ALL_PRICES = range(MIN_TRADEABLE_PRICE_CENTS, MAX_TRADEABLE_PRICE_CENTS + 1)


# --------------------------------------------------------------- fees


def test_fee_matches_the_verified_schedule_at_every_tradeable_price():
    """Delegation, proven -- not a second implementation that happens to
    agree today."""
    for price in ALL_PRICES:
        for count in (1, 2, 7, 100, 1_000):
            assert taker_fee_cents(contract_count=count, price_cents=price) == calculate_fee_cents(
                price, count, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER
            )


def test_fee_is_a_true_ceiling_not_a_float_rounding():
    """1 contract at 50c is exactly 1.75c, which must round UP to 2.
    A float pipeline can land on 1.7499999 and answer 1."""
    assert taker_fee_cents(contract_count=1, price_cents=50) == 2
    assert taker_fee_cents(contract_count=4, price_cents=50) == 7  # exactly 7.00
    assert taker_fee_cents(contract_count=100, price_cents=50) == 175


def test_fee_peaks_around_fifty_cents():
    """P(1-P) peaks at 0.50, but the whole-cent ceiling flattens the top:
    at 1000 contracts, 49c, 50c and 51c all round to the same 1750c. The
    test asserts the tie rather than a single winner, because asserting
    'the max is 50' would fail on an implementation that is correct."""
    fees = {p: taker_fee_cents(contract_count=1000, price_cents=p) for p in ALL_PRICES}
    peak = max(fees.values())
    assert {p for p, f in fees.items() if f == peak} == {49, 50, 51}
    assert fees[50] == peak


def test_fee_is_symmetric_around_fifty():
    for price in ALL_PRICES:
        assert taker_fee_cents(contract_count=1000, price_cents=price) == taker_fee_cents(
            contract_count=1000, price_cents=100 - price
        )


def test_fee_is_zero_for_zero_contracts():
    assert taker_fee_cents(contract_count=0, price_cents=50) == 0


@pytest.mark.parametrize("price", [0, 100, -1, 101, 1000])
def test_untradeable_prices_raise_rather_than_extrapolate(price):
    with pytest.raises(SizingDomainError):
        taker_fee_cents(contract_count=1, price_cents=price)


@pytest.mark.parametrize("bad", [1.5, "50", None, True])
def test_price_must_be_a_real_int(bad):
    with pytest.raises(SizingDomainError):
        taker_fee_cents(contract_count=1, price_cents=bad)


def test_negative_contract_count_raises():
    with pytest.raises(SizingDomainError):
        taker_fee_cents(contract_count=-1, price_cents=50)


# ------------------------------------------------------------ costs


def test_effective_cost_per_contract_falls_as_the_order_grows():
    """The fee ceiling is amortised, so a bigger order is cheaper per
    contract. Sizing that assumes a fixed per-contract fee is wrong at
    exactly the small counts where it matters."""
    costs = [
        effective_cost_per_contract(contract_count=n, price_cents=50) for n in (1, 2, 10, 100, 10_000)
    ]
    assert costs == sorted(costs, reverse=True)
    assert costs[0] == Decimal(52)
    assert costs[-1] < Decimal("51.76")


def test_effective_cost_never_below_the_price_itself():
    for price in ALL_PRICES:
        for count in (1, 3, 500):
            assert effective_cost_per_contract(contract_count=count, price_cents=price) >= Decimal(price)


def test_zero_contract_effective_cost_is_undefined_not_zero():
    with pytest.raises(SizingDomainError):
        effective_cost_per_contract(contract_count=0, price_cents=50)


def test_order_cost_is_contracts_plus_whole_order_fee():
    for price in (1, 33, 50, 99):
        for count in (1, 9, 250):
            assert order_cost_cents(contract_count=count, price_cents=price) == count * price + taker_fee_cents(
                contract_count=count, price_cents=price
            )


def test_break_even_is_cost_over_payout():
    for price in ALL_PRICES:
        be = fee_adjusted_break_even(contract_count=10, price_cents=price)
        assert be == effective_cost_per_contract(contract_count=10, price_cents=price) / Decimal(PAYOUT_CENTS)
        assert be > Decimal(price) / Decimal(100)


def test_worst_case_break_even_reaches_exactly_one():
    """A single contract at 99c costs 99c plus a 1c fee: the all-in cost
    is the entire $1 payout, so the break-even is exactly 1.00 and the
    position cannot profit at any probability. Nothing rounds it back
    down to a comfortable-looking 0.99."""
    assert fee_adjusted_break_even(contract_count=1, price_cents=99) == Decimal(1)
    assert full_kelly_fraction(probability=Decimal(1), all_in_cost_cents=100) == 0


def test_break_even_never_exceeds_one_at_any_tradeable_price():
    """Checked rather than assumed: the fee is small enough that all-in
    cost tops out at the payout, so a break-even above 1 is unreachable
    on a single contract."""
    for price in ALL_PRICES:
        assert fee_adjusted_break_even(contract_count=1, price_cents=price) <= Decimal(1)


# ------------------------------------------------------------ kelly


def test_full_kelly_matches_the_closed_form():
    for price_cost in (10, 25, 52, 80, 95):
        for p in ("0.05", "0.30", "0.5", "0.75", "0.99"):
            probability = Decimal(p)
            cost = Decimal(price_cost) / Decimal(100)
            expected = max(Decimal(0), (probability - cost) / (Decimal(1) - cost))
            got = full_kelly_fraction(probability=probability, all_in_cost_cents=price_cost)
            assert math.isclose(float(got), float(expected), rel_tol=1e-12, abs_tol=1e-12)


def test_kelly_is_zero_not_negative_when_the_edge_is_gone():
    """Kelly's answer to a bad bet is 'no bet', never a negative stake
    that a caller could read as a position on the other side."""
    assert full_kelly_fraction(probability=Decimal("0.30"), all_in_cost_cents=52) == 0
    assert full_kelly_fraction(probability=Decimal("0.52"), all_in_cost_cents=52) == 0


def test_kelly_is_zero_when_cost_reaches_or_exceeds_the_payout():
    assert full_kelly_fraction(probability=Decimal("0.99"), all_in_cost_cents=100) == 0
    assert full_kelly_fraction(probability=Decimal("0.99"), all_in_cost_cents=150) == 0


def test_kelly_is_monotone_increasing_in_probability():
    previous = Decimal(-1)
    for step in range(0, 101):
        value = full_kelly_fraction(probability=Decimal(step) / Decimal(100), all_in_cost_cents=40)
        assert value >= previous
        previous = value


def test_kelly_never_exceeds_one():
    for price in ALL_PRICES:
        assert full_kelly_fraction(probability=Decimal(1), all_in_cost_cents=price) <= Decimal(1)


def test_certain_win_gives_full_bankroll_at_full_kelly():
    assert full_kelly_fraction(probability=Decimal(1), all_in_cost_cents=50) == Decimal(1)


def test_zero_cost_raises_rather_than_dividing():
    with pytest.raises(SizingDomainError):
        full_kelly_fraction(probability=Decimal("0.6"), all_in_cost_cents=0)


@pytest.mark.parametrize("bad", ["-0.1", "1.1", "2"])
def test_probability_outside_the_unit_interval_raises(bad):
    with pytest.raises(SizingDomainError):
        validate_probability(Decimal(bad))


def test_floats_are_refused_everywhere_money_is_involved():
    with pytest.raises(SizingDomainError):
        full_kelly_fraction(probability=0.6, all_in_cost_cents=52)
    with pytest.raises(SizingDomainError):
        full_kelly_fraction(probability=Decimal("0.6"), all_in_cost_cents=52.0)


def test_scaled_kelly_requires_an_explicit_multiplier():
    """There is no default fraction of Kelly. Omitting it must be a
    TypeError, not a house policy nobody chose."""
    with pytest.raises(TypeError):
        scaled_kelly_fraction(probability=Decimal("0.6"), all_in_cost_cents=52)


def test_scaled_kelly_scales_linearly():
    full = full_kelly_fraction(probability=Decimal("0.7"), all_in_cost_cents=52)
    half = scaled_kelly_fraction(
        probability=Decimal("0.7"), all_in_cost_cents=52, kelly_multiplier=Decimal("0.5")
    )
    assert half == full / 2


@pytest.mark.parametrize("bad", ["-0.1", "1.5"])
def test_kelly_multiplier_outside_zero_to_one_raises(bad):
    with pytest.raises(SizingDomainError):
        scaled_kelly_fraction(
            probability=Decimal("0.7"), all_in_cost_cents=52, kelly_multiplier=Decimal(bad)
        )


# ------------------------------------------------------------- EV


def test_expected_value_is_negative_at_the_break_even_probability_minus_a_hair():
    ev = expected_value_cents(probability=Decimal("0.51"), contract_count=1, price_cents=50)
    assert ev < 0


def test_expected_value_zero_contracts_is_zero():
    assert expected_value_cents(probability=Decimal("0.9"), contract_count=0, price_cents=50) == 0


def test_expected_value_sign_flips_exactly_at_break_even():
    be = fee_adjusted_break_even(contract_count=100, price_cents=50)
    assert expected_value_cents(probability=be, contract_count=100, price_cents=50) == 0
    assert expected_value_cents(
        probability=be + Decimal("0.001"), contract_count=100, price_cents=50
    ) > 0


# --------------------------------------------------------- sizing


def test_size_position_requires_every_knob():
    with pytest.raises(TypeError):
        size_position(probability=Decimal("0.6"), price_cents=50, bankroll_cents=10_000)


def test_size_position_never_spends_more_than_the_budget():
    """The all-in cost, fee included, must fit. Dividing the target by
    the price alone overshoots on small orders."""
    for cap in (100, 466, 467, 500, 5_000, 100_000):
        result = size_position(
            probability=Decimal("0.9"),
            price_cents=50,
            bankroll_cents=10_000_000,
            kelly_multiplier=Decimal(1),
            max_position_cents=cap,
        )
        assert result.order_cost_cents <= cap


def test_size_position_takes_the_largest_count_that_fits():
    result = size_position(
        probability=Decimal("0.9"),
        price_cents=50,
        bankroll_cents=10_000_000,
        kelly_multiplier=Decimal(1),
        max_position_cents=500,
    )
    assert result.contract_count == 9
    assert result.order_cost_cents == 466
    assert order_cost_cents(contract_count=10, price_cents=50) > 500


def test_no_edge_produces_no_position():
    result = size_position(
        probability=Decimal("0.20"),
        price_cents=50,
        bankroll_cents=100_000,
        kelly_multiplier=Decimal("0.25"),
        max_position_cents=100_000,
    )
    assert result.is_zero
    assert result.binding_constraint == "NO_POSITIVE_EDGE"
    assert result.order_cost_cents == 0


def test_budget_below_one_contract_produces_no_position():
    result = size_position(
        probability=Decimal("0.99"),
        price_cents=50,
        bankroll_cents=100_000,
        kelly_multiplier=Decimal(1),
        max_position_cents=10,
    )
    assert result.is_zero
    assert result.binding_constraint == "BUDGET_BELOW_ONE_CONTRACT"


def test_zero_bankroll_produces_no_position():
    result = size_position(
        probability=Decimal("0.99"),
        price_cents=50,
        bankroll_cents=0,
        kelly_multiplier=Decimal(1),
        max_position_cents=100_000,
    )
    assert result.is_zero


def test_zero_multiplier_produces_no_position():
    result = size_position(
        probability=Decimal("0.99"),
        price_cents=50,
        bankroll_cents=1_000_000,
        kelly_multiplier=Decimal(0),
        max_position_cents=1_000_000,
    )
    assert result.is_zero


def test_binding_constraint_names_what_actually_bound():
    generous = size_position(
        probability=Decimal("0.60"),
        price_cents=50,
        bankroll_cents=100_000,
        kelly_multiplier=Decimal("0.25"),
        max_position_cents=10_000_000,
    )
    assert generous.binding_constraint == "KELLY_TARGET"

    capped = size_position(
        probability=Decimal("0.60"),
        price_cents=50,
        bankroll_cents=100_000,
        kelly_multiplier=Decimal("0.25"),
        max_position_cents=500,
    )
    assert capped.binding_constraint == "MAX_POSITION_CAP"


def test_sizing_is_deterministic():
    args = dict(
        probability=Decimal("0.63"),
        price_cents=37,
        bankroll_cents=250_000,
        kelly_multiplier=Decimal("0.2"),
        max_position_cents=50_000,
    )
    assert size_position(**args) == size_position(**args)


def test_sizing_holds_across_every_tradeable_price():
    """A sweep, because an off-by-one in the fee ceiling shows up at one
    price and nowhere else."""
    for price in ALL_PRICES:
        result = size_position(
            probability=Decimal("0.95"),
            price_cents=price,
            bankroll_cents=1_000_000,
            kelly_multiplier=Decimal("0.1"),
            max_position_cents=1_000_000,
        )
        if result.contract_count:
            assert result.order_cost_cents <= result.target_stake_cents
            assert result.fee_cents == taker_fee_cents(
                contract_count=result.contract_count, price_cents=price
            )
