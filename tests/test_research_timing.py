"""Mission sections 5-9: timing-bucket windows, due/missed logic, stale
guard interplay with the scheduler."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from cfb_edge_finder.research import timing
from cfb_edge_finder.schemas.capture_state import CaptureState

KICKOFF = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)


def _hours_before(hours: float) -> datetime:
    return KICKOFF - timedelta(hours=hours)


def test_early_open_due_immediately_when_never_captured():
    due = timing.resolve_due_labels(kickoff_utc=KICKOFF, now=_hours_before(200), already_captured_labels=set())
    assert timing.EARLY_OPEN in due


def test_early_open_not_due_once_captured():
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=_hours_before(200), already_captured_labels={timing.EARLY_OPEN}
    )
    assert timing.EARLY_OPEN not in due


def test_t_7d_due_within_window():
    due = timing.resolve_due_labels(kickoff_utc=KICKOFF, now=_hours_before(168), already_captured_labels=set())
    assert "T_7D" in due


def test_t_7d_not_due_far_outside_window():
    due = timing.resolve_due_labels(kickoff_utc=KICKOFF, now=_hours_before(300), already_captured_labels=set())
    assert "T_7D" not in due


def test_numeric_bucket_missed_window_after_it_closes():
    # T_60 window is 45-75 minutes before kickoff; 10 minutes before kickoff is well past it.
    state = timing.classify_bucket_state(
        label="T_60", kickoff_utc=KICKOFF, now=_hours_before(1 / 6), captured=False
    )
    assert state == CaptureState.MISSED_WINDOW


def test_missed_window_is_never_fabricated_as_captured():
    due = timing.resolve_due_labels(kickoff_utc=KICKOFF, now=_hours_before(1 / 6), already_captured_labels=set())
    assert "T_60" not in due  # window already closed -- never silently captured late


def test_no_due_labels_once_game_started():
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=_hours_before(-1), already_captured_labels=set(), game_started=True
    )
    assert due == []


def test_no_due_labels_when_kickoff_unknown():
    due = timing.resolve_due_labels(kickoff_utc=None, now=datetime.now(UTC), already_captured_labels=set())
    assert due == []


def test_overlapping_windows_both_due_if_neither_captured():
    # 70 minutes before kickoff is inside BOTH T_90 (60-120min) and T_60 (45-75min).
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=_hours_before(70 / 60), already_captured_labels=set()
    )
    numeric_due = [label for label in due if label != timing.EARLY_OPEN]
    assert set(numeric_due) == {"T_90", "T_60"}


def test_overlapping_windows_sorted_nearest_target_first():
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=_hours_before(70 / 60), already_captured_labels={timing.EARLY_OPEN}
    )
    # 70min is 20min from T_90's 90min target, 10min from T_60's 60min target -- T_60 nearer.
    assert due == ["T_60", "T_90"]


def test_already_captured_bucket_excluded_even_if_still_in_window():
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=_hours_before(1.0), already_captured_labels={"T_60", timing.EARLY_OPEN}
    )
    assert "T_60" not in due


def test_resolve_all_bucket_states_covers_every_pregame_label():
    states = timing.resolve_all_bucket_states(
        kickoff_utc=KICKOFF, now=_hours_before(100), already_captured_labels=set()
    )
    assert set(states.keys()) == set(timing.ALL_PREGAME_LABELS)


def test_captured_bucket_reports_captured_state():
    states = timing.resolve_all_bucket_states(
        kickoff_utc=KICKOFF, now=_hours_before(168), already_captured_labels={"T_7D"}
    )
    assert states["T_7D"] == CaptureState.CAPTURED


def test_not_yet_due_bucket_reports_not_yet_due():
    states = timing.resolve_all_bucket_states(
        kickoff_utc=KICKOFF, now=_hours_before(300), already_captured_labels=set()
    )
    assert states["T_7D"] == CaptureState.NOT_YET_DUE


def test_nearest_window_label_diagnostic():
    assert timing.nearest_window_label(1.0) == "T_60"
    assert timing.nearest_window_label(168.0) == "T_7D"


def test_closing_excluded_from_pregame_bucket_labels():
    assert timing.CLOSING not in timing.ALL_PREGAME_LABELS
