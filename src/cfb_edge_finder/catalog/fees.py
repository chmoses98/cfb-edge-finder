"""Kalshi fee mechanics, as currently documented — and only as far as a
credential-free public catalog can honestly go.

*** PRIMARY SOURCE ***
https://docs.kalshi.com/getting_started/fee_rounding, captured verbatim on
2026-09-18 into docs/evidence/kalshi_fee_and_override_probe.txt (this
environment cannot reach docs.kalshi.com; the probe runs on an Actions
runner). The official fee-schedule PDF and regulatory page both returned
HTTP 429 from a fresh runner — a Cloudflare signature, not rate limiting —
so the docs page is the source of truth used here, and nothing below is
inferred beyond it.

*** WHAT THE DOCS ACTUALLY SAY ***
Fees are six-decimal dollar amounts ($0.000001 granularity). Every fill
produces THREE components:

    Trade fee     fee from the fee model, rounded UP to the nearest $0.000001
    Rounding fee  adjustment that restores the user's target balance precision
    Rebate        refund from accumulated rounding overpayment

    Net fee = trade fee + rounding fee - rebate      (always >= $0.00)

Target balance precision is $0.0001 for direct members and $0.01 for
non-direct members. The rounding fee, the rebate and the accumulator all
depend on the user's member type and on the fill sequence of a specific
order — the accumulator is maintained per order across all its fills,
taker and maker alike.

*** WHY THIS CATALOG PUBLISHES ONLY THE TRADE FEE ***
A public, credential-free market catalog cannot know a member type, an
order's fill sequence, or an accumulator balance. It therefore cannot know
a net fee, and must not imply that it does. What it CAN compute exactly is
the documented **trade fee**: the fee model rounded up to $0.000001. Every
published field is named and flagged to say precisely that.

*** THE DEFECT THIS MODULE CORRECTS ***
The previous helper rounded the model fee UP TO A WHOLE CENT and published
the result as "estimated_fee_per_contract_at_mid". Both halves were wrong:

  - Rounding. The official rule is ceil to $0.000001, not to $0.01. On the
    docs' own worked example (model fee $0.00363825) the correct trade fee
    is $0.003639; ceiling to a cent gives $0.01 — an overstatement of
    roughly 2.7x, and it would report a non-zero fee for a contract whose
    true trade fee is a fraction of a cent.
  - Naming. It read as "the fee" when it was neither net nor exact.

*** WHY THE ROUNDING-FEE HELPERS EXIST BUT ARE NOT PUBLISHED ***
`rounding_fee_components` and `rebate_schedule` implement the documented
rounding and accumulator mechanics so they can be TESTED against the
docs' own worked examples — which is the only way to show this module
reads the spec correctly. They take the member precision explicitly and
are never called by the artifact builder, because the artifact has no way
to know which precision applies.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

FEE_GRANULARITY_DOLLARS = 0.000001
"""Fees are six-decimal dollar amounts -- "the finest precision a fill's
revenue (price x quantity) can occupy"."""

DIRECT_MEMBER_PRECISION_DOLLARS = 0.0001
NON_DIRECT_MEMBER_PRECISION_DOLLARS = 0.01
"""Target balance precision: $0.0001 (0.01c) for direct members, $0.01
(1c) for non-direct members. Which one applies to a given account is not
knowable from public market data."""

QUADRATIC_FEE_COEFFICIENT = 0.07
"""The published quadratic fee model: 0.07 * C * P * (1 - P). Confirmed
against the docs' worked example, where a 1-contract fill at P=$0.055
yields a model fee of exactly $0.00363825
(0.07 * 1 * 0.055 * 0.945 = 0.00363825)."""

MODEL_TRADE_FEE_FORMULA = "ceil_to_0.000001(fee_multiplier * 0.07 * contracts * P * (1 - P))"

NET_FEE_EXCLUDES = ("rounding_fee", "rebate", "fee_accumulator", "user_balance_precision")

NET_FEE_NOTE = (
    "This is the documented TRADE FEE only (fee model, rounded up to $0.000001) -- NOT a net fee. "
    "Kalshi's net fee is trade fee + rounding fee - rebate, where the rounding fee, the rebate and "
    "the per-order fee accumulator all depend on the account's target balance precision ($0.0001 "
    "direct member, $0.01 non-direct) and on that order's fill sequence. None of those are knowable "
    "without credentials, so this public catalog does not estimate them."
)


class FeeSource(StrEnum):
    EVENT_OVERRIDE = "event_override"
    """The event supplied fee_type_override / fee_multiplier_override."""
    SERIES = "series"
    """Inherited from the parent series, no override present."""
    UNAVAILABLE = "unavailable"
    """Fee metadata could not be established. NOT a default -- see
    `unavailable_reason`. The catalog publishes no fee amount in this
    case rather than guessing."""


class FeeModelSupport(StrEnum):
    SUPPORTED = "supported"
    UNSUPPORTED_MODEL = "unsupported_model"
    """A fee_type this module has no formula for. The type is reported
    verbatim and no amount is computed -- a fabricated number would be
    worse than an absent one."""
    METADATA_UNAVAILABLE = "metadata_unavailable"


_QUADRATIC_PREFIX = "quadratic"


@dataclass(frozen=True)
class EffectiveFee:
    """The fee metadata that actually applies to one contract.

    Resolution order is the one Kalshi documents: an event's
    `fee_type_override` / `fee_multiplier_override` wins over the parent
    series' `fee_type` / `fee_multiplier`."""

    fee_type: str | None
    fee_multiplier: float | None
    source: FeeSource
    support: FeeModelSupport
    unavailable_reason: str | None = None

    @property
    def is_quadratic(self) -> bool:
        return bool(self.fee_type) and str(self.fee_type).lower().startswith(_QUADRATIC_PREFIX)

    @property
    def maker_fee_applies(self) -> bool | None:
        """True when the effective fee type charges the resting side too.

        None -- not False -- whenever we cannot actually establish it:
        when no fee type was resolved at all, and equally when the fee
        type is one this code does not model. "We could not establish
        whether maker fees apply" is a different statement from "they do
        not", and only the second one is a claim.

        Within the quadratic family the answer is in the name -- Kalshi
        spells it `quadratic` vs `quadratic_with_maker_fees`. For a
        schedule outside that family we have no basis to assume the same
        naming convention holds, so we do not answer."""
        if not self.fee_type or not self.is_quadratic:
            return None
        return "maker" in str(self.fee_type).lower()

    @property
    def computable(self) -> bool:
        return self.support is FeeModelSupport.SUPPORTED


def resolve_effective_fee(
    event: dict[str, Any] | None,
    series: dict[str, Any] | None,
    series_lookup_succeeded: bool = True,
) -> EffectiveFee:
    """Resolve the fee metadata for a contract: event override, else series.

    *** WHY THIS FAILS CLOSED ***
    The previous behaviour treated a missing `fee_type` as quadratic and a
    missing `fee_multiplier` as 1. At the catalog boundary that converts a
    DATA FAILURE into a plausible-looking number, which is the one thing
    this repository's discovery layer refuses to do everywhere else. The
    public API normally supplies series fee metadata -- a live sweep found
    0 of 145 CFB series missing `fee_type` -- so its absence in a capture
    is a diagnostic condition, not permission to assume the common case.

    `series_lookup_succeeded=False` says the series fetch itself failed, as
    distinct from a series that genuinely carries no fee metadata. Both
    yield no fee, with different reasons.
    """
    event = event or {}

    override_type = event.get("fee_type_override")
    override_multiplier = event.get("fee_multiplier_override")
    has_override = override_type not in (None, "") or override_multiplier not in (None, "")

    if not series_lookup_succeeded and not has_override:
        return EffectiveFee(
            fee_type=None,
            fee_multiplier=None,
            source=FeeSource.UNAVAILABLE,
            support=FeeModelSupport.METADATA_UNAVAILABLE,
            unavailable_reason=(
                "the parent series could not be fetched, so its fee metadata is unknown; "
                "a fee is not estimated from a failed lookup"
            ),
        )

    series = series or {}
    if has_override:
        # An override may set only one of the two fields; the other still
        # comes from the series, which is what "layered on top of the
        # parent series fee" means.
        fee_type = override_type if override_type not in (None, "") else series.get("fee_type")
        fee_multiplier = (
            override_multiplier if override_multiplier not in (None, "") else series.get("fee_multiplier")
        )
        source = FeeSource.EVENT_OVERRIDE
    else:
        fee_type = series.get("fee_type")
        fee_multiplier = series.get("fee_multiplier")
        source = FeeSource.SERIES

    if fee_type in (None, ""):
        return EffectiveFee(
            fee_type=None,
            fee_multiplier=_as_float(fee_multiplier),
            source=FeeSource.UNAVAILABLE,
            support=FeeModelSupport.METADATA_UNAVAILABLE,
            unavailable_reason=(
                "no effective fee_type could be established from the event override or the parent "
                "series; the field is normally present, so its absence is a capture diagnostic"
            ),
        )

    multiplier = _as_float(fee_multiplier)
    if multiplier is None:
        return EffectiveFee(
            fee_type=str(fee_type),
            fee_multiplier=None,
            source=FeeSource.UNAVAILABLE,
            support=FeeModelSupport.METADATA_UNAVAILABLE,
            unavailable_reason=(
                f"fee_type {fee_type!r} was established but no fee_multiplier was; a multiplier is "
                f"not assumed to be 1 because that would turn missing data into a plausible number"
            ),
        )

    resolved = EffectiveFee(
        fee_type=str(fee_type),
        fee_multiplier=multiplier,
        source=source,
        support=FeeModelSupport.SUPPORTED,
    )
    if not resolved.is_quadratic:
        return EffectiveFee(
            fee_type=str(fee_type),
            fee_multiplier=multiplier,
            source=source,
            support=FeeModelSupport.UNSUPPORTED_MODEL,
            unavailable_reason=(
                f"fee model {fee_type!r} is not a quadratic schedule and has no implemented formula "
                f"here; the type is reported verbatim and no amount is computed"
            ),
        )
    return resolved


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ceil_to_granularity(amount: float, granularity: float = FEE_GRANULARITY_DOLLARS) -> float:
    """Round UP to a multiple of `granularity`, defaulting to $0.000001.

    The 1e-9 nudge absorbs binary float representation error: without it a
    value that is mathematically exactly on the grid can land a hair above
    it and get pushed a whole increment higher."""
    if granularity <= 0:
        raise ValueError("granularity must be positive")
    steps = math.ceil(amount / granularity - 1e-9)
    return round(steps * granularity, 10)


def floor_to_precision(amount: float, precision: float) -> float:
    """Round DOWN to a multiple of `precision` (the docs'
    `floor_precision`). Applied to a signed balance change, so it floors
    toward negative infinity -- which for a buyer's negative revenue means
    away from zero, i.e. the user is charged the fuller amount."""
    if precision <= 0:
        raise ValueError("precision must be positive")
    return round(math.floor(amount / precision + 1e-9) * precision, 10)


def quadratic_model_fee(
    price: float | None, contracts: float = 1.0, fee_multiplier: float | None = None
) -> float | None:
    """The UNROUNDED model fee: multiplier * 0.07 * C * P * (1 - P).

    Returns None for an unusable price -- never 0, which would read as
    free. `fee_multiplier` is required by the caller's resolution step;
    None here means "not established" and yields None rather than 1."""
    if price is None or not (0.0 <= price <= 1.0):
        return None
    if fee_multiplier is None:
        return None
    return fee_multiplier * QUADRATIC_FEE_COEFFICIENT * contracts * price * (1.0 - price)


def model_trade_fee(
    price: float | None, contracts: float = 1.0, fee_multiplier: float | None = None
) -> float | None:
    """The documented TRADE FEE: the model fee rounded UP to $0.000001.

    Verified against the docs' worked example: a 1-contract fill at
    P=$0.055 has model fee $0.00363825 and trade fee $0.003639."""
    model = quadratic_model_fee(price, contracts, fee_multiplier)
    if model is None:
        return None
    return ceil_to_granularity(model)


@dataclass(frozen=True)
class RoundingComponents:
    """One fill's rounding arithmetic, per the documented mechanics.

    NOT published by the catalog -- `target_precision` is account-specific.
    This exists so the module can be tested against the docs' own worked
    example."""

    trade_fee: float
    aligned_change: float
    rounding_fee: float

    @property
    def trade_plus_rounding(self) -> float:
        return round(self.trade_fee + self.rounding_fee, 10)


def rounding_fee_components(
    signed_revenue: float, model_fee: float, target_precision: float
) -> RoundingComponents:
    """Implements the documented rounding mechanics:

        trade_fee      = ceil_6dp(model_fee)
        aligned_change = floor_precision(revenue - trade_fee)
        rounding_fee   = (revenue - trade_fee) - aligned_change

    `signed_revenue` is negative for buyers."""
    trade_fee = ceil_to_granularity(model_fee)
    net_change = signed_revenue - trade_fee
    aligned_change = floor_to_precision(net_change, target_precision)
    return RoundingComponents(
        trade_fee=trade_fee,
        aligned_change=aligned_change,
        rounding_fee=round(net_change - aligned_change, 10),
    )


def rebate_schedule(
    per_fill_rounding: list[float], target_precision: float
) -> list[tuple[float, float, float]]:
    """Walk the per-order fee accumulator across an order's fills.

    Returns one (accumulated_before_rebate, rebate, carried_forward) per
    fill. Rebates are paid in whole increments of the target precision;
    accumulation carries across fills regardless of taker/maker."""
    carried = 0.0
    rows: list[tuple[float, float, float]] = []
    for added in per_fill_rounding:
        before = round(carried + added, 10)
        increments = math.floor(before / target_precision + 1e-9)
        rebate = round(increments * target_precision, 10)
        carried = round(before - rebate, 10)
        rows.append((before, rebate, carried))
    return rows


def fee_block(
    fee: EffectiveFee,
    yes_ask: float | None,
    yes_mid: float | None,
    contracts: float = 1.0,
) -> dict[str, Any]:
    """The self-describing fee block published per contract.

    *** WHY THE ASK IS THE HEADLINE AND THE MID IS NOT ***
    A taker buying YES pays the ASK, so the ask-based trade fee is the one
    that bears on "does this price justify the thesis". The mid-based
    figure is a market mechanic -- useful for comparing contracts, but it
    is not the fee on any order you can actually send, and publishing it
    as the headline let it masquerade as one."""
    computable = fee.computable
    block: dict[str, Any] = {
        "model": fee.fee_type,
        "multiplier": fee.fee_multiplier,
        "source": str(fee.source),
        "support": str(fee.support),
        "maker_fee_applies": fee.maker_fee_applies,
        "is_net_fee": False,
        "excludes": list(NET_FEE_EXCLUDES),
        "per_contracts": contracts,
        "formula": MODEL_TRADE_FEE_FORMULA if computable else None,
        "model_trade_fee_at_yes_ask": model_trade_fee(yes_ask, contracts, fee.fee_multiplier)
        if computable
        else None,
        "model_trade_fee_at_yes_mid": model_trade_fee(yes_mid, contracts, fee.fee_multiplier)
        if computable
        else None,
        "basis_yes_ask": yes_ask,
        "basis_yes_mid": yes_mid,
        "unavailable_reason": fee.unavailable_reason,
        "note": NET_FEE_NOTE,
    }
    return block
