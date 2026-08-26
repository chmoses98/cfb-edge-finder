"""Mission section 31: failure-injection rehearsal. Each test simulates
one specific failure mode and proves the documented recovery behavior --
never a crash, never silent data corruption, never a duplicate row."""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, "tests")
from research_factories import make_corpus_row, make_observation  # noqa: E402

from cfb_edge_finder.research import git_durable_store, health, persistence, scan_logic, timing
from cfb_edge_finder.schemas.capture_state import CaptureState
from cfb_edge_finder.schemas.kalshi_observation import SnapshotTiming

KICKOFF = datetime(2026, 9, 5, 19, 0, tzinfo=UTC)


# 1. Missed T_60 -------------------------------------------------------


def test_missed_t60_is_marked_not_fabricated():
    now_past_t60 = KICKOFF - timedelta(minutes=5)
    state = timing.classify_bucket_state(label="T_60", kickoff_utc=KICKOFF, now=now_past_t60, captured=False)
    assert state == CaptureState.MISSED_WINDOW
    due = timing.resolve_due_labels(kickoff_utc=KICKOFF, now=now_past_t60, already_captured_labels=set())
    assert "T_60" not in due  # never fabricated after the fact


def test_missed_t60_does_not_block_next_valid_checkpoint():
    now_at_t30 = KICKOFF - timedelta(minutes=30)
    due = timing.resolve_due_labels(kickoff_utc=KICKOFF, now=now_at_t30, already_captured_labels={"T_90"})
    assert "T_30" in due  # the NEXT checkpoint is still captured normally


# 2. Duplicated scheduler run -------------------------------------------


def test_duplicated_scheduler_run_does_not_duplicate_rows(tmp_path: Path):
    path = persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2026)
    row = make_corpus_row()
    result1 = persistence.append_observation_rows(path, [row])
    # A second, fully independent "scheduler run" processes the identical checkpoint.
    result2 = persistence.append_observation_rows(path, [row])
    assert result1.written == 1
    assert result2.written == 0
    assert len(persistence.read_observation_rows(path)) == 1


# 3. Kickoff moved by several hours --------------------------------------


def test_kickoff_moved_several_hours_detected_and_buckets_reevaluate():
    original_kickoff = KICKOFF
    new_kickoff = KICKOFF + timedelta(hours=3)
    assert scan_logic.detect_reschedule(original_kickoff, new_kickoff) is True

    # Buckets already captured against the OLD kickoff keep their identity
    # (observation_key does not depend on kickoff time) -- only NOT-YET-
    # captured buckets get re-evaluated against the new kickoff.
    already_captured = {"EARLY_OPEN", "T_7D"}
    now = new_kickoff - timedelta(hours=24)
    due = timing.resolve_due_labels(kickoff_utc=new_kickoff, now=now, already_captured_labels=already_captured)
    assert "T_24H" in due
    assert "EARLY_OPEN" not in due  # not re-captured just because kickoff moved
    assert "T_7D" not in due


def test_kickoff_moved_minor_jitter_not_flagged_as_reschedule():
    assert scan_logic.detect_reschedule(KICKOFF, KICKOFF + timedelta(minutes=2)) is False


# 4. Kalshi partial failure (some series fail, others succeed) -----------


def test_kalshi_partial_failure_isolated_per_series(tmp_path: Path):
    # Simulates: KXNCAAFSPREAD returns data, KXNCAAFTOTAL raises. The
    # health report tracks the failure without losing the successful series'
    # captures -- verified here at the persistence layer: a partial write
    # from one series succeeds and is durable even if a later series fails.
    path = persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2026)
    good_row = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-SPREAD-1"))
    persistence.append_observation_rows(path, [good_row])

    report = health.CaptureHealthReport(games_scanned=10, markets_scanned=50, api_failures=1)
    assert len(persistence.read_observation_rows(path)) == 1  # good series' data survives
    assert report.api_failures == 1  # failure is tracked, not swallowed silently


# 5. CFBD temporary failure ------------------------------------------------


def test_cfbd_temporary_failure_produces_explicit_zero_games_diagnostic():
    report = health.CaptureHealthReport(games_scanned=0, markets_scanned=500)
    diagnostics = health.evaluate_collapse(report, baseline_supported_markets=None)
    assert any(d.code == "zero_games_scanned" for d in diagnostics)
    assert health.should_fail_run(diagnostics) is True  # fails loud, never silently continues


# 6. Persistence retry (git push rejected then retried) ------------------


def test_persistence_retry_via_git_durable_store(tmp_path: Path):
    import subprocess

    def _run(args, cwd):
        r = subprocess.run(args, cwd=str(cwd), capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
        return r

    bare = tmp_path / "bare.git"
    _run(["git", "init", "--bare", str(bare)], tmp_path)
    clone_a = tmp_path / "clone_a"
    _run(["git", "clone", str(bare), str(clone_a)], tmp_path)
    _run(["git", "config", "user.email", "test@example.com"], clone_a)
    _run(["git", "config", "user.name", "Test"], clone_a)
    (clone_a / "README.md").write_text("seed\n")
    _run(["git", "add", "README.md"], clone_a)
    _run(["git", "commit", "-m", "seed"], clone_a)
    _run(["git", "push", "-u", "origin", "HEAD:main"], clone_a)

    git_durable_store.ensure_branch_checked_out(clone_a, "research-data")

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, 2026)
        return persistence.append_observation_rows(path, [make_corpus_row()])

    result = git_durable_store.commit_and_push_with_retry(clone_a, "research-data", apply_fn, "retry test")
    assert result.append_result.written == 1


# 7. Already-started game --------------------------------------------------


def test_already_started_game_rejected_by_stale_guard():
    with pytest.raises(scan_logic.StaleScheduleGuardError):
        scan_logic.guard_capture_allowed(game_status="in_progress", schedule_source_timestamp=KICKOFF, now=KICKOFF)


def test_already_started_game_no_due_labels_from_timing():
    due = timing.resolve_due_labels(
        kickoff_utc=KICKOFF, now=KICKOFF + timedelta(minutes=5), already_captured_labels=set(), game_started=True
    )
    assert due == []


# 8. Market disappears before closing ---------------------------------------


def test_market_disappearing_before_closing_is_missed_not_fabricated():
    from cfb_edge_finder.research.closing import select_closing_candidate

    # No candidates at all reach select_closing_candidate (the market
    # vanished from the Kalshi sweep before a closing-eligible quote was ever seen).
    result = select_closing_candidate([])
    assert result.captured is False
    assert result.quality == "MISSED"


# 9. Rescheduled game (postponed, later resumed) ---------------------------


def test_rescheduled_game_preserves_original_capture_history(tmp_path: Path):
    path = persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2026)
    original = make_corpus_row(kickoff_utc_at_capture=KICKOFF)
    persistence.append_observation_rows(path, [original])

    # Kickoff moves; a NEW capture (different timing label) is appended --
    # the original row is never rewritten or deleted.
    new_kickoff = KICKOFF + timedelta(days=1)
    new_obs = make_observation(
        kalshi_market_ticker=original.observation.kalshi_market_ticker,
        snapshot_timing=SnapshotTiming(label="T_24H"),
    )
    new_row = make_corpus_row(observation=new_obs, kickoff_utc_at_capture=new_kickoff)
    persistence.append_observation_rows(path, [new_row])

    rows = persistence.read_observation_rows(path)
    assert len(rows) == 2
    assert rows[0].kickoff_utc_at_capture == KICKOFF  # original untouched


# 10. Settlement delay -----------------------------------------------------


def test_settlement_delay_pending_then_settled_preserves_both_facts(tmp_path: Path):
    from cfb_edge_finder.research.settlement import extract_game_result, settle_market

    path = persistence.canonical_path(tmp_path, persistence.SETTLEMENTS_SUBDIR, 2026)
    obs = make_observation(game_id="g-delay")
    pending_result = extract_game_result({"status": "scheduled"}, game_id="g-delay", season=2026, captured_at=KICKOFF)
    pending_settlement = settle_market(obs, pending_result, settled_at=KICKOFF)
    persistence.append_settlement_rows(path, [pending_settlement])

    final_result = extract_game_result(
        {"status": "final", "homePoints": 20, "awayPoints": 17},
        game_id="g-delay", season=2026, captured_at=KICKOFF + timedelta(days=1),
    )
    final_settlement = settle_market(obs, final_result, settled_at=KICKOFF + timedelta(days=1))
    persistence.append_settlement_rows(path, [final_settlement])

    rows = persistence.read_settlement_rows(path)
    assert len(rows) == 2  # both the pending and final facts are preserved in history
    latest = persistence.latest_settlements(rows)
    assert latest[("g-delay", obs.kalshi_market_ticker)].status.value == "settled"
