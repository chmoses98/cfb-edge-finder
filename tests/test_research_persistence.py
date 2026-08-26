"""Part A: durable append-only store -- dedup, immutability, no silent
overwrite, settlement fact-fingerprint, capture-state log semantics."""

from __future__ import annotations

import sys
from datetime import UTC, datetime
from pathlib import Path

import pytest

sys.path.insert(0, "tests")
from research_factories import make_corpus_row, make_observation  # noqa: E402

from cfb_edge_finder.research import persistence
from cfb_edge_finder.research.settlement import extract_game_result, settle_market
from cfb_edge_finder.schemas.capture_state import CaptureState, CaptureStateRecord
from cfb_edge_finder.schemas.common import MarketFamily, Side

NOW = datetime(2026, 9, 6, tzinfo=UTC)


@pytest.fixture
def tmp_obs_path(tmp_path: Path) -> Path:
    return persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2026)


def test_append_new_rows_written(tmp_obs_path):
    row = make_corpus_row()
    result = persistence.append_observation_rows(tmp_obs_path, [row])
    assert result.written == 1
    assert result.skipped_duplicate == 0
    assert tmp_obs_path.exists()


def test_append_same_row_twice_deduped_no_overwrite(tmp_obs_path):
    row = make_corpus_row()
    persistence.append_observation_rows(tmp_obs_path, [row])
    result2 = persistence.append_observation_rows(tmp_obs_path, [row])
    assert result2.written == 0
    assert result2.skipped_duplicate == 1
    rows = persistence.read_observation_rows(tmp_obs_path)
    assert len(rows) == 1  # never a second copy


def test_within_batch_duplicates_also_deduped(tmp_obs_path):
    row = make_corpus_row()
    result = persistence.append_observation_rows(tmp_obs_path, [row, row, row])
    assert result.written == 1
    assert result.skipped_duplicate == 2


def test_different_keys_both_appended(tmp_obs_path):
    row1 = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-1"))
    row2 = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-2"))
    persistence.append_observation_rows(tmp_obs_path, [row1])
    result = persistence.append_observation_rows(tmp_obs_path, [row2])
    assert result.written == 1
    assert len(persistence.read_observation_rows(tmp_obs_path)) == 2


def test_rows_are_immutable_on_disk_no_line_ever_rewritten(tmp_obs_path):
    row = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-1"))
    persistence.append_observation_rows(tmp_obs_path, [row])
    original_content = tmp_obs_path.read_text()

    # Appending a genuinely new row must never touch the first line.
    row2 = make_corpus_row(observation=make_observation(kalshi_market_ticker="MKT-2"))
    persistence.append_observation_rows(tmp_obs_path, [row2])
    new_content = tmp_obs_path.read_text()
    assert new_content.startswith(original_content)


def test_new_process_reading_same_path_sees_prior_rows(tmp_obs_path):
    # Simulates "a new workflow run can read prior snapshots" -- a fresh
    # call against the same path sees everything written before it.
    row = make_corpus_row()
    persistence.append_observation_rows(tmp_obs_path, [row])
    keys = persistence.read_observation_keys(tmp_obs_path)
    assert row.observation_key in keys


def test_reading_nonexistent_file_returns_empty_not_an_error(tmp_path):
    path = persistence.canonical_path(tmp_path, persistence.OBSERVATIONS_SUBDIR, 2099)
    assert persistence.read_observation_rows(path) == []
    assert persistence.read_observation_keys(path) == set()


def test_a_random_uuid_snapshot_id_does_not_prevent_dedup(tmp_obs_path):
    # snapshot_id (Milestone D's random UUID) differs across two captures
    # of the SAME logical checkpoint -- dedup must still collapse them via
    # observation_key, not accidentally rely on snapshot_id.
    row1 = make_corpus_row()
    obs2 = make_observation(snapshot_id="a-totally-different-uuid")
    row2 = make_corpus_row(observation=obs2)
    assert row1.observation.snapshot_id != row2.observation.snapshot_id
    assert row1.observation_key == row2.observation_key
    persistence.append_observation_rows(tmp_obs_path, [row1])
    result = persistence.append_observation_rows(tmp_obs_path, [row2])
    assert result.written == 0
    assert result.skipped_duplicate == 1


# --- Settlements ---------------------------------------------------------


def test_settlement_identical_fact_deduped(tmp_path):
    path = persistence.canonical_path(tmp_path, persistence.SETTLEMENTS_SUBDIR, 2026)
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME)
    result = extract_game_result(
        {"status": "final", "homePoints": 31, "awayPoints": 24}, game_id="g1", season=2026, captured_at=NOW
    )
    settlement = settle_market(obs, result, settled_at=NOW)
    r1 = persistence.append_settlement_rows(path, [settlement])
    r2 = persistence.append_settlement_rows(path, [settlement])
    assert r1.written == 1
    assert r2.written == 0
    assert r2.skipped_duplicate == 1


def test_settlement_state_transition_appends_new_row(tmp_path):
    path = persistence.canonical_path(tmp_path, persistence.SETTLEMENTS_SUBDIR, 2026)
    obs = make_observation(family=MarketFamily.MONEYLINE, team=Side.HOME, game_id="g1")
    pending = extract_game_result({"status": "scheduled"}, game_id="g1", season=2026, captured_at=NOW)
    settlement_pending = settle_market(obs, pending, settled_at=NOW)
    persistence.append_settlement_rows(path, [settlement_pending])

    final = extract_game_result(
        {"status": "final", "homePoints": 31, "awayPoints": 24}, game_id="g1", season=2026, captured_at=NOW
    )
    settlement_final = settle_market(obs, final, settled_at=NOW)
    result = persistence.append_settlement_rows(path, [settlement_final])
    assert result.written == 1  # genuine state change -> new row, not a duplicate

    rows = persistence.read_settlement_rows(path)
    assert len(rows) == 2
    latest = persistence.latest_settlements(rows)
    assert latest[("g1", obs.kalshi_market_ticker)].status.value == "settled"


# --- Capture-state log -----------------------------------------------------


def test_capture_state_same_state_reobserved_is_deduped(tmp_path):
    path = persistence.canonical_path(tmp_path, persistence.CAPTURE_STATE_SUBDIR, 2026)
    rec = CaptureStateRecord(
        game_id="g1", kalshi_market_ticker="MKT-1", timing_label="T_60", state=CaptureState.CAPTURED, observed_at=NOW
    )
    r1 = persistence.append_capture_state_rows(path, [rec])
    r2 = persistence.append_capture_state_rows(path, [rec])
    assert r1.written == 1
    assert r2.written == 0


def test_capture_state_transition_appends(tmp_path):
    path = persistence.canonical_path(tmp_path, persistence.CAPTURE_STATE_SUBDIR, 2026)
    not_due = CaptureStateRecord(
        game_id="g1", kalshi_market_ticker="MKT-1", timing_label="T_60",
        state=CaptureState.NOT_YET_DUE, observed_at=NOW,
    )
    captured = CaptureStateRecord(
        game_id="g1", kalshi_market_ticker="MKT-1", timing_label="T_60",
        state=CaptureState.CAPTURED, observed_at=NOW,
    )
    persistence.append_capture_state_rows(path, [not_due])
    result = persistence.append_capture_state_rows(path, [captured])
    assert result.written == 1
    rows = persistence.read_capture_state_rows(path)
    assert len(rows) == 2
    latest = persistence.latest_capture_states(rows)
    assert latest[("g1", "MKT-1", "T_60")].state == CaptureState.CAPTURED
