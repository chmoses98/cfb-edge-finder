"""Milestone D closure, items 3-4: research-only, cents-safe Kalshi fee
calculation against the CURRENT, VERIFIED official schedule.

*** VERIFICATION STATUS: VERIFIED_CURRENT ***
The prior hardening pass tried, and genuinely failed, to fetch
https://kalshi.com/regulatory/fee-schedule and
https://kalshi.com/docs/kalshi-fee-schedule.pdf directly (both blocked by
Kalshi's own bot-mitigation checkpoint from every attempt tried -- see
this module's git history for that evidence trail). This closure pass was
given the current document's content directly by the user, who
transcribed it from the official PDF:

    Document: "Fee Schedule for July 2026 -- 7.7.26 Update"
    Effective: July 7, 2026
    Source: https://kalshi.com/docs/kalshi-fee-schedule.pdf

    General immediately-matched (taker) fee:
        fees = round up(M x 0.07 x C x P x (1 - P))
    General maker fee:
        fees = round up(M x 0.0175 x C x P x (1 - P))
    where P = contract price in dollars, C = number of contracts, M = a
    series-specific multiplier, default 1 unless the schedule's own
    non-standard-fee table lists a series explicitly.

    "The general fee applies to all markets except listed non-standard
    products." The current non-standard-fee table explicitly lists:
        KXNCAAFGAME -- College Football Game -- Maker Multiplier 1 --
        Taker Multiplier 1
    KXNCAAFSPREAD and KXNCAAFTOTAL are NOT present in that table -- per
    the schedule's own fallback rule quoted above, the general default
    multiplier (M=1) applies to both.

This is the current, controlling schedule for CFB series as of this
closure pass -- `KALSHI_FEE_SCHEDULE_2026_07_07_TAKER`/`_MAKER` below are
stamped `verification_status="VERIFIED_CURRENT"`, not "legacy" or
"unverified". The prior pass's `LEGACY_UNVERIFIED_TAKER/MAKER_SCHEDULE`
constants have been removed entirely (not merely superseded) now that a
current schedule is actually confirmed -- keeping a dead "unverified
placeholder" around after verification succeeds would be exactly the
kind of speculative code this project avoids. Any HISTORICAL live-run
log that already printed `fee_schedule_version=legacy_unverified_taker_v1`
is an immutable past artifact (a GH Actions job log, never a file this
repository stores) and is not rewritten by this change -- only NEW
snapshots use the new, verified provenance below.

*** WHY CENTS, NOT FLOATS ***
`Decimal` throughout, at cent-level precision -- see
`calculate_fee_cents`'s own docstring for the ROUND_CEILING ("round up")
rounding semantics the schedule itself specifies.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_CEILING, Decimal


@dataclass(frozen=True)
class FeeScheduleVersion:
    """Provenance for exactly which fee schedule a calculation used."""

    version_label: str
    fee_type: str
    """"taker" (immediately-matched) or "maker"."""
    rate: Decimal
    effective_date: str
    """ISO date string -- the schedule's own stated effective date."""
    source: str
    verification_status: str
    """"VERIFIED_CURRENT" (read directly from a current official source,
    or supplied directly by the user with the document's own title/
    effective-date self-identification) or "UNVERIFIED". Never set to
    "VERIFIED_CURRENT" on inference, precedent, or another sport's
    behavior."""
    calculation_version: str
    notes: str


@dataclass(frozen=True)
class SeriesMultiplier:
    """One series' entry (or documented absence) in the schedule's own
    non-standard-fee table -- mission closure item 6's "verify series
    multipliers" requirement."""

    series_ticker: str
    taker_multiplier: Decimal
    maker_multiplier: Decimal
    listed_as_non_standard: bool
    evidence: str


CALCULATION_VERSION = "fee_schedule_v2_cents_decimal_ceiling_multiplier"

KALSHI_FEE_SCHEDULE_2026_07_07_TAKER = FeeScheduleVersion(
    version_label="kalshi_fee_schedule_2026_07_07_taker",
    fee_type="taker",
    rate=Decimal("0.07"),
    effective_date="2026-07-07",
    source='https://kalshi.com/docs/kalshi-fee-schedule.pdf ("Fee Schedule for July 2026 -- 7.7.26 Update")',
    verification_status="VERIFIED_CURRENT",
    calculation_version=CALCULATION_VERSION,
    notes=(
        "General immediately-matched trading fee: fees = round up(M x 0.07 x C x P x (1-P)). Applies to "
        "all markets except those explicitly listed in the schedule's own non-standard-fee table -- see "
        "CFB_SERIES_MULTIPLIERS / get_taker_multiplier for the CFB-specific multiplier lookup and "
        "fallback rule."
    ),
)

KALSHI_FEE_SCHEDULE_2026_07_07_MAKER = FeeScheduleVersion(
    version_label="kalshi_fee_schedule_2026_07_07_maker",
    fee_type="maker",
    rate=Decimal("0.0175"),
    effective_date="2026-07-07",
    source='https://kalshi.com/docs/kalshi-fee-schedule.pdf ("Fee Schedule for July 2026 -- 7.7.26 Update")',
    verification_status="VERIFIED_CURRENT",
    calculation_version=CALCULATION_VERSION,
    notes=(
        "General maker fee: fees = round up(M x 0.0175 x C x P x (1-P)). Not used anywhere in this "
        "research ledger: the capture pipeline is read-only and never places orders, so the maker/taker "
        "distinction is moot for a research-only fee estimate -- the taker schedule is used as the "
        "conservative reference throughout. Retained here for completeness/provenance only."
    ),
)

CFB_SERIES_MULTIPLIERS: dict[str, SeriesMultiplier] = {
    "KXNCAAFGAME": SeriesMultiplier(
        series_ticker="KXNCAAFGAME",
        taker_multiplier=Decimal(1),
        maker_multiplier=Decimal(1),
        listed_as_non_standard=True,
        evidence=(
            "Explicitly listed in the July 2026 (7.7.26 Update, effective 2026-07-07) official "
            "non-standard-fee table as 'KXNCAAFGAME -- College Football Game -- Maker Multiplier 1 -- "
            "Taker Multiplier 1' -- listed, but at the SAME value as the general default (M=1), so this "
            "listing changes nothing numerically. Recorded for provenance/evidence completeness, not "
            "because it alters the fee amount."
        ),
    ),
}
"""KXNCAAFSPREAD and KXNCAAFTOTAL are deliberately absent from this dict
-- per the schedule's own text ("the general fee applies to all markets
except listed non-standard products") and their own absence from the
current non-standard-fee table, `get_taker_multiplier` falls back to the
general default multiplier (1) for them, exactly like any other unlisted
series. This is the documented fallback rule, not an assumption."""

DEFAULT_MULTIPLIER = Decimal(1)


def get_taker_multiplier(series_ticker: str) -> tuple[Decimal, str]:
    """Returns (multiplier, evidence). See `CFB_SERIES_MULTIPLIERS`'s own
    docstring for why an absent series correctly falls back to the
    general default rather than raising or guessing."""
    entry = CFB_SERIES_MULTIPLIERS.get(series_ticker)
    if entry is not None:
        return entry.taker_multiplier, entry.evidence
    return (
        DEFAULT_MULTIPLIER,
        f"{series_ticker!r} is not present in the July 2026 non-standard-fee table -- per the schedule's "
        f"own text, the general default multiplier (M=1) applies.",
    )


def calculate_fee_cents(
    price_cents: int, contracts: int, schedule: FeeScheduleVersion, multiplier: Decimal = DEFAULT_MULTIPLIER
) -> int:
    """`round up(M x rate x C x P x (1-P))`, computed and rounded entirely
    in integer cents via `Decimal` -- never a plain float -- and rounded
    UP (ROUND_CEILING) to the next whole cent, matching the schedule's
    own "round up" formula exactly. `price_cents` must be an integer in
    [1, 99] (Kalshi's own tradeable price range -- a contract can never
    price at exactly 0 or 100 cents while still trading); raises
    ValueError outside that range or for `contracts < 1`, rather than
    silently producing a meaningless fee for an impossible price/quantity."""
    if not 1 <= price_cents <= 99:
        raise ValueError(f"price_cents must be in [1, 99], got {price_cents!r}")
    if contracts < 1:
        raise ValueError(f"contracts must be >= 1, got {contracts!r}")

    p = Decimal(price_cents) / Decimal(100)
    raw_dollars = multiplier * schedule.rate * Decimal(contracts) * p * (Decimal(1) - p)
    raw_cents = raw_dollars * Decimal(100)
    return int(raw_cents.to_integral_value(rounding=ROUND_CEILING))


def calculate_fee_dollars(
    price_cents: int, contracts: int, schedule: FeeScheduleVersion, multiplier: Decimal = DEFAULT_MULTIPLIER
) -> Decimal:
    """Same calculation as `calculate_fee_cents`, returned in dollars
    (still a `Decimal`, never a float) for callers that want the money-
    scale amount directly rather than cents."""
    return Decimal(calculate_fee_cents(price_cents, contracts, schedule, multiplier)) / Decimal(100)
