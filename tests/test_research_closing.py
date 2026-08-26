"""Mission section 10-11: closing definition, quality grading, fallback."""

from __future__ import annotations

import pytest

from cfb_edge_finder.research.closing import (
    EXACT_MAX_MINUTES,
    NEAR_CLOSE_MAX_MINUTES,
    ClosingCandidate,
    classify_closing_quality,
    select_closing_candidate,
)


def test_exact_within_threshold():
    assert classify_closing_quality(5.0) == "EXACT"
    assert classify_closing_quality(EXACT_MAX_MINUTES) == "EXACT"


def test_near_close_beyond_exact_within_near():
    assert classify_closing_quality(EXACT_MAX_MINUTES + 1) == "NEAR_CLOSE"
    assert classify_closing_quality(NEAR_CLOSE_MAX_MINUTES) == "NEAR_CLOSE"


def test_missed_beyond_near_window():
    assert classify_closing_quality(NEAR_CLOSE_MAX_MINUTES + 1) == "MISSED"


def test_negative_minutes_rejected_never_post_kickoff():
    with pytest.raises(ValueError):
        classify_closing_quality(-1.0)


def _candidate(minutes: float, status: str = "scheduled", price: float | None = 0.55) -> ClosingCandidate:
    return ClosingCandidate(
        market_ticker="MKT-1", captured_at=None, game_status_at_capture=status,
        executable_yes_price=price, minutes_before_kickoff=minutes,
    )


def test_select_closing_prefers_nearest_to_kickoff():
    result = select_closing_candidate([_candidate(60.0), _candidate(5.0), _candidate(30.0)])
    assert result.captured is True
    assert result.quality == "EXACT"
    assert result.minutes_to_kickoff == 5.0


def test_select_closing_approximate_within_fallback_window():
    result = select_closing_candidate([_candidate(45.0)])
    assert result.captured is True
    assert result.quality == "NEAR_CLOSE"
    assert "approximate" in result.detail.lower()


def test_select_closing_missed_beyond_fallback_window():
    result = select_closing_candidate([_candidate(120.0)])
    assert result.captured is False
    assert result.quality == "MISSED"


def test_select_closing_ignores_post_kickoff_rows():
    result = select_closing_candidate([_candidate(-5.0)])
    assert result.captured is False


def test_select_closing_ignores_non_scheduled_status():
    result = select_closing_candidate([_candidate(5.0, status="in_progress")])
    assert result.captured is False


def test_select_closing_ignores_missing_executable_price():
    result = select_closing_candidate([_candidate(5.0, price=None)])
    assert result.captured is False


def test_select_closing_empty_candidates_is_missed_not_a_crash():
    result = select_closing_candidate([])
    assert result.captured is False
    assert result.quality == "MISSED"


def test_never_pretends_approximate_is_exact():
    result = select_closing_candidate([_candidate(50.0)])
    assert result.quality != "EXACT"
    assert result.quality == "NEAR_CLOSE"
