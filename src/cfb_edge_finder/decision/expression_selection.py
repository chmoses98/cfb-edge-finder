"""Deterministic selection among economically equivalent expressions.

*** THE SEPARATION THIS ENFORCES ***

THESIS QUALITY -- "is this football view worth acting on?" -- is an
empirical question, answered only by an approved threshold artifact and
accumulated settled evidence. Nothing here touches it.

EXPRESSION QUALITY -- "given that a thesis has independently qualified,
which contract expresses it most cheaply?" -- is a mechanical question
about prices, fees and settlement semantics. That is all this module
answers.

Keeping them apart matters because expression comparison looks seductively
like bet selection. It is not: choosing the cheaper of two contracts that
settle identically adds no view about the game. If no thesis has passed
an approved empirical gate, this module is never consulted and the card
stays empty.

*** ALL-IN COST, NOT DISPLAYED PRICE ***

The cheapest displayed price is not the cheapest position. Kalshi's taker
fee is `ceil(0.07 x C x P x (1-P))`, which peaks at P=0.50 and vanishes at
the extremes, so two expressions quoted a cent apart can invert once fees
are included. Selection therefore ranks on all-in cost and refuses to rank
at all when a fee is unverified.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum

from cfb_edge_finder.expression.grouping import ContractSnapshot
from cfb_edge_finder.kalshi.fee_schedule import (
    KALSHI_FEE_SCHEDULE_2026_07_07_TAKER,
    calculate_fee_cents,
)
from cfb_edge_finder.schemas.common import Side


class ExpressionRejection(StrEnum):
    """Why one expression is not selectable. Every one is mechanical."""

    NO_EXECUTABLE_SIDE = "NO_EXECUTABLE_SIDE"
    MARKET_NOT_EXECUTABLE = "MARKET_NOT_EXECUTABLE"
    FEE_UNVERIFIED = "FEE_UNVERIFIED"
    QUOTE_STALE = "QUOTE_STALE"
    SEMANTICS_UNRESOLVED = "SEMANTICS_UNRESOLVED"


EXECUTABLE_MARKET_STATUSES = frozenset({"active"})
VERIFIED_FEE_STATUSES = frozenset({"VERIFIED_CURRENT"})


@dataclass(frozen=True)
class ExpressionOption:
    """One executable way to express a thesis, with its all-in cost."""

    market_ticker: str
    side: Side
    executable_price: float | None
    all_in_cost: float | None
    fee: float | None
    market_status: str | None
    fee_status: str | None
    captured_at: str | None
    semantics_resolved: bool
    rejections: tuple[ExpressionRejection, ...]

    @property
    def selectable(self) -> bool:
        return not self.rejections and self.all_in_cost is not None


@dataclass(frozen=True)
class ExpressionSelection:
    """The outcome of comparing equivalent expressions."""

    truth_condition_key: str | None
    options: tuple[ExpressionOption, ...]
    selected: ExpressionOption | None
    reason: str

    @property
    def selectable_options(self) -> tuple[ExpressionOption, ...]:
        return tuple(o for o in self.options if o.selectable)


def estimate_taker_fee(price: float) -> float | None:
    """Kalshi taker fee for ONE contract at `price`, in dollars.

    Returns None outside the tradeable [0.01, 0.99] range rather than a
    number: a contract quoted at exactly 0 or 1 is not buyable at a
    profit, and inventing a fee for it would let an unbuyable quote win a
    cheapest-cost comparison."""
    if price is None or not (0.01 <= price <= 0.99):
        return None
    # Delegated to the verified schedule, in Decimal. A float ceiling
    # computes 0.07*0.50*0.50*100 as 1.7499999999999998 at some prices
    # and rounds to the wrong whole cent, which would let one expression
    # win a cheapest-all-in comparison it should have lost.
    price_cents = int(Decimal(str(price)).scaleb(2).to_integral_value(rounding=ROUND_HALF_UP))
    if not 1 <= price_cents <= 99:
        return None
    fee_cents = calculate_fee_cents(price_cents, 1, KALSHI_FEE_SCHEDULE_2026_07_07_TAKER)
    return float(Decimal(fee_cents) / Decimal(100))


def build_option(
    snapshot: ContractSnapshot,
    side: Side,
    *,
    now: datetime,
    max_quote_age_seconds: float | None,
) -> ExpressionOption:
    """Assess ONE expression. Every refusal is structural -- there is no
    judgement here about whether the underlying view is any good."""
    price = snapshot.executable_yes_price if side is Side.YES else snapshot.executable_no_price
    rejections: list[ExpressionRejection] = []

    if price is None:
        rejections.append(ExpressionRejection.NO_EXECUTABLE_SIDE)
    if (snapshot.market_status or "").strip().lower() not in EXECUTABLE_MARKET_STATUSES:
        rejections.append(ExpressionRejection.MARKET_NOT_EXECUTABLE)
    if (snapshot.fee_status or "") not in VERIFIED_FEE_STATUSES:
        rejections.append(ExpressionRejection.FEE_UNVERIFIED)

    semantics_resolved = snapshot.semantics.parse_status == "confirmed_live"
    if not semantics_resolved:
        rejections.append(ExpressionRejection.SEMANTICS_UNRESOLVED)

    # Freshness is UNCONFIGURED-fails-closed, exactly as the eligibility
    # layer treats it: an uncertified quote age is not a fresh quote.
    if max_quote_age_seconds is None:
        rejections.append(ExpressionRejection.QUOTE_STALE)
    elif snapshot.captured_at:
        try:
            captured = datetime.fromisoformat(snapshot.captured_at.replace("Z", "+00:00"))
            if now - captured > timedelta(seconds=max_quote_age_seconds):
                rejections.append(ExpressionRejection.QUOTE_STALE)
        except ValueError:
            rejections.append(ExpressionRejection.QUOTE_STALE)
    else:
        rejections.append(ExpressionRejection.QUOTE_STALE)

    fee = estimate_taker_fee(price) if price is not None else None
    all_in = None if (price is None or fee is None) else round(price + fee, 6)
    return ExpressionOption(
        market_ticker=snapshot.semantics.market_ticker,
        side=side,
        executable_price=price,
        all_in_cost=all_in,
        fee=fee,
        market_status=snapshot.market_status,
        fee_status=snapshot.fee_status,
        captured_at=snapshot.captured_at,
        semantics_resolved=semantics_resolved,
        rejections=tuple(rejections),
    )


def select_expression(
    truth_condition_key: str | None, options: list[ExpressionOption]
) -> ExpressionSelection:
    """Pick the cheapest all-in selectable expression.

    Tie-breaking is deterministic and documented rather than incidental:
    lowest all-in cost, then lowest raw price, then market ticker
    ascending, then YES before NO. Two runs over the same input always
    choose the same contract, which is what makes a research result
    reproducible."""
    selectable = [o for o in options if o.selectable]
    if not selectable:
        return ExpressionSelection(
            truth_condition_key=truth_condition_key,
            options=tuple(options),
            selected=None,
            reason="no selectable expression: every option was refused on structural grounds",
        )
    ordered = sorted(
        selectable,
        key=lambda o: (o.all_in_cost, o.executable_price, o.market_ticker, 0 if o.side is Side.YES else 1),
    )
    best = ordered[0]
    reason = f"cheapest all-in cost {best.all_in_cost:.4f} among {len(selectable)} selectable expression(s)"
    if len(ordered) > 1 and ordered[1].all_in_cost == best.all_in_cost:
        reason += "; tie broken deterministically by price, then ticker, then side"
    return ExpressionSelection(
        truth_condition_key=truth_condition_key,
        options=tuple(options),
        selected=best,
        reason=reason,
    )
