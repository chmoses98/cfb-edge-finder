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


DECLARABLE_TRIGGER_SOURCES: frozenset[str] = frozenset(
    {TriggerType.EXTERNAL_SCHEDULE.value, TriggerType.MANUAL.value}
)
"""The only trigger types a CALLER may assert about itself.

Deliberately excludes GITHUB_SCHEDULE: cron provenance is something only
GitHub can establish, via the `schedule` event. If an external caller
could declare it, the health report could no longer tell "cron is alive"
from "something claimed cron was alive", and the staleness signal that
exists to catch a dead scheduler would become unfalsifiable."""


def classify_trigger(
    event_name: str | None, actor: str | None, declared_source: str | None = None
) -> TriggerType:
    """Map a GitHub event + actor (+ an optional self-declared source)
    onto a trigger type.

    `workflow_dispatch` is ambiguous on its own -- the conductor, a human,
    and an external scheduler all use it. The actor disambiguates the
    conductor, whose dispatch is made with the workflow's own
    GITHUB_TOKEN and so appears as `github-actions`.

    It cannot disambiguate an EXTERNAL scheduler: a dispatch made with a
    fine-grained PAT carries the token OWNER as the actor, so an
    independent cron service is indistinguishable from a human pressing
    Run. `declared_source` closes that gap -- the caller states what it
    is, restricted to DECLARABLE_TRIGGER_SOURCES so nothing can claim to
    be GitHub's own scheduler. An unrecognised or absent value falls back
    to inference, so the parameter can only ever refine the answer."""
    if declared_source and declared_source.strip().upper() in DECLARABLE_TRIGGER_SOURCES:
        return TriggerType(declared_source.strip().upper())
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


# --- positive schedule observability --------------------------------------
#
# *** WHY THIS EXISTS ***
#
# After the 2026-08-27 conductor incident, the repaired run was judged
# healthy by INFERENCE: the old "SCHEDULE LOOKUP FAILED" line was absent
# and the kickoff count was zero. Both of those were also true of the
# BROKEN conductor, which had no credential, fetched nothing, and
# reported zero. The two states were distinguishable only by reading raw
# logs for a message that was missing.
#
# Absence of an error is not evidence of success. These types make the
# success positive and structured: a healthy invocation states how many
# games it fetched, how many are supported, when the next supported
# kickoff is even when that is beyond the horizon, and which of the
# possible zero-states it is in.


class SchedulePlanningState(StrEnum):
    """Why the conductor saw what it saw. Every zero is a DIFFERENT zero."""

    FETCH_FAILED = "FETCH_FAILED"
    """The schedule source could not be reached or refused the request.
    The state the broken conductor was permanently in."""

    FETCH_SUCCESS_EMPTY_SCHEDULE = "FETCH_SUCCESS_EMPTY_SCHEDULE"
    """The request succeeded and returned no games at all. A season
    always has games, so zero records from a successful call means the
    source or the query is wrong -- operationally suspicious, and
    deliberately NOT the same as 'no games are near'."""

    FETCH_SUCCESS_NO_UPCOMING_GAMES = "FETCH_SUCCESS_NO_UPCOMING_GAMES"
    """Games exist, but all of them have already kicked off. End of
    season, or a stale schedule."""

    FETCH_SUCCESS_NO_SUPPORTED_GAMES = "FETCH_SUCCESS_NO_SUPPORTED_GAMES"
    """Upcoming games exist, but none are FBS-vs-FBS. Normal on a
    weeknight of FCS-only fixtures; a coverage problem if it persists
    through a Saturday."""

    FETCH_SUCCESS_SUPPORTED_OUTSIDE_HORIZON = "FETCH_SUCCESS_SUPPORTED_OUTSIDE_HORIZON"
    """Supported games exist but all lie beyond the protection horizon.
    THE post-incident state: the next supported kickoff was ~40.6h away
    against a 36h horizon. Healthy, and completely different from a
    failed fetch, which the old output could not express."""

    FETCH_SUCCESS_GUARDABLE_GAME_PRESENT = "FETCH_SUCCESS_GUARDABLE_GAME_PRESENT"
    """At least one supported game is inside the horizon."""

    @property
    def fetch_succeeded(self) -> bool:
        return self is not SchedulePlanningState.FETCH_FAILED

    @property
    def is_operationally_suspicious(self) -> bool:
        """States that warrant a warning even though nothing errored."""
        return self in (
            SchedulePlanningState.FETCH_FAILED,
            SchedulePlanningState.FETCH_SUCCESS_EMPTY_SCHEDULE,
        )


def classify_schedule(
    *,
    fetch_success: bool,
    total_games: int,
    upcoming_games: int,
    supported_upcoming_games: int,
    supported_inside_horizon: int,
) -> SchedulePlanningState:
    """Narrow the counts to exactly one state, most-severe first.

    Note what is NOT here: any threshold on "too few games". The only
    suspicious count is exactly zero from a successful request, which
    needs no magic number to justify -- a season with no games at all is
    degenerate by definition. Guessing an expected seasonal game count
    would invent a constant that would then need maintaining."""
    if not fetch_success:
        return SchedulePlanningState.FETCH_FAILED
    if total_games == 0:
        return SchedulePlanningState.FETCH_SUCCESS_EMPTY_SCHEDULE
    if upcoming_games == 0:
        return SchedulePlanningState.FETCH_SUCCESS_NO_UPCOMING_GAMES
    if supported_upcoming_games == 0:
        return SchedulePlanningState.FETCH_SUCCESS_NO_SUPPORTED_GAMES
    if supported_inside_horizon == 0:
        return SchedulePlanningState.FETCH_SUCCESS_SUPPORTED_OUTSIDE_HORIZON
    return SchedulePlanningState.FETCH_SUCCESS_GUARDABLE_GAME_PRESENT


@dataclass(frozen=True)
class ScheduleHealth:
    """Positive proof of what one planning invocation actually saw."""

    fetch_success: bool
    total_games: int
    upcoming_games: int
    supported_upcoming_games: int
    supported_inside_horizon: int
    horizon_end: datetime
    next_upcoming_kickoff: datetime | None = None
    next_supported_kickoff: datetime | None = None
    """The next supported kickoff ANYWHERE ahead, not only inside the
    horizon. Carrying it past the horizon is the whole point: it is what
    turns 'nothing to guard' into 'nothing yet, next one at 16:00Z'."""

    next_supported_kickoff_inside_horizon: datetime | None = None
    kickoffs_inside_horizon: tuple[datetime, ...] = ()
    detail: str = ""

    @property
    def state(self) -> SchedulePlanningState:
        return classify_schedule(
            fetch_success=self.fetch_success,
            total_games=self.total_games,
            upcoming_games=self.upcoming_games,
            supported_upcoming_games=self.supported_upcoming_games,
            supported_inside_horizon=self.supported_inside_horizon,
        )

    def as_telemetry(self) -> dict[str, object]:
        """Flat, log-and-heartbeat friendly. No raw game payloads: this
        is operational telemetry, not a copy of the schedule."""
        return {
            "schedule_fetch_success": self.fetch_success,
            "schedule_state": self.state.value,
            "total_schedule_games": self.total_games,
            "upcoming_games": self.upcoming_games,
            "supported_upcoming_games": self.supported_upcoming_games,
            "supported_inside_horizon": self.supported_inside_horizon,
            "next_upcoming_kickoff": self.next_upcoming_kickoff.isoformat() if self.next_upcoming_kickoff else None,
            "next_supported_kickoff": self.next_supported_kickoff.isoformat() if self.next_supported_kickoff else None,
            "next_supported_kickoff_inside_horizon": (
                self.next_supported_kickoff_inside_horizon.isoformat()
                if self.next_supported_kickoff_inside_horizon
                else None
            ),
            "horizon_end": self.horizon_end.isoformat(),
            "detail": self.detail,
        }

    def render(self) -> str:
        lines = [
            f"schedule fetch         : {'PASS' if self.fetch_success else 'FAIL'}",
            f"schedule state         : {self.state.value}",
            f"games fetched          : {self.total_games}",
            f"upcoming games         : {self.upcoming_games}",
            f"supported upcoming     : {self.supported_upcoming_games}",
            f"supported in horizon   : {self.supported_inside_horizon}",
            f"horizon end            : {self.horizon_end.isoformat()}",
            f"next upcoming kickoff  : "
            f"{self.next_upcoming_kickoff.isoformat() if self.next_upcoming_kickoff else 'none'}",
            f"next supported kickoff : "
            f"{self.next_supported_kickoff.isoformat() if self.next_supported_kickoff else 'none'}",
        ]
        if self.detail:
            lines.append(f"schedule detail        : {self.detail}")
        return "\n".join(lines)
