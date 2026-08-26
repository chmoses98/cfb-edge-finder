"""Mission section 8-9: stale-schedule guard, reschedule detection, and
the corpus-row builder's identity wiring."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta

import pytest

sys.path.insert(0, "tests")
from research_factories import make_data_versions, make_observation  # noqa: E402

from cfb_edge_finder.research import scan_logic
from cfb_edge_finder.research.identity import observation_key

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def test_guard_allows_fresh_scheduled_game():
    scan_logic.guard_capture_allowed(game_status="scheduled", schedule_source_timestamp=NOW, now=NOW)  # no raise


def test_guard_rejects_already_started_game():
    with pytest.raises(scan_logic.StaleScheduleGuardError):
        scan_logic.guard_capture_allowed(game_status="in_progress", schedule_source_timestamp=NOW, now=NOW)


def test_guard_rejects_final_game():
    with pytest.raises(scan_logic.StaleScheduleGuardError):
        scan_logic.guard_capture_allowed(game_status="final", schedule_source_timestamp=NOW, now=NOW)


def test_guard_rejects_stale_schedule_data():
    stale_source = NOW - timedelta(hours=scan_logic.MAX_SCHEDULE_STALENESS_HOURS + 1)
    with pytest.raises(scan_logic.StaleScheduleGuardError):
        scan_logic.guard_capture_allowed(game_status="scheduled", schedule_source_timestamp=stale_source, now=NOW)


def test_guard_allows_schedule_data_within_freshness_window():
    fresh_source = NOW - timedelta(hours=scan_logic.MAX_SCHEDULE_STALENESS_HOURS - 1)
    scan_logic.guard_capture_allowed(game_status="scheduled", schedule_source_timestamp=fresh_source, now=NOW)


def test_guard_allows_unknown_schedule_timestamp():
    scan_logic.guard_capture_allowed(game_status="scheduled", schedule_source_timestamp=None, now=NOW)


def test_detect_reschedule_true_for_genuine_shift():
    old = NOW
    new = NOW + timedelta(hours=3)
    assert scan_logic.detect_reschedule(old, new) is True


def test_detect_reschedule_false_for_sub_threshold_jitter():
    old = NOW
    new = NOW + timedelta(minutes=5)
    assert scan_logic.detect_reschedule(old, new) is False


def test_detect_reschedule_false_when_either_kickoff_unknown():
    assert scan_logic.detect_reschedule(None, NOW) is False
    assert scan_logic.detect_reschedule(NOW, None) is False
    assert scan_logic.detect_reschedule(None, None) is False


def test_build_corpus_row_key_matches_identity_module():
    obs = make_observation(kalshi_market_ticker="MKT-1")
    row = scan_logic.build_corpus_row(
        observation=obs, season=2026, kickoff_utc_at_capture=NOW, game_status_at_capture="scheduled",
        schedule_source_timestamp=NOW, data_versions=make_data_versions(), run_id="run-1",
    )
    expected = observation_key(
        season=2026, game_id=obs.game_id, market_ticker=obs.kalshi_market_ticker,
        timing_label=obs.snapshot_timing.label, model_version=obs.model_version.model_version,
    )
    assert row.observation_key == expected


def test_build_corpus_row_unmapped_game_uses_placeholder_in_key():
    obs = make_observation(
        game_id=None, family=None, model_version=None,
        coverage_outcome="ticker_unresolved", pricing_status="not_priced",
    )
    row = scan_logic.build_corpus_row(
        observation=obs, season=2026, kickoff_utc_at_capture=None, game_status_at_capture="unknown",
        schedule_source_timestamp=None, data_versions=make_data_versions(), run_id=None,
    )
    assert row.observation_key  # does not crash on missing game_id/model_version
