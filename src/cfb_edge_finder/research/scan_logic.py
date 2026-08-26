"""Milestone E, Parts B/C: pure decision logic for the single scheduled
scanner -- kept separate from scripts/research_scan_and_capture.py's
live I/O so every rule here is unit-testable without a network call.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from cfb_edge_finder.research.identity import CAPTURE_WINDOW_VERSION, observation_key
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.data_versions import DataVersionManifest
from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation

MAX_SCHEDULE_STALENESS_HOURS = 6.0
"""mission section 9: source schedule timestamp must be "fresh enough."
6 hours is generous relative to an hourly scanner cadence (comfortably
survives one missed run) while still catching a genuinely stale/stuck
schedule fetch."""

RESCHEDULE_THRESHOLD_MINUTES = 15.0
"""A kickoff shift smaller than this is treated as clock-precision noise,
not a genuine schedule change -- avoids spuriously flagging GAME_RESCHEDULED
on sub-15-minute source jitter."""


class StaleScheduleGuardError(ValueError):
    """Raised when a capture attempt fails the stale-schedule guard --
    callers must record this as a stale_schedule_failure in the health
    report, never silently swallow it."""


def guard_capture_allowed(
    *,
    game_status: str,
    schedule_source_timestamp: datetime | None,
    now: datetime,
    max_staleness_hours: float = MAX_SCHEDULE_STALENESS_HOURS,
) -> None:
    """Mission section 9: every capture run must verify the game has not
    already started and the schedule data is fresh. Raises
    StaleScheduleGuardError on either violation -- never returns a
    silently-degraded "maybe ok" signal."""
    if game_status not in ("scheduled",):
        raise StaleScheduleGuardError(
            f"game_status={game_status!r} -- refusing to create a new pregame snapshot for a game "
            f"that is not in 'scheduled' status"
        )
    if schedule_source_timestamp is not None:
        age_hours = (now - schedule_source_timestamp).total_seconds() / 3600.0
        if age_hours > max_staleness_hours:
            raise StaleScheduleGuardError(
                f"schedule_source_timestamp is {age_hours:.1f}h old, exceeding the "
                f"{max_staleness_hours:.1f}h freshness guard"
            )


def detect_reschedule(
    previous_kickoff_utc: datetime | None,
    latest_kickoff_utc: datetime | None,
    *,
    threshold_minutes: float = RESCHEDULE_THRESHOLD_MINUTES,
) -> bool:
    """True only for a genuine schedule change -- both kickoffs known and
    differing by more than the noise threshold. A brand-new game
    (previous None) or an unresolved kickoff (either None) is never
    itself a "reschedule.\""""
    if previous_kickoff_utc is None or latest_kickoff_utc is None:
        return False
    delta_minutes = abs((latest_kickoff_utc - previous_kickoff_utc).total_seconds()) / 60.0
    return delta_minutes > threshold_minutes


@dataclass(frozen=True)
class ScheduleChangeRecord:
    """Provenance for a kickoff change -- preserved alongside, never
    instead of, the original capture history (mission section 8:
    "preserve original capture history and schedule-change provenance").
    Timing-bucket re-evaluation naturally follows from this: buckets
    already captured keep their labels/keys (their observation_key is a
    function of season/game_id/ticker/label/model_version, not of
    kickoff time), and `resolve_due_labels` is simply re-run against the
    NEW kickoff for everything not yet captured -- no separate
    "re-labeling" step is needed, and no old label is ever duplicated
    under the new kickoff."""

    game_id: str
    previous_kickoff_utc: datetime | None
    new_kickoff_utc: datetime
    detected_at: datetime


def build_corpus_row(
    *,
    observation: KalshiResearchObservation,
    season: int,
    kickoff_utc_at_capture: datetime | None,
    game_status_at_capture: str,
    schedule_source_timestamp: datetime | None,
    data_versions: DataVersionManifest,
    run_id: str | None,
    capture_window_version: str = CAPTURE_WINDOW_VERSION,
) -> ResearchCorpusRow:
    key = observation_key(
        season=season,
        game_id=observation.game_id or "unmapped",
        market_ticker=observation.kalshi_market_ticker,
        timing_label=observation.snapshot_timing.label,
        model_version=observation.model_version.model_version if observation.model_version else "unpriced",
        capture_window_version=capture_window_version,
    )
    return ResearchCorpusRow(
        observation_key=key,
        capture_window_version=capture_window_version,
        season=season,
        kickoff_utc_at_capture=kickoff_utc_at_capture,
        game_status_at_capture=game_status_at_capture,
        schedule_source_timestamp=schedule_source_timestamp,
        data_versions=data_versions,
        observation=observation,
        run_id=run_id,
    )
