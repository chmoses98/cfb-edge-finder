"""Deterministic Kalshi price arithmetic for an ANALYST'S probability. PURE. No market data, no model.

The research packet never originates a probability. This module only converts a probability (and a stated
uncertainty range) that a human or ChatGPT supplies into the price arithmetic that follows from it:

    all-in cost  ->  fee-inclusive break-even probability  ->  bet-up-to price  ->  advisory stake check

Everything reuses the repository's existing fee machinery (``kalshi.fee_schedule`` / ``expression.fair_value``):
the taker fee is ``ceil_to_cent(multiplier * 0.07 * contracts * P * (1 - P))`` for the ORDER, which is what
Kalshi charges, so a one-unit order's fee is not simply the per-contract figure scaled.

*** WHAT THIS IS NOT ***
* It does not say whether a probability is right. A stated range is an opinion about uncertainty, not a measured
  one; ``qualified_positive_ev`` is therefore never produced here (that needs a settled-outcome reliability table,
  see ``execution/pit_review.py``).
* It does not place orders and does not bypass the automated-approval gates in ``execution``/``decision``.
* Sizing is ADVISORY and analyst-owned (``stake_check``): the tool derives no stake from edge, confidence or range
width, because no settled-outcome evidence supports such a rule. It only enforces the owner's mechanical limits (1U =
$75, maximum 5U per bet), the quoted size, and "no stake when the lower bound does not clear break-even".
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from cfb_edge_finder.expression.fair_value import fee_inclusive_break_even, order_fee_dollars

UNIT_DOLLARS = 75.0
MAX_UNITS_PER_BET = 5.0

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


def stake_check(
    proposed_units: float | None,
    probability_low: float,
    break_even: float | None,
    *,
    quoted_contracts: float | None = None,
    price: float | None = None,
) -> dict[str, Any]:
    """ADVISORY check of a stake the ANALYST proposes. This function never chooses a stake.

    There is no empirical sizing rule behind this repository's probabilities (no settled-outcome calibration exists),
    so no stake is derived from an edge size, a confidence level or a range width. With no proposal the answer is "not
    assigned". With a proposal it only enforces what is mechanical and owner-set: no stake when the lower bound does
    not clear fee-inclusive break-even, never above 5U ($375) on one bet, never more than the quoted size at the ask.
    The reasons it returns are for the analyst to weigh; the analyst owns the number and must label it advisory.
    """
    base: dict[str, Any] = {
        "advisory": True,
        "assigned_by_tool": False,
        "unit_dollars": UNIT_DOLLARS,
        "max_units_per_bet": MAX_UNITS_PER_BET,
        "basis": (
            "analyst-proposed and advisory: the tool derives no stake from edge, confidence or range width because no "
            "settled-outcome evidence supports any such rule"
        ),
    }
    if proposed_units is None:
        return {
            **base,
            "status": "NOT_ASSIGNED",
            "units": None,
            "dollars": None,
            "reasons": ["no stake proposed; unsized candidates are a normal output"],
        }
    if proposed_units <= 0:
        return {**base, "status": "NO_STAKE", "units": 0.0, "dollars": 0.0, "reasons": ["analyst proposed no stake"]}
    reasons: list[str] = []
    if break_even is None:
        return {
            **base,
            "status": "NO_STAKE",
            "units": 0.0,
            "dollars": 0.0,
            "reasons": ["fee or price unknown: no stake without a known cost"],
        }
    if probability_low < break_even:
        return {
            **base,
            "status": "NO_STAKE",
            "units": 0.0,
            "dollars": 0.0,
            "reasons": [
                f"the lower bound ({probability_low:.3f}) does not clear fee-inclusive break-even "
                    f"({break_even:.3f}) at this ask"
            ],
        }
    units = proposed_units
    if units > MAX_UNITS_PER_BET:
        units = MAX_UNITS_PER_BET
        reasons.append("clamped to the owner's 5U maximum per bet")
    if quoted_contracts is not None and price:
        depth_units = (quoted_contracts * price) / UNIT_DOLLARS
        if units > depth_units:
            units = math.floor(depth_units * 2) / 2
            reasons.append("limited to the quoted size at the ask")
    status = "ADVISORY_PROPOSAL" if units == proposed_units else "ADVISORY_PROPOSAL_ADJUSTED"
    if units <= 0:
        status = "NO_STAKE"
    return {**base, "status": status, "units": units, "dollars": round(units * UNIT_DOLLARS, 2), "reasons": reasons}


QUALIFICATION: dict[str, Any] = {
    "tier": "ANALYST_RECOMMENDATION_WITH_STATED_UNCERTAINTY",
    "empirically_validated_positive_ev": False,
    "meaning": (
        "the probability and range are the analyst's stated opinion. The ceiling below is arithmetic on that "
            "opinion; it is "
        "not evidence that the wager has positive expected value"
    ),
    "required_for_validated_positive_ev": (
        "a probability calibrated on settled outcomes (a reliability table) whose lower bound clears fee-inclusive "
        "break-even, as computed by execution/pit_review.py. None exists, so this tool can never produce that tier"
    ),
    "automated_approval_gates": "unchanged and separate: nothing here qualifies, approves or places an order",
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
    proposed_units: float | None = None,
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
        "stake": stake_check(proposed_units, probability_low, be, quoted_contracts=quoted_contracts, price=ask),
        "qualified_positive_ev": False,
        "qualification": QUALIFICATION
        if clears_at_ask
        else {
            **QUALIFICATION,
            "tier": "PASS_AT_THIS_ASK",
            "meaning": (
                "the lower bound of the analyst's range does not clear fee-inclusive break-even at the captured ask: "
                "this is a pass at this price, not a recommendation. The bet-up-to ceiling is the most the analyst's "
                "own lower bound could justify paying"
            ),
        },
        "unit_dollars": UNIT_DOLLARS,
        "max_units_per_bet": MAX_UNITS_PER_BET,
    }
