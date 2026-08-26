"""Mission section 30: end-to-end rehearsal of the full game lifecycle,
in-process, against real pricing/settlement/reporting code -- exactly the
modules scripts/research_scan_and_capture.py, research_settle.py, and
research_weekly_report.py wire together, minus the live CFBD/Kalshi HTTP
calls (this sandboxed environment has no network egress to either, same
constraint every prior milestone in this repo documents -- see
docs/MILESTONE_E.md). No manual database editing anywhere below: every
step goes through the real library function a live run would use.

Lifecycle covered: schedule discovery -> market discovery -> mapping ->
model projection -> timing bucket selection -> snapshot persistence ->
duplicate retry -> missed-window handling -> closing capture -> settlement
-> CLV calculation -> weekly report.
"""

from __future__ import annotations

import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, "tests")
from research_factories import make_data_versions  # noqa: E402

from cfb_edge_finder.kalshi.game_mapping import KalshiGameMappingResult
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest
from cfb_edge_finder.kalshi.ladder_pricing import price_one_market
from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.research import closing, clv, persistence, reporting, scan_logic, timing
from cfb_edge_finder.schemas.capture_state import CaptureState, CaptureStateRecord
from cfb_edge_finder.schemas.common import MarketFamily
from cfb_edge_finder.schemas.kalshi_observation import SnapshotTiming
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion

GAME_ID = "cfb-2026-wk01-texas-at-ohio-state"
KICKOFF = datetime(2026, 8, 29, 19, 0, tzinfo=UTC)
PROVENANCE = DataProvenance(schedule_source="cfbd", data_timestamp=KICKOFF)
MODEL_VERSION = ModelVersion(model_version="rehearsal-model-1.0", pricing_engine_version="0.1.0")

MAPPING = KalshiGameMappingResult(
    reason=None, game_id=GAME_ID, detail="unique match", home_team_id="ohio-state", away_team_id="texas"
)


def _line(team, opp, pts, opp_pts, home, week=1):
    return TeamGameLine(
        source_game_id=f"{'-'.join(sorted([team, opp]))}-{week}", season=2025, week=week, is_postseason=False,
        team_id=team, opponent_id=opp, team_classification="fbs", opponent_classification="fbs", is_home=home,
        is_neutral_site=False, team_points=pts, opponent_points=opp_pts, team_plays=68, captured_at=KICKOFF,
    )


def _synthetic_history():
    rng = np.random.default_rng(11)
    teams = ["ohio-state", "texas", *[f"filler{i}" for i in range(10)]]
    strength = {t: rng.normal(0, 0.05) for t in teams}
    lines = []
    for week in range(1, 7):
        shuffled = teams[:]
        rng.shuffle(shuffled)
        for i in range(0, len(shuffled), 2):
            home, away = shuffled[i], shuffled[i + 1]
            home_pts = max(int(rng.normal(28 + strength[home] * 180 + 2, 9)), 0)
            away_pts = max(int(rng.normal(24 + strength[away] * 180, 9)), 0)
            lines.append(_line(home, away, home_pts, away_pts, True, week=week))
            lines.append(_line(away, home, away_pts, home_pts, False, week=week))
    return lines


@pytest.fixture(scope="module")
def cached_projection():
    cache = GameProjectionCache(_synthetic_history())
    request = GameProjectionRequest(
        game_id=GAME_ID, home_id="ohio-state", away_id="texas", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, as_of_season=2025, as_of_week=7, n_simulations=2000, seed=0,
    )
    return cache.get_or_build(request)


def _moneyline_market():
    return {
        "ticker": "KXNCAAFGAME-26AUG29TEXOSU-OSU", "title": "Ohio State wins",
        "yes_bid_dollars": "0.55", "yes_ask_dollars": "0.62", "no_bid_dollars": "0.38", "no_ask_dollars": "0.45",
    }


def _capture(now: datetime, label: str, cached_projection) -> object:
    hours = timing.hours_before_kickoff(KICKOFF, now)
    return price_one_market(
        _moneyline_market(), family_hint=MarketFamily.MONEYLINE, event_ticker="EVT-TEST",
        series_ticker="KXNCAAFGAME", mapping=MAPPING, home_classification="fbs", away_classification="fbs",
        cached_projection=cached_projection, captured_at=now, snapshot_id=f"snap-{label}",
        snapshot_timing=SnapshotTiming(label=label, hours_before_kickoff=hours), model_version=MODEL_VERSION,
        training_cutoff="rehearsal", provenance=PROVENANCE,
    )


def test_full_lifecycle_rehearsal(tmp_path: Path, cached_projection):
    base_dir = tmp_path / "data" / "research"
    obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, 2026)
    settle_path = persistence.canonical_path(base_dir, persistence.SETTLEMENTS_SUBDIR, 2026)
    state_path = persistence.canonical_path(base_dir, persistence.CAPTURE_STATE_SUBDIR, 2026)

    # 1-4: schedule discovery / market discovery / mapping / model
    # projection are all represented by MAPPING + cached_projection above
    # (the same real objects a live scan would build from CFBD+Kalshi).
    assert MAPPING.reason is None
    assert cached_projection is not None

    already_captured: set[str] = set()
    written_keys: list[str] = []

    # 5-6: timing bucket selection + snapshot persistence, across several
    # realistic checkpoints.
    for hours_out in (168, 24, 6, 1.5):
        now = KICKOFF - timedelta(hours=hours_out)
        due = timing.resolve_due_labels(kickoff_utc=KICKOFF, now=now, already_captured_labels=already_captured)
        assert due, f"expected at least one due label at {hours_out}h out"
        for label in due:
            observation = _capture(now, label, cached_projection)
            row = scan_logic.build_corpus_row(
                observation=observation, season=2026, kickoff_utc_at_capture=KICKOFF,
                game_status_at_capture="scheduled", schedule_source_timestamp=now,
                data_versions=make_data_versions(model_version=MODEL_VERSION.model_version), run_id="rehearsal-1",
            )
            result = persistence.append_observation_rows(obs_path, [row])
            assert result.written == 1
            written_keys.append(row.observation_key)
            already_captured.add(label)

    assert len(persistence.read_observation_rows(obs_path)) == len(written_keys)

    # 7: duplicate retry -- re-appending the SAME rows must not duplicate.
    all_rows = persistence.read_observation_rows(obs_path)
    retry_result = persistence.append_observation_rows(obs_path, all_rows)
    assert retry_result.written == 0
    assert retry_result.skipped_duplicate == len(all_rows)

    # 8: missed-window handling -- T_60 never captured, now well past it.
    past_t60 = KICKOFF - timedelta(minutes=10)
    states = timing.resolve_all_bucket_states(
        kickoff_utc=KICKOFF, now=past_t60, already_captured_labels=already_captured
    )
    assert states["T_60"] == CaptureState.MISSED_WINDOW
    persistence.append_capture_state_rows(
        state_path,
        [
            CaptureStateRecord(
                game_id=GAME_ID, kalshi_market_ticker=_moneyline_market()["ticker"], timing_label="T_60",
                state=CaptureState.MISSED_WINDOW, observed_at=past_t60, detail="rehearsal: window closed",
            )
        ],
    )
    assert len(persistence.read_capture_state_rows(state_path)) == 1

    # 9: closing capture -- 4 minutes before kickoff.
    closing_now = KICKOFF - timedelta(minutes=4)
    closing_observation = _capture(closing_now, "CLOSING", cached_projection)
    closing_row = scan_logic.build_corpus_row(
        observation=closing_observation, season=2026, kickoff_utc_at_capture=KICKOFF,
        game_status_at_capture="scheduled", schedule_source_timestamp=closing_now,
        data_versions=make_data_versions(model_version=MODEL_VERSION.model_version), run_id="rehearsal-1",
    )
    persistence.append_observation_rows(obs_path, [closing_row])

    candidate = closing.ClosingCandidate(
        market_ticker=closing_observation.kalshi_market_ticker, captured_at=closing_now,
        game_status_at_capture="scheduled", executable_yes_price=closing_observation.executable_yes_price,
        minutes_before_kickoff=4.0,
    )
    closing_result = closing.select_closing_candidate([candidate])
    assert closing_result.captured is True
    assert closing_result.quality == "EXACT"

    # 10: settlement -- Ohio State wins 31-24.
    from cfb_edge_finder.research.settlement import extract_game_result, settle_market

    game_result = extract_game_result(
        {"status": "final", "homePoints": 31, "awayPoints": 24},
        game_id=GAME_ID, season=2026, captured_at=KICKOFF + timedelta(hours=4),
    )
    settlement = settle_market(closing_observation, game_result, settled_at=KICKOFF + timedelta(hours=4))
    persistence.append_settlement_rows(settle_path, [settlement])
    assert settlement.status.value == "settled"
    assert settlement.derived_contract_settlement is not None

    # 11: CLV -- entry (T_7D-equivalent capture) vs closing.
    entry_observation = _capture(KICKOFF - timedelta(hours=168), "T_7D", cached_projection)
    movement = clv.compute_market_movement(
        entry_snapshot_price=entry_observation.executable_yes_price,
        closing_price=closing_observation.executable_yes_price,
        model_probability_at_entry=entry_observation.model_probability,
        estimated_taker_fee_entry=entry_observation.estimated_taker_fee,
        estimated_taker_fee_closing=closing_observation.estimated_taker_fee,
        time_to_kickoff_hours_at_entry=168.0,
    )
    assert movement.raw_price_movement == pytest.approx(
        closing_observation.executable_yes_price - entry_observation.executable_yes_price
    )

    # 12: weekly report.
    all_rows = persistence.read_observation_rows(obs_path)
    settlement_rows = persistence.read_settlement_rows(settle_path)
    report = reporting.build_weekly_report(
        season=2026, week_label="wk01", rows=all_rows, settlement_rows=settlement_rows,
        generated_at=KICKOFF + timedelta(hours=5),
    )
    assert report.games_captured == 1
    assert report.contracts_captured == 1
    # settled_observations is per-OBSERVATION (contract-level, mission
    # section 18's correlation-awareness axis) -- every pregame snapshot
    # of this one now-settled market counts, not just the closing one.
    assert report.settled_observations == len(all_rows)
