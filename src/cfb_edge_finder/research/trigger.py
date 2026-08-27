"""Trigger provenance, checkpoint deadlines, and trigger-health severity.

*** WHY THIS EXISTS ***

GitHub's scheduled cron is not dependable enough to protect CLOSING.
Measured on 2026-08-27, against an HOURLY cron, consecutive scheduled
collector runs arrived at gaps of 64, 71, 52, 61, 54, 56, 66, 80, 49, 95,
144, 171, 296 and 653 minutes; after the 10-minute cadence merged at
18:08Z, zero scheduled runs fired in the next three hours.

Every other checkpoint tolerates that. T_7D/T_3D/T_24H/T_6H are hours or
days wide, and T_90/T_60/T_30 are 60/30/30 minutes wide and may still be
captured late inside their window. CLOSING cannot: it is 14 minutes wide,
strictly pre-kickoff, and once the ball is kicked the closing line is
gone permanently. A 95-minute gap -- the *median-ish* bad case above, not
the worst -- silently destroys it.

So the deadline that matters is not "has the collector run recently" but
"will the collector run again BEFORE the closing window of the next
supported kickoff closes". That is what this module computes, from the
real schedule and the real window definitions in research/timing.py,
rather than from a fixed staleness constant.

Nothing here prices, qualifies, or recommends anything. It decides when
the collector should be invoked, never what the collector should conclude.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum

from cfb_edge_finder.research.timing import (
    CLOSING,
    CLOSING_WINDOW_MINUTES,
    NUMERIC_TIMING_WINDOWS,
)


class TriggerType(StrEnum):
    """How a collector invocation was started. Operational metadata only:
    it is recorded on the run manifest and never enters a probability, a
    price, or an eligibility decision."""

    GITHUB_SCHEDULE = "GITHUB_SCHEDULE"
    """The `*/10` cron. Retained as an independent fallback."""

    EXTERNAL_SCHEDULE = "EXTERNAL_SCHEDULE"
    """A conductor run, or any external dispatcher, driving the canonical
    collector through workflow_dispatch."""

    MANUAL = "MANUAL"
    """A human pressing Run workflow. Emergency path, not the design."""

    UNKNOWN = "UNKNOWN"


def classify_trigger(event_name: str | None, actor: str | None) -> TriggerType:
    """Map a GitHub event + actor onto a trigger type.

    `workflow_dispatch` is ambiguous on its own -- the conductor and a
    human both use it -- so the actor disambiguates: a dispatch made with
    the workflow's own GITHUB_TOKEN is attributed to `github-actions`."""
    if event_name == "schedule":
        return TriggerType.GITHUB_SCHEDULE
    if event_name in ("workflow_dispatch", "repository_dispatch"):
        if actor and actor.startswith("github-actions"):
            return TriggerType.EXTERNAL_SCHEDULE
        return TriggerType.MANUAL
    if event_name is None:
        return TriggerType.UNKNOWN
    return TriggerType.UNKNOWN


class TriggerHealth(StrEnum):
    """Severity of the trigger layer, judged against football deadlines."""

    HEALTHY = "HEALTHY"
    """Recent enough collector activity to reach the next checkpoint."""

    WARN = "WARN"
    """Expected cadence missed, but no critical checkpoint is threatened
    yet -- there is still room for a late invocation to catch it."""

    HIGH = "HIGH"
    """A critical checkpoint is approaching and there has been no recent
    successful collector invocation."""

    MISSED = "MISSED"
    """A checkpoint passed with no collector opportunity inside it. For
    CLOSING this is permanent."""


@dataclass(frozen=True)
class Checkpoint:
    """One label becoming due for one game, with the instant after which
    it can no longer be captured."""

    game_id: str
    label: str
    kickoff: datetime
    opens_at: datetime
    closes_at: datetime
    recoverable: bool
    """True for the numeric buckets, which a late run may still catch
    inside their window. False for CLOSING, which is gone at kickoff."""

    def slack_seconds(self, now: datetime) -> float:
        """Time left before this checkpoint can never be captured."""
        return (self.closes_at - now).total_seconds()

    def is_open(self, now: datetime) -> bool:
        return self.opens_at <= now < self.closes_at


def checkpoints_for_kickoff(game_id: str, kickoff: datetime, captured_labels: set[str]) -> list[Checkpoint]:
    """Every not-yet-captured checkpoint for one game, in deadline order.

    Windows come from research/timing.py rather than being restated, so
    the trigger layer cannot drift out of agreement with the collector's
    own due-label resolution."""
    result: list[Checkpoint] = []
    for window in NUMERIC_TIMING_WINDOWS:
        if window.label in captured_labels:
            continue
        result.append(
            Checkpoint(
                game_id=game_id,
                label=window.label,
                kickoff=kickoff,
                opens_at=kickoff - timedelta(hours=window.upper_bound_hours),
                closes_at=kickoff - timedelta(hours=window.lower_bound_hours),
                recoverable=True,
            )
        )
    if CLOSING not in captured_labels:
        result.append(
            Checkpoint(
                game_id=game_id,
                label=CLOSING,
                kickoff=kickoff,
                opens_at=kickoff - timedelta(minutes=CLOSING_WINDOW_MINUTES),
                closes_at=kickoff,
                recoverable=False,
            )
        )
    return sorted(result, key=lambda c: c.closes_at)


def next_checkpoint(
    checkpoints: list[Checkpoint], now: datetime, *, only_unrecoverable: bool = False
) -> Checkpoint | None:
    """The soonest checkpoint whose deadline has not yet passed."""
    candidates = [c for c in checkpoints if c.closes_at > now and (c.recoverable is False or not only_unrecoverable)]
    return min(candidates, key=lambda c: c.closes_at, default=None)


def missed_checkpoints(
    checkpoints: list[Checkpoint], last_successful_run: datetime | None, now: datetime
) -> list[Checkpoint]:
    """Checkpoints whose window closed with no collector run inside it.

    A window counts as covered if a successful run happened at or after
    it opened. `last_successful_run` is the most recent one, so a window
    that closed before it is covered and everything after is not."""
    missed = []
    for checkpoint in checkpoints:
        if checkpoint.closes_at > now:
            continue
        if last_successful_run is not None and last_successful_run >= checkpoint.opens_at:
            continue
        missed.append(checkpoint)
    return missed


def assess_trigger_health(
    *,
    now: datetime,
    last_successful_run: datetime | None,
    checkpoints: list[Checkpoint],
    trigger_interval_seconds: float,
    max_dispatch_latency_seconds: float,
    collector_runtime_seconds: float,
) -> tuple[TriggerHealth, str]:
    """Severity of the trigger layer right now.

    The judgement is deliberately deadline-relative, not a fixed staleness
    threshold: being 40 minutes quiet is fine at 3am on a Tuesday and is
    an emergency 12 minutes before a kickoff. The comparison is therefore
    "can one more invocation still land inside the next window", using the
    real interval, a pessimistic dispatch latency, and the measured
    collector runtime -- so the answer degrades honestly when any of those
    gets worse instead of staying green until it is too late."""
    if last_successful_run is None:
        return TriggerHealth.HIGH, "no successful collector run has ever been recorded"

    already_missed = missed_checkpoints(checkpoints, last_successful_run, now)
    if already_missed:
        worst = min(already_missed, key=lambda c: c.closes_at)
        return (
            TriggerHealth.MISSED,
            f"{worst.label} for {worst.game_id} closed at {worst.closes_at.isoformat()} "
            f"with no collector run inside its window"
            + ("" if worst.recoverable else " -- unrecoverable"),
        )

    # Worst case for one more capture to land: we have just missed a
    # trigger, so we wait a full interval, plus dispatch latency, plus the
    # collector's own runtime before the row is written.
    needed = trigger_interval_seconds + max_dispatch_latency_seconds + collector_runtime_seconds

    upcoming = next_checkpoint(checkpoints, now)
    if upcoming is None:
        quiet_for = (now - last_successful_run).total_seconds()
        if quiet_for > trigger_interval_seconds * 6:
            return (
                TriggerHealth.WARN,
                f"no checkpoint pending, but the collector has been quiet for {quiet_for / 60:.0f} min",
            )
        return TriggerHealth.HEALTHY, "no checkpoint pending and the collector is running"

    slack = upcoming.slack_seconds(now)
    if slack < needed:
        severity = TriggerHealth.HIGH if not upcoming.recoverable else TriggerHealth.WARN
        return (
            severity,
            f"{upcoming.label} for {upcoming.game_id} closes in {slack / 60:.1f} min, but a fresh "
            f"invocation needs {needed / 60:.1f} min worst case"
            + ("" if upcoming.recoverable else " -- CLOSING cannot be recovered after kickoff"),
        )

    quiet_for = (now - last_successful_run).total_seconds()
    if quiet_for > trigger_interval_seconds * 6:
        return (
            TriggerHealth.WARN,
            f"collector quiet for {quiet_for / 60:.0f} min; next deadline "
            f"({upcoming.label}) is still {slack / 60:.0f} min away",
        )
    return (
        TriggerHealth.HEALTHY,
        f"next deadline {upcoming.label} for {upcoming.game_id} in {slack / 60:.0f} min",
    )


# --- conductor pacing -----------------------------------------------------

CLOSING_GUARD_LEAD_MINUTES = 25.0
"""How far ahead of a kickoff the tight-cadence guard engages.

Derived, not picked: CLOSING opens 14 minutes before kickoff, and the
guard must already be running and have completed at least one full cycle
by then. 14 (window) + 5 (one tight interval) + ~1 (dispatch + collector
runtime) rounds up to 20; 25 leaves a further margin for a late start
without meaningfully increasing cost, because the guard only runs at all
in this narrow band before each kickoff."""

TIGHT_INTERVAL_SECONDS = 240.0
"""4 minutes. Inside a 14-minute window this guarantees at least three
opportunities even if one is lost entirely, and still leaves room for
dispatch latency and the ~55s full collector runtime."""


def guard_should_be_active(
    now: datetime, upcoming_kickoffs: list[datetime], lead_minutes: float = CLOSING_GUARD_LEAD_MINUTES
) -> bool:
    """True when some supported kickoff is inside the guard band.

    This is the whole cost-control story: the tight loop exists only in
    the ~25 minutes before each kickoff, which is exactly where cron
    failure is unrecoverable. Kickoffs cluster (noon, 3:30, 7:00), so
    overlapping bands collapse into a handful of short windows per day
    rather than running around the clock."""
    for kickoff in upcoming_kickoffs:
        if now < kickoff and (kickoff - now).total_seconds() <= lead_minutes * 60.0:
            return True
    return False


def seconds_until_guard_needed(
    now: datetime,
    upcoming_kickoffs: list[datetime],
    lead_minutes: float = CLOSING_GUARD_LEAD_MINUTES,
) -> float | None:
    """Seconds until the guard band opens for the next kickoff, or None
    if no future kickoff is known."""
    future = [k for k in upcoming_kickoffs if k > now]
    if not future:
        return None
    return max(0.0, (min(future) - now).total_seconds() - lead_minutes * 60.0)
