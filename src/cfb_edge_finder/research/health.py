"""Milestone E, Part H: capture health checks and coverage-collapse
detection (mission sections 19-20).

`CaptureHealthReport` is what one scheduled run emits; `evaluate_collapse`
compares it against a trailing baseline and returns explicit diagnostics
-- never a bare pass/fail boolean, and never a blanket "any count change
is an error" rule (mission section 20 explicitly warns against that).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Severity(StrEnum):
    INFO = "info"
    WARNING = "warning"
    HIGH = "high"


@dataclass(frozen=True)
class Diagnostic:
    severity: Severity
    code: str
    detail: str


@dataclass
class CaptureHealthReport:
    """Mutable accumulator -- a single scan run builds one of these up
    field-by-field as it scans games/markets/captures, then hands the
    finished object to `evaluate_collapse`/`should_fail_run`. Diagnostics
    themselves (`Diagnostic`) stay frozen/immutable value objects."""

    games_scanned: int = 0
    markets_scanned: int = 0
    events_scanned: int = 0
    """Distinct Kalshi events discovered across the scanned series. One
    unresolved EVENT fans its failure across every contract in its ladder
    (mapping_failures counts markets), so event-level counts are the
    companion metric that says how much of the universe actually failed
    rather than how many contracts sat under the failures."""
    events_mapping_failed: int = 0
    """Events whose identity mapping was a genuine failure
    (scan_logic.is_genuine_mapping_failure) -- the event-level numerator
    matching mapping_failures' market-level one."""
    markets_unsupported_population: int = 0
    """Markets under events classified as deliberately-declined
    populations (FCS_VS_FCS / NON_FBS_PARTICIPANT --
    scan_logic.is_unsupported_population): counted so the report
    accounts them explicitly instead of leaving them implied by
    subtraction, and NEVER counted in mapping_failures."""
    supported_markets: int = 0
    captures_due: int = 0
    captures_written: int = 0
    captures_skipped_already_present: int = 0
    missed_windows: int = 0
    mapping_failures: int = 0
    stale_schedule_failures: int = 0
    closing_due: int = 0
    closing_captured: int = 0
    closing_missing: int = 0
    api_failures: int = 0
    persistence_failures: int = 0
    diagnostics: tuple[Diagnostic, ...] = field(default_factory=tuple)

    @property
    def has_high_severity(self) -> bool:
        return any(d.severity == Severity.HIGH for d in self.diagnostics)


# Thresholds -- generous, documented, and explicit rather than implicit
# "any drop is bad." A real season slate varies week to week (bye weeks,
# fewer games), so these compare RATIOS against a baseline, not raw counts.
SUPPORTED_MARKET_DROP_WARN_RATIO = 0.5
"""supported_markets this run < 50% of the trailing baseline -- WARNING
(could be a genuinely light week, e.g. many bye weeks)."""
SUPPORTED_MARKET_DROP_HIGH_RATIO = 0.15
"""supported_markets this run < 15% of the trailing baseline -- HIGH.
Combined with markets_scanned staying roughly flat, this indicates the
mapping/parsing/pricing pipeline broke, not that the slate shrank."""
MAPPING_FAILURE_RATE_WARN = 0.15
MAPPING_FAILURE_RATE_HIGH = 0.40
PERSISTENCE_WRITE_MISMATCH_HIGH = True
"""persistence writes actually completed != captures the run intended to
write (excluding already-present skips) is always HIGH -- a season-long
corpus with unexplained missing rows is a data-integrity failure by
definition, not a threshold judgment call."""


def evaluate_collapse(current: CaptureHealthReport, baseline_supported_markets: int | None) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    if current.markets_scanned == 0:
        diagnostics.append(
            Diagnostic(
                Severity.HIGH, "zero_markets_scanned", "Kalshi returned zero markets across every scanned series"
            )
        )
    if current.games_scanned == 0:
        diagnostics.append(
            Diagnostic(Severity.HIGH, "zero_games_scanned", "CFBD schedule scan returned zero not-started games")
        )

    if baseline_supported_markets is not None and baseline_supported_markets > 0:
        ratio = current.supported_markets / baseline_supported_markets
        if ratio < SUPPORTED_MARKET_DROP_HIGH_RATIO:
            diagnostics.append(
                Diagnostic(
                    Severity.HIGH,
                    "supported_market_collapse",
                    f"supported_markets={current.supported_markets} is {ratio:.0%} of baseline "
                    f"{baseline_supported_markets} -- below the {SUPPORTED_MARKET_DROP_HIGH_RATIO:.0%} HIGH threshold",
                )
            )
        elif ratio < SUPPORTED_MARKET_DROP_WARN_RATIO:
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "supported_market_drop",
                    f"supported_markets={current.supported_markets} is {ratio:.0%} of baseline "
                    f"{baseline_supported_markets}",
                )
            )

    if current.markets_scanned > 0:
        mapping_failure_rate = current.mapping_failures / current.markets_scanned
        event_context = ""
        if current.events_scanned > 0:
            # Companion event-level rate: one failed event fans out
            # across its whole contract ladder, so the market-level rate
            # alone cannot say whether 40% of the universe failed or one
            # big ladder did. Context only -- the threshold decision
            # stays on the market-level rate, unchanged.
            event_rate = current.events_mapping_failed / current.events_scanned
            event_context = (
                f" (events: {current.events_mapping_failed}/{current.events_scanned} = {event_rate:.0%}; "
                f"markets_unsupported_population={current.markets_unsupported_population})"
            )
        if mapping_failure_rate >= MAPPING_FAILURE_RATE_HIGH:
            diagnostics.append(
                Diagnostic(
                    Severity.HIGH,
                    "mapping_failure_rate_high",
                    f"mapping_failures/markets_scanned = {mapping_failure_rate:.0%}{event_context}",
                )
            )
        elif mapping_failure_rate >= MAPPING_FAILURE_RATE_WARN:
            diagnostics.append(
                Diagnostic(
                    Severity.WARNING,
                    "mapping_failure_rate_elevated",
                    f"mapping_failures/markets_scanned = {mapping_failure_rate:.0%}{event_context}",
                )
            )

    expected_new_writes = current.captures_due - current.captures_skipped_already_present
    if expected_new_writes > 0 and current.captures_written != expected_new_writes:
        diagnostics.append(
            Diagnostic(
                Severity.HIGH,
                "persistence_write_count_mismatch",
                f"expected {expected_new_writes} new writes (captures_due - already_present) but "
                f"captures_written={current.captures_written}",
            )
        )

    # Closing is the most time-sensitive primitive the collector produces
    # and the only one that can never be recovered after kickoff, so a
    # closing capture that was DUE and did not land is escalated on its
    # own rather than being averaged into the general capture counts.
    if current.closing_due > 0 and current.closing_captured < current.closing_due:
        missed = current.closing_due - current.closing_captured
        diagnostics.append(
            Diagnostic(
                Severity.HIGH,
                "closing_capture_shortfall",
                f"{missed} of {current.closing_due} due CLOSING capture(s) did not land -- "
                f"closing lines cannot be recovered after kickoff",
            )
        )
    if current.closing_missing > 0:
        diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                "closing_missing_recorded",
                f"{current.closing_missing} market(s) in the closing window could not produce a CLOSING "
                f"row; each has an explicit recorded reason in the capture-state log",
            )
        )

    if current.api_failures > 0:
        diagnostics.append(
            Diagnostic(Severity.HIGH, "api_failures", f"{current.api_failures} data-source call(s) failed")
        )

    if current.persistence_failures > 0:
        diagnostics.append(
            Diagnostic(
                Severity.HIGH, "persistence_failures", f"{current.persistence_failures} persistence write failure(s)"
            )
        )
    if current.stale_schedule_failures > 0:
        diagnostics.append(
            Diagnostic(
                Severity.WARNING,
                "stale_schedule_failures",
                f"{current.stale_schedule_failures} capture(s) rejected by the stale-schedule guard",
            )
        )

    return diagnostics


def should_fail_run(diagnostics: list[Diagnostic]) -> bool:
    """The run should fail loudly (non-zero exit) on any HIGH-severity
    diagnostic -- mission section 19's "fail loud on high-severity
    data-integrity issues.\""""
    return any(d.severity == Severity.HIGH for d in diagnostics)
