"""Deadline-aware collection protection.

*** THE QUESTION THIS ANSWERS ***

    "Will the CURRENT trigger regime reliably cover the NEXT critical
     data-collection deadline?"

and deliberately NOT

    "Did the external scheduler run every five minutes all day?"

*** WHY THE OLD QUESTION WAS WRONG ***

An earlier version of the ops health check compared the observed external
trigger interval against an assumed ~10-minute cadence and reported
BLOCKED when it was longer. That was wrong twice over. The repository
cannot observe cron-job.org's configured schedule at all, so the assumed
cadence was fiction; and a long interval is the CORRECT operating state
during a quiet period, because every external dispatch spends private-
repository Actions minutes. A check that cries failure over an
intentional, cost-saving configuration is worse than no check: it trains
the reader to ignore it, and then it is ignored on the night that matters.

*** WHAT IS ACTUALLY KNOWABLE ***

Observable                                  | Source
------------------------------------------- | ------------------------
last successful collector run               | heartbeat ledger
INTERVALS BETWEEN OBSERVED runs             | heartbeat ledger
which trigger fired each run                | heartbeat trigger_type
next supported kickoff / critical checkpoint| heartbeat + schedule
the width of each checkpoint window         | research/timing.py
whether manual dispatch remains available   | operational fact

NOT observable: cron-job.org's configured cadence. This module therefore
MEASURES the interval between recent runs and reasons from that, and says
`OBSERVED` everywhere rather than claiming to know a configuration.

*** THE DECISION RULE, DERIVED NOT INVENTED ***

A checkpoint is covered when a trigger is guaranteed to land inside its
window, i.e. when the observed interval is no wider than the window. For
CLOSING that window is `CLOSING_WINDOW_MINUTES` (14) and it is
unrecoverable.

When the observed interval is WIDER than the window, coverage depends on
a cadence change happening before the window opens. The lead time needed
for that is already established in `research/trigger.py` as
`CLOSING_GUARD_LEAD_MINUTES` (25) -- itself derived as window + one tight
interval + dispatch and runtime margin. So:

    interval <= window                      -> covered by the regime
    time_to_window <= guard lead            -> CLOSING_AT_RISK (act NOW)
    time_to_window <= guard lead + interval -> CHECKPOINT_APPROACHING
    otherwise                               -> QUIET_PERIOD

The middle rule is the honest one: if the next deadline is closer than
one more observed interval plus the lead time, there may be no further
trigger before the cadence must already be tight, so the change has to be
made on THIS look, not the next one.

*** WHAT THIS MODULE WILL NOT DO ***

It does not weaken CLOSING protection. A narrow window with a wide
interval and no time left is `CLOSING_AT_RISK` no matter how deliberate
the quiet period was: intent does not capture a closing line. It also
never claims a checkpoint is protected when the next checkpoint is
unknown -- absence of a deadline is absence of information, not safety.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from cfb_edge_finder.research.timing import (
    CLOSING,
    CLOSING_WINDOW_MINUTES,
    NUMERIC_TIMING_WINDOWS,
)
from cfb_edge_finder.research.trigger import CLOSING_GUARD_LEAD_MINUTES

COLLECTION_PROTECTION_VERSION = "collection_protection_v1"

QUIET_PERIOD_IS_INTENTIONAL = (
    "A long observed external interval is the intended Week 1 quiet-period policy "
    "(Actions-minute conservation), not a fault. It becomes a problem only when a "
    "critical checkpoint approaches without the cadence being tightened."
)


class ProtectionState(StrEnum):
    """Whether the next critical deadline is covered."""

    QUIET_PERIOD = "QUIET_PERIOD"
    """No critical checkpoint is near. A wide interval is correct and
    cheap. Nothing to do."""

    CHECKPOINT_APPROACHING = "CHECKPOINT_APPROACHING"
    """A critical checkpoint is close enough that the observed interval
    must be tightened on THIS look -- there may be no further trigger
    before the cadence needs to already be tight."""

    CLOSING_AT_RISK = "CLOSING_AT_RISK"
    """The window opens within the guard lead and the observed interval
    is wider than the window. A CLOSING missed here is unrecoverable."""

    COVERED_TIGHT_CADENCE = "COVERED_TIGHT_CADENCE"
    """The observed interval already fits inside the checkpoint window.
    The regime covers the deadline on its own."""

    COLLECTION_STOPPED = "COLLECTION_STOPPED"
    """No successful run for far longer than any plausible cadence. This
    is a genuine failure, not a quiet period."""

    UNKNOWN_NO_TELEMETRY = "UNKNOWN_NO_TELEMETRY"
    """Not enough heartbeat history, or no known next checkpoint. Reported
    as ignorance rather than dressed up as health."""


CHECKPOINT_WINDOW_MINUTES: dict[str, float] = {
    CLOSING: CLOSING_WINDOW_MINUTES,
    **{w.label: w.half_width_hours * 2 * 60 for w in NUMERIC_TIMING_WINDOWS},
}
"""Full window width per label, read from the timing module rather than
restated. CLOSING's 14 minutes is the one that actually constrains
anything; T_30's is 30 minutes, and the earlier labels are hours wide."""

STOPPED_MULTIPLE = 4.0
"""How many observed intervals may elapse before silence is called a
failure rather than a gap. Expressed as a multiple of the OBSERVED
interval, so a deliberate 3-hour quiet cadence tolerates 12 hours while a
5-minute game-day cadence tolerates 20 minutes. A fixed minute count
could not do both, which is exactly how the previous check went wrong."""


@dataclass(frozen=True)
class TriggerObservation:
    """One collector run, as recorded. Provenance included because a run
    triggered by GitHub's own cron is not evidence that the external
    scheduler is alive."""

    invoked_at: datetime
    trigger_type: str
    succeeded: bool


@dataclass(frozen=True)
class ProtectionAssessment:
    state: ProtectionState
    detail: str
    remedy: str = ""

    observed_interval_minutes: float | None = None
    """MEASURED from heartbeat history. Never a configured cadence -- the
    repository cannot see cron-job.org."""
    interval_sample_size: int = 0
    minutes_since_last_run: float | None = None
    minutes_to_checkpoint: float | None = None
    checkpoint_label: str | None = None
    checkpoint_window_minutes: float | None = None
    tighten_by: datetime | None = None
    """When the cadence must already be tight. Reported so the owner can
    act on a clock rather than on a mood."""

    @property
    def is_actionable_now(self) -> bool:
        return self.state in (
            ProtectionState.CLOSING_AT_RISK,
            ProtectionState.CHECKPOINT_APPROACHING,
            ProtectionState.COLLECTION_STOPPED,
        )


def observed_interval_minutes(
    observations: list[TriggerObservation], *, trigger_type: str | None = None, sample: int = 6
) -> tuple[float | None, int]:
    """Median gap between recent successful runs, in minutes.

    The MEDIAN, not the mean: a single manual dispatch in the middle of a
    quiet period drags a mean far below the regime the owner actually
    configured, which would report protection that does not exist.

    Returns (interval, number_of_gaps_used). None when fewer than two
    runs are available -- one run establishes no interval, and inventing
    one from a single point is exactly the fiction this module exists to
    avoid."""
    runs = [o for o in observations if o.succeeded]
    if trigger_type is not None:
        runs = [o for o in runs if o.trigger_type == trigger_type]
    runs.sort(key=lambda o: o.invoked_at)
    recent = runs[-(sample + 1) :]
    if len(recent) < 2:
        return None, 0
    # strict=False deliberately: the two sequences are offset by one by
    # construction, which is the point -- pairing each run with its
    # successor is how a gap is formed.
    gaps = [
        (b.invoked_at - a.invoked_at).total_seconds() / 60.0
        for a, b in zip(recent, recent[1:], strict=False)
    ]
    return statistics.median(gaps), len(gaps)


def assess_collection_protection(
    *,
    now: datetime,
    last_successful_run: datetime | None,
    observations: list[TriggerObservation],
    next_checkpoint_at: datetime | None,
    next_checkpoint_label: str | None,
    manual_fallback_available: bool,
    guard_lead_minutes: float = CLOSING_GUARD_LEAD_MINUTES,
) -> ProtectionAssessment:
    """Is the next critical deadline covered by the regime we can observe?

    `guard_lead_minutes` defaults to the conductor's own already-derived
    constant so the two layers cannot drift apart; it stays a parameter so
    a test can probe the boundary without patching a module global."""
    interval, gaps = observed_interval_minutes(observations)
    since = None if last_successful_run is None else (now - last_successful_run).total_seconds() / 60.0

    if last_successful_run is None:
        return ProtectionAssessment(
            state=ProtectionState.COLLECTION_STOPPED,
            detail="No successful collector run has ever been recorded.",
            remedy="Dispatch Research Capture manually and confirm a heartbeat lands.",
            observed_interval_minutes=interval,
            interval_sample_size=gaps,
        )

    # Silence judged against the OBSERVED regime, not an assumed cadence.
    if interval is not None and since is not None and since > interval * STOPPED_MULTIPLE:
        return ProtectionAssessment(
            state=ProtectionState.COLLECTION_STOPPED,
            detail=(
                f"Last successful run {since:.0f} min ago, more than {STOPPED_MULTIPLE:g}x the "
                f"observed {interval:.0f} min interval. Collection has stopped."
            ),
            remedy="Check the external scheduler's job history and dispatch manually.",
            observed_interval_minutes=interval,
            interval_sample_size=gaps,
            minutes_since_last_run=since,
        )

    if next_checkpoint_at is None or next_checkpoint_label is None:
        return ProtectionAssessment(
            state=ProtectionState.UNKNOWN_NO_TELEMETRY,
            detail=(
                "No next critical checkpoint is known, so coverage cannot be assessed. "
                "This is missing information, not confirmed safety."
            ),
            remedy="Confirm the schedule source is answering (see schedule_state in the heartbeat).",
            observed_interval_minutes=interval,
            interval_sample_size=gaps,
            minutes_since_last_run=since,
        )

    window = CHECKPOINT_WINDOW_MINUTES.get(next_checkpoint_label, CLOSING_WINDOW_MINUTES)
    to_checkpoint = (next_checkpoint_at - now).total_seconds() / 60.0
    common = dict(
        observed_interval_minutes=interval,
        interval_sample_size=gaps,
        minutes_since_last_run=since,
        minutes_to_checkpoint=to_checkpoint,
        checkpoint_label=next_checkpoint_label,
        checkpoint_window_minutes=window,
    )

    if interval is None:
        return ProtectionAssessment(
            state=ProtectionState.UNKNOWN_NO_TELEMETRY,
            detail=(
                "Fewer than two recorded runs, so no trigger interval can be measured. "
                f"Next {next_checkpoint_label} in {to_checkpoint:.0f} min."
            ),
            remedy="Let two runs land, or dispatch manually to establish an interval.",
            **common,
        )

    # The regime covers the window on its own.
    if interval <= window:
        return ProtectionAssessment(
            state=ProtectionState.COVERED_TIGHT_CADENCE,
            detail=(
                f"Observed interval {interval:.0f} min fits inside the {window:.0f} min "
                f"{next_checkpoint_label} window ({to_checkpoint:.0f} min away)."
            ),
            **common,
        )

    tighten_by = next_checkpoint_at.fromtimestamp(
        next_checkpoint_at.timestamp() - guard_lead_minutes * 60, tz=next_checkpoint_at.tzinfo
    )

    if to_checkpoint <= guard_lead_minutes:
        at_risk = next_checkpoint_label == CLOSING
        return ProtectionAssessment(
            state=ProtectionState.CLOSING_AT_RISK,
            detail=(
                f"{next_checkpoint_label} opens in {to_checkpoint:.0f} min but the observed "
                f"interval is {interval:.0f} min, wider than the {window:.0f} min window. "
                + ("A missed CLOSING is unrecoverable." if at_risk else "")
            ).strip(),
            remedy=(
                "Switch the external scheduler to a tight cadence NOW"
                + (", and dispatch Research Capture manually as cover."
                   if manual_fallback_available
                   else " (no manual fallback recorded as available).")
            ),
            tighten_by=tighten_by,
            **common,
        )

    if to_checkpoint <= guard_lead_minutes + interval:
        return ProtectionAssessment(
            state=ProtectionState.CHECKPOINT_APPROACHING,
            detail=(
                f"{next_checkpoint_label} opens in {to_checkpoint:.0f} min. The observed "
                f"{interval:.0f} min interval is wider than the {window:.0f} min window, and "
                f"the next trigger may not arrive before the cadence must already be tight."
            ),
            remedy=f"Switch the external scheduler to a tight cadence by {tighten_by.isoformat()}.",
            tighten_by=tighten_by,
            **common,
        )

    return ProtectionAssessment(
        state=ProtectionState.QUIET_PERIOD,
        detail=(
            f"Next {next_checkpoint_label} is {to_checkpoint / 60:.1f} h away. The observed "
            f"{interval:.0f} min interval is intentionally wide and costs nothing here. "
            f"{QUIET_PERIOD_IS_INTENTIONAL}"
        ),
        remedy=f"No action now. Tighten the cadence by {tighten_by.isoformat()}.",
        tighten_by=tighten_by,
        **common,
    )
