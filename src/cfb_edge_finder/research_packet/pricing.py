"""Deterministic Kalshi price arithmetic for an ANALYST'S probability. PURE. No market data, no model.

The research packet never originates a probability. This module only converts a probability (and a stated
uncertainty range) that a human or ChatGPT supplies into the price arithmetic that follows from it:

    all-in cost  ->  fee-inclusive break-even probability  ->  bet-up-to price  ->  stake tier

Everything reuses the repository's existing fee machinery (``kalshi.fee_schedule`` / ``expression.fair_value``):
the taker fee is ``ceil_to_cent(multiplier * 0.07 * contracts * P * (1 - P))`` for the ORDER, which is what
Kalshi charges, so a one-unit order's fee is not simply the per-contract figure scaled.

*** WHAT THIS IS NOT ***
* It does not say whether a probability is right. A stated range is an opinion about uncertainty, not a measured
  one; ``qualified_positive_ev`` is therefore never produced here (that needs a settled-outcome reliability table,
  see ``execution/pit_review.py``).
* It does not place orders and does not bypass the automated-approval gates in ``execution``/``decision``.
* The stake tiers are a transparent analyst heuristic that respects the owner's rules (1U = $75, max 5U per bet).
  They are NOT Kelly sizing and are not calibrated; an uncalibrated edge is capped well below the 5U ceiling.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from cfb_edge_finder.expression.fair_value import fee_inclusive_break_even, order_fee_dollars

UNIT_DOLLARS = 75.0
MAX_UNITS_PER_BET = 5.0
#: Stake tiers by LOWER-BOUND edge over fee-inclusive break-even (percentage points of probability). The edge is
#: measured at the lower end of the analyst's stated range, so a wide range shrinks the stake automatically.
STAKE_TIERS: tuple[tuple[float, float], ...] = (
    (0.14, 5.0),
    (0.10, 4.0),
    (0.07, 3.0),
    (0.04, 2.0),
    (0.02, 1.0),
    (0.005, 0.5),
)
#: Without a settled-outcome calibration table every stake is capped here regardless of the edge claimed.
UNCALIBRATED_UNIT_CAP = 3.0
#: A range wider than this (high - low) is too uncertain to size at all: flag, do not stake.
MAX_SIZABLE_RANGE_WIDTH = 0.30

SIDES = ("YES", "NO")


def contracts_for_dollars(price: float, dollars: float) -> int:
    """Whole contracts a dollar budget buys at ``price`` (fees excluded; a fill can only be whole contracts)."""
    if not (0.0 < price < 1.0) or dollars <= 0:
        return 0
    return int(dollars // price)


def order_fee(price: float, contracts: float, series_ticker: str | None) -> float | None:
    """The ORDER's Kalshi taker fee in dollars (rounded up to the cent). None = unknown, never zero."""
    return order_fee_dollars(price, contracts, series_ticker)


def all_in_cost_per_contract(price: float, contracts: float, series_ticker: str | None) -> float | None:
    """Ask plus the order's fee spread over its contracts: what one contract really costs at this size."""
    fee = order_fee(price, contracts, series_ticker)
    return None if fee is None else round(price + fee / contracts, 6)


def break_even_probability(price: float, contracts: float, series_ticker: str | None) -> float | None:
    """The probability at which buying ``contracts`` at ``price`` has exactly zero expected value after fees."""
    value = fee_inclusive_break_even(price, contracts, series_ticker)
    return None if value is None else round(value, 6)


def bet_up_to_price(
    probability_low: float,
    series_ticker: str | None,
    *,
    dollars: float = UNIT_DOLLARS,
    required_margin: float = 0.0,
) -> float | None:
    """Highest whole-cent price at which ``probability_low`` still clears fee-inclusive break-even + margin.

    Evaluated for an order of roughly ``dollars`` at each candidate price (contracts = floor(dollars / price)),
    because the fee is an order-level figure. ``None`` = no price qualifies (or the fee schedule is unknown):
    the correct answer is then "no bet at any price on this estimate", never a guess.
    """
    if not (0.0 < probability_low < 1.0):
        return None
    best: float | None = None
    for cents in range(1, 100):
        price = cents / 100
        contracts = contracts_for_dollars(price, dollars)
        if contracts < 1:
            continue
        be = break_even_probability(price, contracts, series_ticker)
        if be is None:
            return None
        if probability_low - be >= required_margin:
            best = price
    return best


def stake_units(
    probability_low: float,
    probability_high: float,
    break_even: float | None,
    *,
    calibrated: bool = False,
    quoted_contracts: float | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    """A transparent unit-stake tier for an analyst-stated range. 0U is a valid, common answer.

    Sized from the LOWER bound's edge over fee-inclusive break-even, capped at the owner's 5U ceiling, capped lower
    (``UNCALIBRATED_UNIT_CAP``) while the probability is an uncalibrated opinion, and limited by quoted size.
    """
    reasons: list[str] = []
    if break_even is None:
        return {"units": 0.0, "dollars": 0.0, "reasons": ["fee or price unknown: no stake without a known cost"]}
    if probability_high < probability_low:
        return {"units": 0.0, "dollars": 0.0, "reasons": ["range is inverted (high < low)"]}
    width = probability_high - probability_low
    if width > MAX_SIZABLE_RANGE_WIDTH:
        return {"units": 0.0, "dollars": 0.0, "reasons": [f"range width {width:.2f} > {MAX_SIZABLE_RANGE_WIDTH}: too uncertain to size"]}
    edge = probability_low - break_even
    units = 0.0
    for threshold, tier_units in STAKE_TIERS:
        if edge >= threshold:
            units = tier_units
            break
    if units == 0.0:
        reasons.append(f"lower-bound edge {edge * 100:+.1f}pp does not clear the smallest tier: no stake")
    if units > MAX_UNITS_PER_BET:
        units = MAX_UNITS_PER_BET
    if not calibrated and units > UNCALIBRATED_UNIT_CAP:
        units = UNCALIBRATED_UNIT_CAP
        reasons.append(f"capped at {UNCALIBRATED_UNIT_CAP}U: the probability is an uncalibrated analyst opinion")
    dollars = units * UNIT_DOLLARS
    if quoted_contracts is not None and price:
        depth_dollars = quoted_contracts * price
        if dollars > depth_dollars:
            dollars = math.floor(depth_dollars / UNIT_DOLLARS * 2) / 2 * UNIT_DOLLARS
            units = dollars / UNIT_DOLLARS
            reasons.append("limited by the quoted size at the ask")
    return {
        "units": units,
        "dollars": round(units * UNIT_DOLLARS, 2),
        "lower_bound_edge": round(edge, 4),
        "tier_basis": "lower-bound edge over fee-inclusive break-even",
        "calibrated": calibrated,
        "reasons": reasons,
    }


@dataclass(frozen=True)
class PriceView:
    side: str
    ask: float | None
    contracts: int
    order_fee: float | None
    all_in_per_contract: float | None
    break_even_probability: float | None


def view_side(side: str, ask: float | None, series_ticker: str | None, *, dollars: float = UNIT_DOLLARS) -> PriceView:
    """One side of one contract at the ask, priced for a ``dollars`` order. YES uses the YES ask, NO the NO ask."""
    if side not in SIDES:
        raise ValueError(f"side must be one of {SIDES}")
    if ask is None or not (0.0 < ask < 1.0):
        return PriceView(side, ask, 0, None, None, None)
    contracts = contracts_for_dollars(ask, dollars)
    if contracts < 1:
        return PriceView(side, ask, 0, None, None, None)
    fee = order_fee(ask, contracts, series_ticker)
    return PriceView(
        side,
        ask,
        contracts,
        fee,
        None if fee is None else round(ask + fee / contracts, 6),
        None if fee is None else round(ask + fee / contracts, 6),
    )


def evaluate(
    *,
    side: str,
    ask: float,
    series_ticker: str | None,
    probability_low: float,
    probability_point: float,
    probability_high: float,
    quoted_contracts: float | None = None,
    calibrated: bool = False,
    required_margin: float = 0.0,
) -> dict[str, Any]:
    """Everything that follows from an analyst's probability for ONE side of ONE contract.

    ``probability_*`` are the probability that THE SIDE BEING BOUGHT WINS (P(YES) when side is YES, P(NO) when NO).
    """
    if not (0.0 <= probability_low <= probability_point <= probability_high <= 1.0):
        raise ValueError("require 0 <= low <= point <= high <= 1")
    view = view_side(side, ask, series_ticker)
    be = view.break_even_probability
    ceiling = bet_up_to_price(probability_low, series_ticker, required_margin=required_margin)
    stake = stake_units(
        probability_low, probability_high, be, calibrated=calibrated, quoted_contracts=quoted_contracts, price=ask
    )
    clears_at_ask = be is not None and probability_low - be >= required_margin
    return {
        "side": side,
        "ask": ask,
        "contracts_for_one_unit": view.contracts,
        "order_fee_one_unit": view.order_fee,
        "all_in_cost_per_contract": view.all_in_per_contract,
        "break_even_probability": be,
        "analyst_probability": {"low": probability_low, "point": probability_point, "high": probability_high},
        "edge_at_point_pp": None if be is None else round((probability_point - be) * 100, 2),
        "edge_at_lower_bound_pp": None if be is None else round((probability_low - be) * 100, 2),
        "bet_up_to": ceiling,
        "clears_at_current_ask": clears_at_ask,
        "stake": stake if clears_at_ask else {"units": 0.0, "dollars": 0.0, "reasons": ["lower bound does not clear break-even at this ask"]},
        "qualified_positive_ev": False,
        "qualified_positive_ev_note": (
            "always False here: a stated range is an opinion, not a settled-outcome calibration. Present any bet "
            "as a football lean / conditional candidate priced up to the stated ceiling."
        ),
        "unit_dollars": UNIT_DOLLARS,
        "max_units_per_bet": MAX_UNITS_PER_BET,
    }
