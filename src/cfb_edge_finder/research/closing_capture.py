"""Prospective CLOSING capture: market-status eligibility and the
closed vocabulary of closing outcomes (mission sections 9, 14, 18).

*** HOW THIS DIFFERS FROM research/closing.py ***
`closing.py` GRADES a closing line after the fact -- given rows already in
the corpus, which one is the closing quote and how good is it. That module
is unchanged and still the right tool for retrospective grading.

This module is about the PROSPECTIVE half: at scan time, inside the
strictly-pre-kickoff CLOSING window (research/timing.py), decide whether
this market can legitimately produce a CLOSING row right now, and if not,
say exactly why in a vocabulary that never collapses to silence.

*** WHY CLOSING IS NEVER INFERRED FROM T_30 ***
Mission section 9 is explicit: do not infer closing from another
checkpoint. A T_30 row is a snapshot taken 15-45 minutes out; treating it
as "the close" silently redefines the most price-sensitive research
primitive in the corpus. If no valid executable quote exists in the
closing window, the correct research record is an explicit missing state
with a reason -- not a substituted neighbouring snapshot. Downstream
analysis can always CHOOSE to fall back to the nearest pregame row (that
is what `closing.py`'s NEAR_CLOSE grading is for), but that has to be a
visible analytical decision, not something persistence quietly did.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

EXECUTABLE_MARKET_STATUSES = frozenset({"active"})
"""The only Kalshi market status this repo treats as executable.

Deliberately an allow-list, not a deny-list of known-bad statuses: an
unfamiliar status string from Kalshi must fall through to
NOT_EXECUTABLE and be recorded as such, never be optimistically priced
because it did not happen to match a blocklist."""

NON_EXECUTABLE_CLOSED_STATUSES = frozenset({"closed", "finalized", "settled", "determined"})
"""Statuses meaning the market is over, not merely paused. Kept separate
from 'suspended'/'paused' only so the recorded reason is specific."""


class ClosingStatus(StrEnum):
    """Mission section 18: every supported market that reaches kickoff
    ends up in exactly one of these. There is no silent missing closing."""

    CLOSING_CAPTURED = "CLOSING_CAPTURED"
    CLOSING_PENDING = "CLOSING_PENDING"
    """Kickoff has not passed yet and the closing window has not closed --
    not an outcome, just "not resolved yet". Never a terminal state."""

    CLOSING_MISSING_MARKET_CLOSED = "CLOSING_MISSING_MARKET_CLOSED"
    CLOSING_MISSING_MARKET_SUSPENDED = "CLOSING_MISSING_MARKET_SUSPENDED"
    CLOSING_MISSING_API_FAILURE = "CLOSING_MISSING_API_FAILURE"
    CLOSING_MISSING_MAPPING_FAILURE = "CLOSING_MISSING_MAPPING_FAILURE"
    CLOSING_MISSING_NO_EXECUTABLE_QUOTE = "CLOSING_MISSING_NO_EXECUTABLE_QUOTE"
    CLOSING_MISSING_NO_SCAN_IN_WINDOW = "CLOSING_MISSING_NO_SCAN_IN_WINDOW"
    """Kickoff passed and no scan ever ran inside the closing window --
    a scheduler/coverage failure, distinct from "we looked and the market
    was not quotable". Keeping these apart is the whole point: one is our
    fault, the other is the market's."""

    CLOSING_NOT_APPLICABLE = "CLOSING_NOT_APPLICABLE"
    """The market was never eligible for a CLOSING row at all -- an
    unsupported population (e.g. FCS-vs-FCS), or a game that never
    reached kickoff (cancelled/postponed indefinitely)."""


TERMINAL_CLOSING_STATUSES = frozenset(ClosingStatus) - {ClosingStatus.CLOSING_PENDING}


MISSING_CLOSING_STATUSES = frozenset(
    {
        ClosingStatus.CLOSING_MISSING_MARKET_CLOSED,
        ClosingStatus.CLOSING_MISSING_MARKET_SUSPENDED,
        ClosingStatus.CLOSING_MISSING_API_FAILURE,
        ClosingStatus.CLOSING_MISSING_MAPPING_FAILURE,
        ClosingStatus.CLOSING_MISSING_NO_EXECUTABLE_QUOTE,
        ClosingStatus.CLOSING_MISSING_NO_SCAN_IN_WINDOW,
    }
)


def is_executable_status(market_status: str | None) -> bool:
    """Whether a Kalshi market status permits producing an executable
    research quote. `None` (status absent from the payload) is NOT
    treated as executable -- absence of evidence is not evidence of a
    tradeable market."""
    if market_status is None:
        return False
    return market_status.strip().lower() in EXECUTABLE_MARKET_STATUSES


def missing_reason_for_status(market_status: str | None) -> ClosingStatus:
    """The specific missing-closing reason implied by a non-executable
    market status."""
    normalized = (market_status or "").strip().lower()
    if normalized in NON_EXECUTABLE_CLOSED_STATUSES:
        return ClosingStatus.CLOSING_MISSING_MARKET_CLOSED
    if normalized in {"suspended", "paused", "halted"}:
        return ClosingStatus.CLOSING_MISSING_MARKET_SUSPENDED
    return ClosingStatus.CLOSING_MISSING_NO_EXECUTABLE_QUOTE


@dataclass(frozen=True)
class ClosingEligibility:
    """Whether a CLOSING row may be written for this market right now."""

    eligible: bool
    status: ClosingStatus
    detail: str


def evaluate_closing_eligibility(
    *,
    market_status: str | None,
    executable_yes_price: float | None,
    executable_no_price: float | None,
    mapping_failed: bool,
    is_supported_population: bool,
    minutes_before_kickoff: float | None,
) -> ClosingEligibility:
    """The single gate a prospective CLOSING capture must pass.

    Ordering matters and is deliberate: a market that is both unmapped and
    suspended is reported as a MAPPING failure, because that is the
    upstream cause a human would need to fix first."""
    if not is_supported_population:
        return ClosingEligibility(
            False,
            ClosingStatus.CLOSING_NOT_APPLICABLE,
            "market is not in a population this repo prices (e.g. not FBS-vs-FBS)",
        )
    if mapping_failed:
        return ClosingEligibility(
            False,
            ClosingStatus.CLOSING_MISSING_MAPPING_FAILURE,
            "market could not be mapped to a scheduled game, so no closing row can be attributed",
        )
    if minutes_before_kickoff is None:
        return ClosingEligibility(
            False,
            ClosingStatus.CLOSING_NOT_APPLICABLE,
            "kickoff time unknown -- no closing window can be defined",
        )
    if minutes_before_kickoff <= 0:
        # Hard pre-kickoff enforcement, independent of the timing module,
        # so a caller that computes its own window can never write a
        # post-kickoff CLOSING row through this gate either.
        return ClosingEligibility(
            False,
            ClosingStatus.CLOSING_MISSING_NO_SCAN_IN_WINDOW,
            f"clock is {abs(minutes_before_kickoff):.1f} min past kickoff -- CLOSING is never backfilled",
        )
    if not is_executable_status(market_status):
        reason = missing_reason_for_status(market_status)
        return ClosingEligibility(False, reason, f"market status {market_status!r} is not executable")
    if executable_yes_price is None and executable_no_price is None:
        return ClosingEligibility(
            False,
            ClosingStatus.CLOSING_MISSING_NO_EXECUTABLE_QUOTE,
            "market is active but produced no executable YES or NO price",
        )
    return ClosingEligibility(
        True,
        ClosingStatus.CLOSING_CAPTURED,
        f"executable pregame quote {minutes_before_kickoff:.1f} min before kickoff",
    )


@dataclass(frozen=True)
class ClosingAccountingRow:
    """One market's closing outcome, for the completeness accounting
    mission section 18 requires."""

    game_id: str
    market_ticker: str
    status: ClosingStatus
    detail: str
    kickoff_utc: datetime | None
    resolved_at: datetime | None = None


def resolve_closing_status(
    *,
    has_closing_row: bool,
    kickoff_utc: datetime | None,
    now: datetime,
    is_supported_population: bool,
    last_observed_market_status: str | None,
    mapping_failed: bool,
    api_failed: bool = False,
) -> ClosingEligibility:
    """Post-hoc classification for the accounting pass: given everything
    known about a market after its game has (or has not) kicked off,
    which single ClosingStatus describes it?

    `has_closing_row` short-circuits everything -- if a CLOSING row exists
    the outcome is CAPTURED regardless of what the market did afterwards."""
    if has_closing_row:
        return ClosingEligibility(True, ClosingStatus.CLOSING_CAPTURED, "CLOSING row present in the corpus")
    if not is_supported_population:
        return ClosingEligibility(
            False, ClosingStatus.CLOSING_NOT_APPLICABLE, "not a priced population"
        )
    if kickoff_utc is None:
        return ClosingEligibility(
            False, ClosingStatus.CLOSING_NOT_APPLICABLE, "no kickoff time -- game never scheduled to start"
        )
    if now < kickoff_utc:
        return ClosingEligibility(
            False, ClosingStatus.CLOSING_PENDING, "kickoff has not passed yet; closing still resolvable"
        )
    if api_failed:
        return ClosingEligibility(
            False, ClosingStatus.CLOSING_MISSING_API_FAILURE, "a data-source failure prevented closing capture"
        )
    if mapping_failed:
        return ClosingEligibility(
            False, ClosingStatus.CLOSING_MISSING_MAPPING_FAILURE, "market never mapped to a scheduled game"
        )
    if last_observed_market_status is not None and not is_executable_status(last_observed_market_status):
        reason = missing_reason_for_status(last_observed_market_status)
        return ClosingEligibility(
            False, reason, f"last observed market status was {last_observed_market_status!r}"
        )
    return ClosingEligibility(
        False,
        ClosingStatus.CLOSING_MISSING_NO_SCAN_IN_WINDOW,
        "kickoff passed with no CLOSING row and no explaining market condition -- scan coverage gap",
    )
