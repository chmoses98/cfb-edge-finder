"""Kelly and stake arithmetic for Kalshi binary contracts, in exact cents.

*** WHY DECIMAL AND CENTS ***

Kalshi prices are integer cents and the taker fee is a ceiling over a
product of them. Binary floats make `0.07 * 100 * 0.5 * 0.5` land a hair
below or above the true value and the ceiling then jumps a whole cent, so
a float implementation is wrong in a way that only shows up on specific
prices. Everything here is `Decimal` or `int`; money never touches
`float`.

*** THE FEE IS NOT LINEAR IN CONTRACT COUNT ***

`ceil(0.07 * C * P * (1-P))` is a ceiling over the WHOLE order, so the
per-contract cost depends on how many contracts you buy. Sizing that
divides a one-contract fee across an order is wrong at small counts,
which is where it matters most. `effective_cost_per_contract` computes it
for the actual count instead.

*** THE FEE RULE IS BORROWED, NOT REWRITTEN ***

`kalshi/fee_schedule.py` already carries the verified July 2026 schedule,
its provenance, and the CFB series-multiplier table. This module calls
it. A second ceiling implementation living here could drift from that one
without any test noticing, and the two would disagree about money.

*** WHAT IS DELIBERATELY MISSING ***

No default bankroll. No default Kelly multiplier. No default position
cap. No default probability haircut. No "recommended" anything. Those are
policy and empirics; this module is arithmetic. Every one of them must be
passed explicitly by a caller who can defend the number, and every
function raises rather than clamping when an input is out of domain --
silent clamping is how an unreasonable input becomes a plausible-looking
stake.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_FLOOR, Decimal, InvalidOperation

from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_cents,
    get_taker_multiplier,
)

TAKER_SCHEDULE = KALSHI_FEE_SCHEDULE_2026_07_07_TAKER
"""Taker side only. The research pipeline never places orders, so the
taker schedule is used throughout as the conservative reference -- an
estimate that assumes the worse of the two fee sides cannot flatter a
position."""

PAYOUT_CENTS = 100
"""A Kalshi binary contract settles at $1.00 or $0.00."""

MIN_TRADEABLE_PRICE_CENTS = 1
MAX_TRADEABLE_PRICE_CENTS = 99
"""The fee formula is defined only on [1, 99]. 0 and 100 are not
tradeable prices, and extrapolating the formula to them would invent a
fee for an order that cannot exist."""


class SizingDomainError(ValueError):
    """An input outside the domain where the arithmetic is meaningful.

    Raised rather than clamped on purpose: a clamp turns a caller's bug
    into a number that looks like an answer."""


def _as_decimal(value: Decimal | int | str, *, name: str) -> Decimal:
    if isinstance(value, float):
        raise SizingDomainError(
            f"{name} must not be a float -- pass Decimal, int, or str to keep cent arithmetic exact"
        )
    try:
        return Decimal(value)
    except (InvalidOperation, TypeError) as exc:
        raise SizingDomainError(f"{name} is not a valid decimal: {value!r}") from exc


def validate_price_cents(price_cents: int) -> int:
    """The executable ASK in integer cents, on [1, 99]."""
    if isinstance(price_cents, bool) or not isinstance(price_cents, int):
        raise SizingDomainError(f"price_cents must be an int, got {type(price_cents).__name__}")
    if not MIN_TRADEABLE_PRICE_CENTS <= price_cents <= MAX_TRADEABLE_PRICE_CENTS:
        raise SizingDomainError(
            f"price_cents {price_cents} outside tradeable range "
            f"[{MIN_TRADEABLE_PRICE_CENTS}, {MAX_TRADEABLE_PRICE_CENTS}]"
        )
    return price_cents


def validate_probability(probability: Decimal | int | str, *, name: str = "probability") -> Decimal:
    """A probability on the CLOSED [0, 1]. 0 and 1 are legal inputs even
    though they make Kelly degenerate; the degeneracy is handled where it
    arises, not hidden by rejecting the input."""
    value = _as_decimal(probability, name=name)
    if not (Decimal(0) <= value <= Decimal(1)):
        raise SizingDomainError(f"{name} {value} outside [0, 1]")
    return value


def validate_contract_count(contract_count: int) -> int:
    if isinstance(contract_count, bool) or not isinstance(contract_count, int):
        raise SizingDomainError(f"contract_count must be an int, got {type(contract_count).__name__}")
    if contract_count < 0:
        raise SizingDomainError(f"contract_count must be non-negative, got {contract_count}")
    return contract_count


def taker_fee_cents(*, contract_count: int, price_cents: int, series_ticker: str | None = None) -> int:
    """`ceil(M * 0.07 * C * P * (1-P))` in whole cents, delegated to the
    verified schedule in `kalshi/fee_schedule.py`.

    `series_ticker` is optional only because a caller may legitimately
    not know it; omitting it uses the schedule's own documented general
    default multiplier, which is what every CFB series resolves to today
    anyway (see `CFB_SERIES_MULTIPLIERS`)."""
    count = validate_contract_count(contract_count)
    price = validate_price_cents(price_cents)
    if count == 0:
        return 0
    if series_ticker is None:
        return calculate_fee_cents(price, count, TAKER_SCHEDULE)
    multiplier, _evidence = get_taker_multiplier(series_ticker)
    return calculate_fee_cents(price, count, TAKER_SCHEDULE, multiplier)


def order_cost_cents(*, contract_count: int, price_cents: int, series_ticker: str | None = None) -> int:
    """Total cash out the door: contracts plus the whole-order fee."""
    count = validate_contract_count(contract_count)
    price = validate_price_cents(price_cents)
    return count * price + taker_fee_cents(
        contract_count=count, price_cents=price, series_ticker=series_ticker
    )


def effective_cost_per_contract(
    *, contract_count: int, price_cents: int, series_ticker: str | None = None
) -> Decimal:
    """All-in cost of one contract at this order size, in cents.

    Strictly decreasing in `contract_count` because the fee ceiling is
    amortised. Sizing that ignores this overstates cost on small orders
    and understates the fee drag on a single contract."""
    count = validate_contract_count(contract_count)
    if count == 0:
        raise SizingDomainError("effective cost per contract is undefined for a zero-contract order")
    total = order_cost_cents(contract_count=count, price_cents=price_cents, series_ticker=series_ticker)
    return Decimal(total) / Decimal(count)


def fee_adjusted_break_even(
    *, contract_count: int, price_cents: int, series_ticker: str | None = None
) -> Decimal:
    """The win probability at which this ORDER breaks even.

    Equals all-in cost divided by the $1 payout. Above 1 when fees make
    the position unwinnable at any probability -- returned as-is rather
    than capped, because a break-even above 1 is exactly the signal a
    caller needs to see."""
    cost = effective_cost_per_contract(
        contract_count=contract_count, price_cents=price_cents, series_ticker=series_ticker
    )
    return cost / Decimal(PAYOUT_CENTS)


def expected_value_cents(
    *, probability: Decimal | int | str, contract_count: int, price_cents: int
) -> Decimal:
    """Expected profit of the whole order in cents. Negative is a loss.

    `probability` is the caller's probability that the contract settles
    YES at $1. No haircut is applied -- if the caller wants one, the
    caller applies it, visibly, before calling."""
    p = validate_probability(probability)
    count = validate_contract_count(contract_count)
    if count == 0:
        return Decimal(0)
    cost = Decimal(order_cost_cents(contract_count=count, price_cents=price_cents))
    gross = p * Decimal(count) * Decimal(PAYOUT_CENTS)
    return gross - cost


def full_kelly_fraction(*, probability: Decimal | int | str, all_in_cost_cents: Decimal | int | str) -> Decimal:
    """The FULL-Kelly bankroll fraction for a binary at this all-in cost.

    For a $1 payout bought at cost c (in dollars), profit on a win is
    (1 - c) and loss is c, so b = (1 - c)/c and

        f* = (p*b - (1-p)) / b = (p - c) / (1 - c)

    Returns 0 when the edge is non-positive: Kelly's answer to a bad bet
    is not a negative stake, it is no bet. This is FULL Kelly. It is not
    a recommendation -- full Kelly is far too aggressive against estimated
    (not known) probabilities, and this module deliberately supplies no
    default fraction of it."""
    p = validate_probability(probability)
    cost = _as_decimal(all_in_cost_cents, name="all_in_cost_cents") / Decimal(PAYOUT_CENTS)
    if cost <= 0:
        raise SizingDomainError(f"all_in_cost_cents must be positive, got {all_in_cost_cents!r}")
    if cost >= 1:
        # Cost at or above the payout: the position cannot profit, so
        # there is no positive Kelly fraction to compute.
        return Decimal(0)
    if p <= cost:
        return Decimal(0)
    return (p - cost) / (Decimal(1) - cost)


def scaled_kelly_fraction(
    *,
    probability: Decimal | int | str,
    all_in_cost_cents: Decimal | int | str,
    kelly_multiplier: Decimal | int | str,
) -> Decimal:
    """Full Kelly times a multiplier the CALLER must supply.

    There is no default. A default here would become the house fraction
    by accident, and the right multiplier depends on how much the
    probability estimate can be trusted -- which, with zero settled
    prospective observations, is currently not at all."""
    multiplier = _as_decimal(kelly_multiplier, name="kelly_multiplier")
    if not (Decimal(0) <= multiplier <= Decimal(1)):
        raise SizingDomainError(f"kelly_multiplier {multiplier} outside [0, 1]")
    return full_kelly_fraction(probability=probability, all_in_cost_cents=all_in_cost_cents) * multiplier


@dataclass(frozen=True)
class SizingResult:
    """The arithmetic outcome. Not an instruction to trade."""

    contract_count: int
    price_cents: int
    order_cost_cents: int
    fee_cents: int
    target_stake_cents: Decimal
    kelly_fraction: Decimal
    expected_value_cents: Decimal
    binding_constraint: str

    @property
    def is_zero(self) -> bool:
        return self.contract_count == 0


def size_position(
    *,
    probability: Decimal | int | str,
    price_cents: int,
    bankroll_cents: int,
    kelly_multiplier: Decimal | int | str,
    max_position_cents: int,
) -> SizingResult:
    """Whole-contract sizing that respects the non-linear fee.

    Every knob is required. The count is found by taking the largest
    whole count whose ALL-IN cost fits the smaller of the Kelly target
    and the caller's cap, then verified -- rather than dividing the target
    by the price and hoping the fee fits, which overshoots the budget on
    small orders."""
    price = validate_price_cents(price_cents)
    if isinstance(bankroll_cents, bool) or not isinstance(bankroll_cents, int) or bankroll_cents < 0:
        raise SizingDomainError(f"bankroll_cents must be a non-negative int, got {bankroll_cents!r}")
    if (
        isinstance(max_position_cents, bool)
        or not isinstance(max_position_cents, int)
        or max_position_cents < 0
    ):
        raise SizingDomainError(f"max_position_cents must be a non-negative int, got {max_position_cents!r}")

    # Cost per contract at a nominal single contract, used only to get a
    # first Kelly read; the real cost is recomputed for the chosen count.
    single_cost = effective_cost_per_contract(contract_count=1, price_cents=price)
    fraction = scaled_kelly_fraction(
        probability=probability, all_in_cost_cents=single_cost, kelly_multiplier=kelly_multiplier
    )
    kelly_target = fraction * Decimal(bankroll_cents)
    budget = min(kelly_target, Decimal(max_position_cents), Decimal(bankroll_cents))

    if budget <= 0:
        binding = "NO_POSITIVE_EDGE" if fraction <= 0 else "BUDGET_ZERO"
        return SizingResult(
            contract_count=0,
            price_cents=price,
            order_cost_cents=0,
            fee_cents=0,
            target_stake_cents=max(budget, Decimal(0)),
            kelly_fraction=fraction,
            expected_value_cents=Decimal(0),
            binding_constraint=binding,
        )

    # Ignoring the fee gives an upper bound on the count; walk down until
    # the all-in cost fits. The loop runs at most a couple of steps
    # because the fee is at most 1.75 cents per contract.
    count = int((budget / Decimal(price)).to_integral_value(rounding=ROUND_FLOOR))
    while count > 0 and Decimal(order_cost_cents(contract_count=count, price_cents=price)) > budget:
        count -= 1

    if count == 0:
        return SizingResult(
            contract_count=0,
            price_cents=price,
            order_cost_cents=0,
            fee_cents=0,
            target_stake_cents=budget,
            kelly_fraction=fraction,
            expected_value_cents=Decimal(0),
            binding_constraint="BUDGET_BELOW_ONE_CONTRACT",
        )

    if kelly_target <= Decimal(max_position_cents) and kelly_target <= Decimal(bankroll_cents):
        binding = "KELLY_TARGET"
    elif Decimal(max_position_cents) <= Decimal(bankroll_cents):
        binding = "MAX_POSITION_CAP"
    else:
        binding = "BANKROLL"

    return SizingResult(
        contract_count=count,
        price_cents=price,
        order_cost_cents=order_cost_cents(contract_count=count, price_cents=price),
        fee_cents=taker_fee_cents(contract_count=count, price_cents=price),
        target_stake_cents=budget,
        kelly_fraction=fraction,
        expected_value_cents=expected_value_cents(
            probability=probability, contract_count=count, price_cents=price
        ),
        binding_constraint=binding,
    )
