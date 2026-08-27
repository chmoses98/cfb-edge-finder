"""Milestone E, Part B: timing-bucket definitions and due/missed logic.

Every bucket except EARLY_OPEN and CLOSING is a (target_hours_before_kickoff,
half_width_hours) window: a checkpoint is DUE while elapsed time-to-kickoff
falls inside its window and it has not yet been captured; once elapsed
time moves past the window's near edge without a capture, it is
MISSED_WINDOW forever (mission section 7: "do not fabricate a T_60
snapshot later"). EARLY_OPEN has no numeric window -- it is due exactly
once, the first time a market is observed pregame, regardless of which
later windows have already opened or closed (mission section 5: "capture
the first valid state").

*** CLOSING (prospective collection milestone) ***
CLOSING used to be excluded from this module entirely: research/closing.py
SELECTED a closing row after the fact from whatever the hourly scanner
happened to have captured. That is not a closing line -- it is "the last
snapshot we happened to take," which on an hourly cadence could be 59
minutes stale. CLOSING is now a real, prospectively-captured checkpoint
with its own window, defined here and enforced by
research/closing_capture.py.

Its window is deliberately NOT a symmetric target +/- half_width like the
numeric buckets. It is anchored hard against kickoff:

    0 < minutes_to_kickoff <= CLOSING_WINDOW_MINUTES

- strictly pre-kickoff (a post-kickoff quote is never CLOSING, and CLOSING
  is never backfilled once kickoff passes -- see `is_closing_due`);
- disjoint from T_30, whose near edge is 15 minutes, so no scan can ever
  owe both CLOSING and T_30 for the same market at the same instant
  (mission section 21: tolerance windows must not cause overlapping
  labels). The numeric buckets DO deliberately overlap each other for
  outage catch-up -- see NUMERIC_TIMING_WINDOWS' own note -- but CLOSING
  is held apart from all of them on purpose, because a CLOSING row is a
  research primitive with stricter meaning than a catch-up snapshot.
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

CLOSING_WINDOW_MINUTES = 14.0
"""CLOSING is due while kickoff is more than 0 and at most this many
minutes away.

14, not 15, so the window is strictly DISJOINT from T_30 (near edge 15.0
min): at no instant can a market owe both. 14 is also wide enough to
survive real GitHub Actions scheduler drift on a 10-minute collection
cadence -- see docs/PROSPECTIVE_COLLECTION.md's cadence analysis -- while
staying tight enough that the captured quote is a genuine closing line
(research/closing.py grades anything within 10 minutes as EXACT)."""

CLOSING_MIN_MINUTES = 0.0
"""Strictly greater than zero: at or after kickoff, CLOSING is never due
and never backfilled. This is the hard pre-kickoff enforcement."""

ALL_NUMERIC_LABELS: tuple[str, ...] = tuple(w.label for w in NUMERIC_TIMING_WINDOWS)
ALL_PREGAME_LABELS: tuple[str, ...] = (EARLY_OPEN, *ALL_NUMERIC_LABELS, CLOSING)
"""Every label the scheduler can owe for a pregame market, CLOSING now
included. Ordering is intentional: EARLY_OPEN first, numeric buckets
farthest-to-nearest, CLOSING last."""


def minutes_before_kickoff(kickoff_utc: datetime, now: datetime) -> float:
    return (kickoff_utc - now).total_seconds() / 60.0


def is_closing_due(
    *,
    kickoff_utc: datetime | None,
    now: datetime,
    already_captured_labels: set[str],
    game_started: bool = False,
    window_minutes: float = CLOSING_WINDOW_MINUTES,
) -> bool:
    """CLOSING is due only inside the strictly-pre-kickoff closing window,
    and only once.

    Never due when: kickoff is unknown, the game has already started, the
    clock is at or past kickoff, CLOSING was already captured, or kickoff
    is still further out than the window. The "at or past kickoff" case is
    what makes CLOSING un-backfillable: a late scan does NOT get to record
    a CLOSING row from post-kickoff data, unlike the numeric buckets which
    a late run may legitimately still catch."""
    if game_started or kickoff_utc is None:
        return False
    if CLOSING in already_captured_labels:
        return False
    remaining = minutes_before_kickoff(kickoff_utc, now)
    return CLOSING_MIN_MINUTES < remaining <= window_minutes


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

    # CLOSING last: its window is disjoint from every numeric bucket, so
    # this can never append a label that duplicates one just added.
    if is_closing_due(
        kickoff_utc=kickoff_utc,
        now=now,
        already_captured_labels=already_captured_labels,
        game_started=game_started,
    ):
        due.append(CLOSING)
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

    if label == CLOSING:
        # CLOSING is MISSED the moment kickoff passes without a capture --
        # there is no later scan that can still legitimately record it,
        # which is exactly what distinguishes it from the numeric buckets.
        remaining = minutes_before_kickoff(kickoff_utc, now)
        if game_started or remaining <= CLOSING_MIN_MINUTES:
            return CaptureState.MISSED_WINDOW
        return CaptureState.NOT_YET_DUE

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
