"""Fee-aware entry economics for one executable expression, and the
strictly-mathematical comparisons between expressions of the same event.

*** THE ARITHMETIC ***
A Kalshi contract pays exactly $1.00 if its condition holds and $0.00
otherwise. Buying one contract at executable price `p` with entry fee `f`
costs `p + f` all in. Expected value at true probability `q` is therefore

    q * (1 - p - f) + (1 - q) * (-(p + f))  =  q - (p + f)

so the break-even probability is exactly the all-in cost:

    fee_adjusted_break_even_probability = p + f

Using the raw price `p` as the break-even probability -- as a naive
reading of the order book would -- understates the required probability
by the whole fee. That is the entire reason this module exists.

*** NAMING ***
`research_probability_surplus` is model probability minus fee-adjusted
break-even. It is deliberately NOT called an edge, a betting edge, or an
expected value: this milestone is pre-recommendation, and a name that
implies a decision would be the first step toward making one. Nothing
here ranks, selects, or sizes.

*** WHAT "DOMINATED" MEANS HERE ***
`ECONOMICALLY_DOMINATED_EQUIVALENT` is a statement about arithmetic, not
about desirability: two expressions settle on the SAME event and pay the
SAME $1, and one costs strictly more all in. That is true regardless of
whether either is worth holding, and it is determined without reference
to any settled outcome.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_dollars,
    get_taker_multiplier,
)
from cfb_edge_finder.schemas.common import Side

CONTRACT_PAYOUT = 1.0
RESEARCH_UNIT_CONTRACTS = 1
"""One contract, always. Fixed so every cost in this module is directly
comparable and so nothing here can be mistaken for sizing."""

DOMINANCE_EPSILON = 1e-9
"""Costs are dollar quantities on a 1-cent tick; this guards against a
floating-point difference being reported as a real price difference."""


@dataclass(frozen=True)
class ExpressionEconomics:
    """All-in entry economics for ONE executable side of ONE ticker."""

    market_ticker: str
    executable_side: Side
    executable_price: float | None
    estimated_fee: float | None
    all_in_cost: float | None
    fee_adjusted_break_even_probability: float | None
    payout: float = CONTRACT_PAYOUT
    model_probability_for_this_side: float | None = None
    research_probability_surplus: float | None = None
    """model_probability_for_this_side - fee_adjusted_break_even_probability.
    Descriptive only; see module docstring on naming."""

    fee_status: str | None = None
    fee_schedule_version: str | None = None

    @property
    def priceable(self) -> bool:
        return self.all_in_cost is not None


def estimate_entry_fee(price: float, series_ticker: str | None) -> float | None:
    """Entry taker fee in dollars for one contract at `price`, from the
    verified schedule. Returns None outside the tradeable range rather
    than a guessed zero -- an unknown fee must never silently become 0,
    which would understate the break-even probability."""
    if not (0.0 < price < 1.0):
        return None
    multiplier, _label = get_taker_multiplier(series_ticker or "")
    fee = calculate_fee_dollars(
        int(round(price * 100)), RESEARCH_UNIT_CONTRACTS, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER, multiplier
    )
    return float(fee if isinstance(fee, Decimal) else fee)


def build_expression_economics(
    *,
    market_ticker: str,
    executable_side: Side,
    executable_price: float | None,
    model_probability_for_this_side: float | None,
    series_ticker: str | None,
    fee_status: str | None = None,
    fee_schedule_version: str | None = None,
) -> ExpressionEconomics:
    """`model_probability_for_this_side` must already be expressed for the
    side being bought -- i.e. the caller has complemented it for a NO
    expression. Doing that here would hide the complementation from the
    call site, which is exactly where the sign errors live."""
    if executable_price is None:
        return ExpressionEconomics(
            market_ticker=market_ticker,
            executable_side=executable_side,
            executable_price=None,
            estimated_fee=None,
            all_in_cost=None,
            fee_adjusted_break_even_probability=None,
            model_probability_for_this_side=model_probability_for_this_side,
            fee_status=fee_status,
            fee_schedule_version=fee_schedule_version,
        )

    fee = estimate_entry_fee(executable_price, series_ticker)
    # An unknown fee yields an unknown cost. Substituting zero would make
    # the expression look cheaper than it can actually be transacted.
    all_in = None if fee is None else executable_price + fee
    surplus = (
        None
        if all_in is None or model_probability_for_this_side is None
        else model_probability_for_this_side - all_in
    )
    return ExpressionEconomics(
        market_ticker=market_ticker,
        executable_side=executable_side,
        executable_price=executable_price,
        estimated_fee=fee,
        all_in_cost=all_in,
        # Break-even probability equals the all-in cost because the payout
        # is exactly $1. Stated explicitly rather than left implicit.
        fee_adjusted_break_even_probability=all_in,
        model_probability_for_this_side=model_probability_for_this_side,
        research_probability_surplus=surplus,
        fee_status=fee_status,
        fee_schedule_version=fee_schedule_version,
    )


DOMINATED_FLAG = "ECONOMICALLY_DOMINATED_EQUIVALENT"


@dataclass(frozen=True)
class DominanceFinding:
    """One expression costs strictly more than another for the identical
    payout on the identical event."""

    truth_condition_key: str
    cheaper_ticker: str
    cheaper_side: Side
    cheaper_all_in_cost: float
    dominated_ticker: str
    dominated_side: Side
    dominated_all_in_cost: float
    cost_difference: float
    fee_difference: float
    flag: str = DOMINATED_FLAG


def find_dominated_expressions(
    truth_condition_key: str, expressions: list[ExpressionEconomics]
) -> list[DominanceFinding]:
    """Every priceable expression that costs strictly more than the
    cheapest one for this event.

    Expressions with an unknown cost are skipped, not assumed expensive:
    a missing fee is missing information, not evidence of dominance."""
    priceable = [e for e in expressions if e.priceable]
    if len(priceable) < 2:
        return []
    cheapest = min(priceable, key=lambda e: e.all_in_cost)
    findings = []
    for expression in priceable:
        if expression is cheapest:
            continue
        difference = expression.all_in_cost - cheapest.all_in_cost
        if difference <= DOMINANCE_EPSILON:
            continue
        findings.append(
            DominanceFinding(
                truth_condition_key=truth_condition_key,
                cheaper_ticker=cheapest.market_ticker,
                cheaper_side=cheapest.executable_side,
                cheaper_all_in_cost=cheapest.all_in_cost,
                dominated_ticker=expression.market_ticker,
                dominated_side=expression.executable_side,
                dominated_all_in_cost=expression.all_in_cost,
                cost_difference=difference,
                fee_difference=(expression.estimated_fee or 0.0) - (cheapest.estimated_fee or 0.0),
            )
        )
    return findings


STATIC_INCONSISTENCY_FLAG = "STATIC_PRICE_INCONSISTENCY"


@dataclass(frozen=True)
class StaticInconsistency:
    """A complementary pair of events whose combined cheapest all-in cost
    is below the $1 they are jointly guaranteed to pay.

    *** WHY ONLY THE COMPLEMENTARY-PAIR CASE ***
    Mission section 19 permits a research-only static diagnostic but
    demands it be fully defensible. This is the one construction that is
    unambiguously so: E and NOT-E partition the sample space, so buying
    one expression of each pays exactly $1.00 in every possible world. If
    the two cheapest all-in costs sum to less than $1.00, the shortfall is
    a guaranteed arithmetic surplus, with no distributional assumption
    and no modelling.

    General nested-ladder inequalities (e.g. YES on an easier rung plus NO
    on a harder rung) also admit guaranteed-payoff arguments, but they
    depend on the exact integer/half-point boundary arithmetic and on both
    legs being simultaneously fillable at the quoted size. That is not
    defensible from captured top-of-book asks alone, so it is deliberately
    NOT implemented -- correctness over novelty.

    This is a diagnostic that the captured quotes were mutually
    inconsistent at that instant. It is not a trade, is not sized, does
    not account for depth or latency, and no order is ever placed."""

    game_id: str
    dimension: str
    event_key: str
    complement_key: str
    event_cheapest_ticker: str
    event_cheapest_side: Side
    event_cheapest_cost: float
    complement_cheapest_ticker: str
    complement_cheapest_side: Side
    complement_cheapest_cost: float
    combined_cost: float
    guaranteed_shortfall: float
    """CONTRACT_PAYOUT - combined_cost, when positive."""
    flag: str = STATIC_INCONSISTENCY_FLAG


def detect_static_inconsistency(
    *,
    game_id: str,
    dimension: str,
    event_key: str,
    complement_key: str,
    event_expressions: list[ExpressionEconomics],
    complement_expressions: list[ExpressionEconomics],
) -> StaticInconsistency | None:
    """Both legs must be priceable with known fees; an unknown fee makes
    the claim unprovable and returns None rather than a maybe."""
    event_priceable = [e for e in event_expressions if e.priceable]
    complement_priceable = [e for e in complement_expressions if e.priceable]
    if not event_priceable or not complement_priceable:
        return None

    a = min(event_priceable, key=lambda e: e.all_in_cost)
    b = min(complement_priceable, key=lambda e: e.all_in_cost)
    combined = a.all_in_cost + b.all_in_cost
    if combined >= CONTRACT_PAYOUT - DOMINANCE_EPSILON:
        return None
    return StaticInconsistency(
        game_id=game_id,
        dimension=dimension,
        event_key=event_key,
        complement_key=complement_key,
        event_cheapest_ticker=a.market_ticker,
        event_cheapest_side=a.executable_side,
        event_cheapest_cost=a.all_in_cost,
        complement_cheapest_ticker=b.market_ticker,
        complement_cheapest_side=b.executable_side,
        complement_cheapest_cost=b.all_in_cost,
        combined_cost=combined,
        guaranteed_shortfall=CONTRACT_PAYOUT - combined,
    )
