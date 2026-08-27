"""Mission sections 3, 9, 14, 18: closing semantics, market-status
handling, and closing completeness accounting.

The load-bearing property under test is NEGATIVE: there must be no input
under which a CLOSING row is produced from a post-kickoff clock, a
non-executable market, or a substituted neighbouring checkpoint.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.research import closing_capture, timing
from cfb_edge_finder.research.closing_capture import ClosingStatus

KICKOFF = datetime(2026, 9, 5, 23, 30, tzinfo=UTC)


def _eligibility(**overrides):
    kwargs = dict(
        market_status="active",
        executable_yes_price=0.55,
        executable_no_price=0.47,
        mapping_failed=False,
        is_supported_population=True,
        minutes_before_kickoff=8.0,
    )
    kwargs.update(overrides)
    return closing_capture.evaluate_closing_eligibility(**kwargs)


# --- The happy path, so the negatives below are not vacuous -------------


def test_active_executable_market_in_window_is_eligible():
    result = _eligibility()
    assert result.eligible is True
    assert result.status is ClosingStatus.CLOSING_CAPTURED


# --- Pre-kickoff enforcement --------------------------------------------


@pytest.mark.parametrize("minutes", [0.0, -0.1, -30.0, -600.0])
def test_closing_is_never_eligible_at_or_after_kickoff(minutes):
    """Hard enforcement independent of the timing module, so a caller
    computing its own window still cannot write a post-kickoff CLOSING."""
    result = _eligibility(minutes_before_kickoff=minutes)
    assert result.eligible is False
    assert result.status is ClosingStatus.CLOSING_MISSING_NO_SCAN_IN_WINDOW


def test_unknown_kickoff_is_not_applicable_rather_than_captured():
    result = _eligibility(minutes_before_kickoff=None)
    assert result.eligible is False
    assert result.status is ClosingStatus.CLOSING_NOT_APPLICABLE


# --- Market-status requirements -----------------------------------------


@pytest.mark.parametrize(
    "status,expected",
    [
        ("suspended", ClosingStatus.CLOSING_MISSING_MARKET_SUSPENDED),
        ("paused", ClosingStatus.CLOSING_MISSING_MARKET_SUSPENDED),
        ("halted", ClosingStatus.CLOSING_MISSING_MARKET_SUSPENDED),
        ("closed", ClosingStatus.CLOSING_MISSING_MARKET_CLOSED),
        ("finalized", ClosingStatus.CLOSING_MISSING_MARKET_CLOSED),
        ("settled", ClosingStatus.CLOSING_MISSING_MARKET_CLOSED),
        ("determined", ClosingStatus.CLOSING_MISSING_MARKET_CLOSED),
    ],
)
def test_non_executable_statuses_never_produce_a_closing_row(status, expected):
    result = _eligibility(market_status=status)
    assert result.eligible is False
    assert result.status is expected


def test_unknown_status_falls_through_to_non_executable():
    """Allow-list, not deny-list: a Kalshi status this repo has never seen
    must NOT be optimistically treated as tradeable."""
    result = _eligibility(market_status="some_new_kalshi_state")
    assert result.eligible is False
    assert result.status is ClosingStatus.CLOSING_MISSING_NO_EXECUTABLE_QUOTE
    assert closing_capture.is_executable_status("some_new_kalshi_state") is False


def test_absent_status_is_not_executable():
    assert closing_capture.is_executable_status(None) is False
    assert _eligibility(market_status=None).eligible is False


def test_active_but_unquotable_market_records_no_executable_quote():
    result = _eligibility(executable_yes_price=None, executable_no_price=None)
    assert result.eligible is False
    assert result.status is ClosingStatus.CLOSING_MISSING_NO_EXECUTABLE_QUOTE


def test_one_sided_quote_is_still_executable():
    assert _eligibility(executable_yes_price=0.6, executable_no_price=None).eligible is True
    assert _eligibility(executable_yes_price=None, executable_no_price=0.4).eligible is True


# --- Population / mapping ------------------------------------------------


def test_unsupported_population_is_not_applicable():
    result = _eligibility(is_supported_population=False)
    assert result.status is ClosingStatus.CLOSING_NOT_APPLICABLE


def test_mapping_failure_reported_ahead_of_market_status():
    """A market that is both unmapped AND suspended reports the MAPPING
    failure -- the upstream cause a human would fix first."""
    result = _eligibility(mapping_failed=True, market_status="suspended")
    assert result.status is ClosingStatus.CLOSING_MISSING_MAPPING_FAILURE


# --- Post-hoc accounting (section 18) ------------------------------------


def _resolve(**overrides):
    kwargs = dict(
        has_closing_row=False,
        kickoff_utc=KICKOFF,
        now=KICKOFF + timedelta(hours=1),
        is_supported_population=True,
        last_observed_market_status="active",
        mapping_failed=False,
        api_failed=False,
    )
    kwargs.update(overrides)
    return closing_capture.resolve_closing_status(**kwargs)


def test_existing_closing_row_short_circuits_to_captured():
    assert _resolve(has_closing_row=True, mapping_failed=True).status is ClosingStatus.CLOSING_CAPTURED


def test_before_kickoff_is_pending_not_missing():
    result = _resolve(now=KICKOFF - timedelta(hours=2))
    assert result.status is ClosingStatus.CLOSING_PENDING
    assert result.status not in closing_capture.MISSING_CLOSING_STATUSES


def test_api_failure_is_attributed_to_us_not_the_market():
    assert _resolve(api_failed=True).status is ClosingStatus.CLOSING_MISSING_API_FAILURE


def test_coverage_gap_is_distinct_from_market_condition():
    """The key distinction: an active market that reached kickoff with no
    CLOSING row is OUR scheduling failure, not the market's."""
    assert _resolve().status is ClosingStatus.CLOSING_MISSING_NO_SCAN_IN_WINDOW


def test_closed_market_is_attributed_to_the_market():
    assert _resolve(last_observed_market_status="closed").status is ClosingStatus.CLOSING_MISSING_MARKET_CLOSED


def test_every_status_is_terminal_except_pending():
    assert ClosingStatus.CLOSING_PENDING not in closing_capture.TERMINAL_CLOSING_STATUSES
    for status in ClosingStatus:
        if status is not ClosingStatus.CLOSING_PENDING:
            assert status in closing_capture.TERMINAL_CLOSING_STATUSES


def test_accounting_is_exhaustive_over_realistic_inputs():
    """Mission section 18: EVERY market that reaches kickoff must land in
    exactly one status -- no input combination may fall through."""
    seen = set()
    for has_row in (True, False):
        for supported in (True, False):
            for mapped_fail in (True, False):
                for api_fail in (True, False):
                    for status in ("active", "suspended", "closed", None, "weird"):
                        for offset in (-2, 2):
                            result = closing_capture.resolve_closing_status(
                                has_closing_row=has_row,
                                kickoff_utc=KICKOFF,
                                now=KICKOFF + timedelta(hours=offset),
                                is_supported_population=supported,
                                last_observed_market_status=status,
                                mapping_failed=mapped_fail,
                                api_failed=api_fail,
                            )
                            assert isinstance(result.status, ClosingStatus)
                            assert result.detail, "every classification must carry a reason"
                            seen.add(result.status)
    # The exercise above should reach a genuine spread of outcomes, not
    # collapse onto one catch-all.
    assert len(seen) >= 5, f"accounting collapsed onto too few statuses: {seen}"


# --- Closing is never substituted from a neighbouring checkpoint ---------


def test_t30_capture_does_not_satisfy_closing():
    """Mission section 9: do not infer closing from T_30. Having captured
    T_30 must leave CLOSING still due inside its own window."""
    now = KICKOFF - timedelta(minutes=8)
    assert timing.is_closing_due(kickoff_utc=KICKOFF, now=now, already_captured_labels={"T_30"}) is True


def test_closing_window_cannot_be_widened_into_t30_territory():
    """Guards the disjointness constant itself: if someone later widens
    CLOSING_WINDOW_MINUTES past T_30's near edge, this fails."""
    assert timing.CLOSING_WINDOW_MINUTES < min(w.lower_bound_hours * 60 for w in timing.NUMERIC_TIMING_WINDOWS)
