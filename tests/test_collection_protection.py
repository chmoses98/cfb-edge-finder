"""Deadline-aware collection protection.

The regression this file exists for: an intentionally wide quiet-period
interval must NOT be reported as failure, while a narrow CLOSING window
that the observed interval cannot cover must still be BLOCKED.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from cfb_edge_finder.decision.collection_protection import (
    STOPPED_MULTIPLE,
    ProtectionState,
    TriggerObservation,
    assess_collection_protection,
    observed_interval_minutes,
)
from cfb_edge_finder.decision.ops_health import OpsState, check_collection_protection
from cfb_edge_finder.research.timing import CLOSING, CLOSING_WINDOW_MINUTES
from cfb_edge_finder.research.trigger import CLOSING_GUARD_LEAD_MINUTES

NOW = datetime(2026, 8, 28, 13, 0, tzinfo=UTC)


def runs(*, interval_minutes: float, count: int = 5, trigger: str = "EXTERNAL_SCHEDULE",
         succeeded: bool = True, end: datetime = NOW) -> list[TriggerObservation]:
    return [
        TriggerObservation(
            invoked_at=end - timedelta(minutes=interval_minutes * i),
            trigger_type=trigger,
            succeeded=succeeded,
        )
        for i in reversed(range(count))
    ]


def assess(**overrides):
    kwargs = dict(
        now=NOW,
        last_successful_run=NOW - timedelta(minutes=5),
        observations=runs(interval_minutes=180),
        next_checkpoint_at=NOW + timedelta(hours=26),
        next_checkpoint_label=CLOSING,
        manual_fallback_available=True,
    )
    kwargs.update(overrides)
    return assess_collection_protection(**kwargs)


# ------------------------------------------- the corrected model


def test_quiet_period_with_a_distant_checkpoint_is_healthy():
    """THE REGRESSION. A deliberate ~3-hour interval with CLOSING a day
    away was previously reported BLOCKED against an assumed 10-minute
    cadence. It is the intended cost-saving policy."""
    a = assess(observations=runs(interval_minutes=180), next_checkpoint_at=NOW + timedelta(hours=26))
    assert a.state is ProtectionState.QUIET_PERIOD
    assert check_collection_protection(a).state is OpsState.HEALTHY
    assert not a.is_actionable_now


def test_quiet_period_still_reports_when_the_cadence_must_be_tightened():
    """Healthy is not the same as 'nothing to know'. The owner gets a
    clock, not a mood."""
    a = assess(next_checkpoint_at=NOW + timedelta(hours=26))
    assert a.tighten_by == NOW + timedelta(hours=26) - timedelta(minutes=CLOSING_GUARD_LEAD_MINUTES)
    assert "Tighten the cadence by" in a.remedy


def test_closing_imminent_with_a_wide_interval_is_blocked():
    """CLOSING protection is NOT weakened. Intent does not capture a
    closing line."""
    a = assess(
        observations=runs(interval_minutes=180),
        next_checkpoint_at=NOW + timedelta(minutes=10),
    )
    assert a.state is ProtectionState.CLOSING_AT_RISK
    assert check_collection_protection(a).state is OpsState.BLOCKED
    assert "unrecoverable" in a.detail
    assert "NOW" in a.remedy


def test_checkpoint_approaching_warns_before_it_is_too_late():
    """The honest middle case: closer than one more interval plus the
    lead time, so the change must happen on THIS look."""
    a = assess(
        observations=runs(interval_minutes=180),
        next_checkpoint_at=NOW + timedelta(minutes=CLOSING_GUARD_LEAD_MINUTES + 60),
    )
    assert a.state is ProtectionState.CHECKPOINT_APPROACHING
    assert check_collection_protection(a).state is OpsState.WARN
    assert a.is_actionable_now


def test_a_tight_cadence_covers_the_window_regardless_of_proximity():
    """Once the interval fits inside the window, the regime covers the
    deadline on its own and proximity stops mattering."""
    for minutes_away in (5, 20, 200, 2000):
        a = assess(
            observations=runs(interval_minutes=5),
            next_checkpoint_at=NOW + timedelta(minutes=minutes_away),
        )
        assert a.state is ProtectionState.COVERED_TIGHT_CADENCE, minutes_away
        assert check_collection_protection(a).state is OpsState.HEALTHY


def test_the_window_boundary_is_exact():
    """An interval exactly equal to the window still covers it; one
    minute wider does not."""
    at_risk_time = NOW + timedelta(minutes=5)
    covered = assess(observations=runs(interval_minutes=CLOSING_WINDOW_MINUTES), next_checkpoint_at=at_risk_time)
    assert covered.state is ProtectionState.COVERED_TIGHT_CADENCE
    wider = assess(observations=runs(interval_minutes=CLOSING_WINDOW_MINUTES + 1), next_checkpoint_at=at_risk_time)
    assert wider.state is ProtectionState.CLOSING_AT_RISK


def test_the_guard_lead_boundary_is_exact():
    wide = runs(interval_minutes=180)
    at = assess(observations=wide, next_checkpoint_at=NOW + timedelta(minutes=CLOSING_GUARD_LEAD_MINUTES))
    assert at.state is ProtectionState.CLOSING_AT_RISK
    just_after = assess(
        observations=wide, next_checkpoint_at=NOW + timedelta(minutes=CLOSING_GUARD_LEAD_MINUTES + 1)
    )
    assert just_after.state is ProtectionState.CHECKPOINT_APPROACHING


# ------------------------------------------------ genuine failure


def test_never_having_run_is_stopped():
    a = assess(last_successful_run=None)
    assert a.state is ProtectionState.COLLECTION_STOPPED
    assert check_collection_protection(a).state is OpsState.BLOCKED


def test_silence_is_judged_against_the_observed_interval_not_a_fixed_number():
    """A 3-hour quiet cadence tolerates hours of silence; a 5-minute
    game-day cadence does not. A fixed minute threshold cannot do both --
    which is precisely how the previous check went wrong."""
    quiet = assess(
        observations=runs(interval_minutes=180),
        last_successful_run=NOW - timedelta(minutes=180 * STOPPED_MULTIPLE - 1),
    )
    assert quiet.state is not ProtectionState.COLLECTION_STOPPED

    tight_regime_same_silence = assess(
        observations=runs(interval_minutes=5),
        last_successful_run=NOW - timedelta(minutes=180),
        next_checkpoint_at=NOW + timedelta(hours=26),
    )
    assert tight_regime_same_silence.state is ProtectionState.COLLECTION_STOPPED


def test_stopped_boundary_is_exact():
    a = assess(
        observations=runs(interval_minutes=100),
        last_successful_run=NOW - timedelta(minutes=100 * STOPPED_MULTIPLE + 1),
    )
    assert a.state is ProtectionState.COLLECTION_STOPPED


# ------------------------------------------ refusing to guess


def test_an_unknown_next_checkpoint_is_reported_as_ignorance_not_safety():
    a = assess(next_checkpoint_at=None, next_checkpoint_label=None)
    assert a.state is ProtectionState.UNKNOWN_NO_TELEMETRY
    assert check_collection_protection(a).state is OpsState.WARN
    assert "not confirmed safety" in a.detail


def test_a_single_run_establishes_no_interval():
    """One point is not a cadence. Inventing one would be the same class
    of fiction as assuming cron-job.org's configuration."""
    a = assess(observations=runs(interval_minutes=180, count=1))
    assert a.state is ProtectionState.UNKNOWN_NO_TELEMETRY
    assert a.observed_interval_minutes is None


def test_no_observations_at_all_establishes_no_interval():
    assert observed_interval_minutes([]) == (None, 0)


def test_the_interval_is_a_median_so_one_manual_dispatch_cannot_fake_protection():
    """A single manual run in the middle of a quiet period would drag a
    MEAN down to something that looks protected."""
    quiet = runs(interval_minutes=180, count=5)
    intruder = list(quiet) + [
        TriggerObservation(invoked_at=NOW - timedelta(minutes=1), trigger_type="MANUAL", succeeded=True)
    ]
    interval, _ = observed_interval_minutes(intruder)
    assert interval is not None and interval > CLOSING_WINDOW_MINUTES


def test_failed_runs_do_not_count_as_coverage():
    failed = runs(interval_minutes=5, succeeded=False)
    interval, gaps = observed_interval_minutes(failed)
    assert interval is None and gaps == 0


def test_interval_can_be_scoped_to_a_trigger_type():
    mixed = runs(interval_minutes=180, trigger="EXTERNAL_SCHEDULE") + runs(
        interval_minutes=5, trigger="MANUAL", end=NOW - timedelta(days=2)
    )
    external, _ = observed_interval_minutes(mixed, trigger_type="EXTERNAL_SCHEDULE")
    assert external == pytest.approx(180)


# ------------------------------------------------ no invented cadence


def test_the_module_never_claims_a_configured_external_cadence():
    """The repository cannot see cron-job.org. Nothing here may imply it
    can."""
    import pathlib

    src = pathlib.Path("src/cfb_edge_finder/decision/collection_protection.py").read_text()
    for claim in ("configured cadence is", "cron-job.org is set", "every 3 hours", "every 5 minutes"):
        assert claim not in src
    assert "MEASURED" in src or "measure" in src.lower()


def test_wider_checkpoints_are_easier_to_cover_than_closing():
    """T_24H is a 12-hour window; a 3-hour interval covers it. CLOSING's
    14 minutes does not. The label must change the answer."""
    soon = NOW + timedelta(minutes=10)
    closing = assess(observations=runs(interval_minutes=180), next_checkpoint_at=soon,
                     next_checkpoint_label=CLOSING)
    t24 = assess(observations=runs(interval_minutes=180), next_checkpoint_at=soon,
                 next_checkpoint_label="T_24H")
    assert closing.state is ProtectionState.CLOSING_AT_RISK
    assert t24.state is ProtectionState.COVERED_TIGHT_CADENCE
