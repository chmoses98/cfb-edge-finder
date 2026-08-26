"""Milestone E, Part B: timing-bucket definitions and due/missed logic.

Every bucket except EARLY_OPEN and CLOSING is a (target_hours_before_kickoff,
half_width_hours) window: a checkpoint is DUE while elapsed time-to-kickoff
falls inside its window and it has not yet been captured; once elapsed
time moves past the window's near edge without a capture, it is
MISSED_WINDOW forever (mission section 7: "do not fabricate a T_60
snapshot later"). EARLY_OPEN has no numeric window -- it is due exactly
once, the first time a market is observed pregame, regardless of which
later windows have already opened or closed (mission section 5: "capture
the first valid state"). CLOSING is intentionally NOT modeled as a fixed
offset here -- see research/closing.py for its own, structural definition.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cfb_edge_finder.schemas.capture_state import CaptureState

EARLY_OPEN = "EARLY_OPEN"
CLOSING = "CLOSING"


@dataclass(frozen=True)
class TimingWindow:
    label: str
    target_hours_before_kickoff: float
    half_width_hours: float

    @property
    def upper_bound_hours(self) -> float:
        """Farther from kickoff -- the window has not opened yet beyond this."""
        return self.target_hours_before_kickoff + self.half_width_hours

    @property
    def lower_bound_hours(self) -> float:
        """Closer to kickoff -- the window has closed (missed) below this."""
        return self.target_hours_before_kickoff - self.half_width_hours


NUMERIC_TIMING_WINDOWS: tuple[TimingWindow, ...] = (
    TimingWindow("T_7D", target_hours_before_kickoff=168.0, half_width_hours=24.0),  # 6d-8d
    TimingWindow("T_3D", target_hours_before_kickoff=72.0, half_width_hours=12.0),  # 2.5d-3.5d
    TimingWindow("T_24H", target_hours_before_kickoff=24.0, half_width_hours=6.0),  # 18h-30h
    TimingWindow("T_6H", target_hours_before_kickoff=6.0, half_width_hours=2.0),  # 4h-8h
    TimingWindow("T_90", target_hours_before_kickoff=1.5, half_width_hours=0.5),  # 60-120min
    TimingWindow("T_60", target_hours_before_kickoff=1.0, half_width_hours=0.25),  # 45-75min
    TimingWindow("T_30", target_hours_before_kickoff=0.5, half_width_hours=0.25),  # 15-45min
)
"""Windows for T_90/T_60/T_30 deliberately overlap (e.g. 60-75min is both
T_90 and T_60's territory) -- a single scan landing in an overlap is
legitimately due for BOTH labels if neither has been captured yet (e.g.
after a scheduler outage), and each gets its own row under its own
deterministic key. `nearest_window_label` below is only used for
diagnostic/reporting "which bucket is this closest to" purposes, never to
suppress a genuinely-due capture."""

ALL_NUMERIC_LABELS: tuple[str, ...] = tuple(w.label for w in NUMERIC_TIMING_WINDOWS)
ALL_PREGAME_LABELS: tuple[str, ...] = (EARLY_OPEN, *ALL_NUMERIC_LABELS)
"""CLOSING is deliberately excluded -- it is resolved by research/closing.py
against the raw observation history, not scheduled as a due/missed bucket
the way the others are."""


def hours_before_kickoff(kickoff_utc: datetime, now: datetime) -> float:
    return (kickoff_utc - now).total_seconds() / 3600.0


def resolve_due_labels(
    *,
    kickoff_utc: datetime | None,
    now: datetime,
    already_captured_labels: set[str],
    game_started: bool = False,
) -> list[str]:
    """Labels due for capture RIGHT NOW, sorted nearest-target-first for
    numeric buckets (EARLY_OPEN always sorts first when due). Excludes
    anything already captured. Returns [] once the game has started
    (mission section 9's stale-schedule guard: never schedule a NEW
    pregame checkpoint for an already-started game) or when kickoff is
    unknown (nothing to schedule against yet)."""
    if game_started or kickoff_utc is None:
        return []

    due: list[str] = []
    if EARLY_OPEN not in already_captured_labels:
        due.append(EARLY_OPEN)

    elapsed = hours_before_kickoff(kickoff_utc, now)
    numeric_due = [
        w
        for w in NUMERIC_TIMING_WINDOWS
        if w.label not in already_captured_labels and w.lower_bound_hours <= elapsed <= w.upper_bound_hours
    ]
    numeric_due.sort(key=lambda w: abs(elapsed - w.target_hours_before_kickoff))
    due.extend(w.label for w in numeric_due)
    return due


def classify_bucket_state(
    *,
    label: str,
    kickoff_utc: datetime | None,
    now: datetime,
    captured: bool,
    game_started: bool = False,
) -> CaptureState:
    """The state of ONE numeric or EARLY_OPEN checkpoint right now --
    the basis for health reporting's missed-window counts (mission
    section 19) and for `resolve_all_bucket_states` below."""
    if captured:
        return CaptureState.CAPTURED
    if kickoff_utc is None:
        return CaptureState.NOT_YET_DUE
    if label == EARLY_OPEN:
        return CaptureState.MISSED_WINDOW if game_started else CaptureState.NOT_YET_DUE

    window = next((w for w in NUMERIC_TIMING_WINDOWS if w.label == label), None)
    if window is None:
        raise ValueError(f"unknown numeric timing label {label!r}")

    elapsed = hours_before_kickoff(kickoff_utc, now)
    if elapsed > window.upper_bound_hours:
        return CaptureState.NOT_YET_DUE
    if elapsed < window.lower_bound_hours:
        return CaptureState.MISSED_WINDOW
    return CaptureState.NOT_YET_DUE  # in-window but not yet captured this scan is reported by resolve_due_labels


def resolve_all_bucket_states(
    *,
    kickoff_utc: datetime | None,
    now: datetime,
    already_captured_labels: set[str],
    game_started: bool = False,
) -> dict[str, CaptureState]:
    return {
        label: classify_bucket_state(
            label=label,
            kickoff_utc=kickoff_utc,
            now=now,
            captured=label in already_captured_labels,
            game_started=game_started,
        )
        for label in ALL_PREGAME_LABELS
    }


def nearest_window_label(elapsed_hours: float) -> str | None:
    """Diagnostic-only nearest bucket for a given elapsed time -- never
    used to gate an actual capture decision."""
    if not NUMERIC_TIMING_WINDOWS:
        return None
    return min(NUMERIC_TIMING_WINDOWS, key=lambda w: abs(elapsed_hours - w.target_hours_before_kickoff)).label
