"""Mission sections 16-18, 22-23: weekly/season report construction."""

from __future__ import annotations

import sys
from datetime import UTC, datetime

sys.path.insert(0, "tests")
from research_factories import make_corpus_row, make_observation  # noqa: E402

from cfb_edge_finder.research import reporting
from cfb_edge_finder.research.settlement import extract_game_result, settle_market
from cfb_edge_finder.schemas.common import MarketFamily, Side

NOW = datetime(2026, 9, 6, tzinfo=UTC)


def _row(ticker: str, game_id: str, family: MarketFamily = MarketFamily.MONEYLINE, **overrides):
    obs = make_observation(kalshi_market_ticker=ticker, game_id=game_id, family=family, team=Side.HOME, **overrides)
    return make_corpus_row(observation=obs)


def test_weekly_report_counts_games_and_contracts():
    rows = [
        _row("MKT-1", "cfb-2026-wk01-a-at-b"),
        _row("MKT-2", "cfb-2026-wk01-a-at-b"),
        _row("MKT-3", "cfb-2026-wk01-c-at-d"),
    ]
    report = reporting.build_weekly_report(
        season=2026, week_label="wk01", rows=rows, settlement_rows=[], generated_at=NOW
    )
    assert report.games_captured == 2
    assert report.contracts_captured == 3


def test_weekly_report_family_coverage():
    rows = [
        _row("MKT-1", "cfb-2026-wk01-a-at-b", family=MarketFamily.SPREAD),
        _row("MKT-2", "cfb-2026-wk01-a-at-b", family=MarketFamily.TOTAL),
    ]
    report = reporting.build_weekly_report(
        season=2026, week_label="wk01", rows=rows, settlement_rows=[], generated_at=NOW
    )
    assert report.family_coverage == {"spread": 1, "total": 1}


def test_weekly_report_settled_observations_counted():
    obs = make_observation(
        kalshi_market_ticker="MKT-1", game_id="cfb-2026-wk01-a-at-b", family=MarketFamily.MONEYLINE, team=Side.HOME
    )
    row = make_corpus_row(observation=obs)
    result = extract_game_result(
        {"status": "final", "homePoints": 31, "awayPoints": 24},
        game_id="cfb-2026-wk01-a-at-b", season=2026, captured_at=NOW,
    )
    settlement = settle_market(obs, result, settled_at=NOW)

    report = reporting.build_weekly_report(
        season=2026, week_label="wk01", rows=[row], settlement_rows=[settlement], generated_at=NOW
    )
    assert report.settled_observations == 1


def test_weekly_report_gap_bucket_distribution_covers_all_buckets():
    rows = [_row("MKT-1", "cfb-2026-wk01-a-at-b", research_probability_gap=0.01)]
    report = reporting.build_weekly_report(
        season=2026, week_label="wk01", rows=rows, settlement_rows=[], generated_at=NOW
    )
    labels = {g.bucket for g in report.gap_bucket_distribution}
    assert labels == {"<2%", "2-5%", "5-8%", "8-12%", "12%+"}
    small_bucket = next(g for g in report.gap_bucket_distribution if g.bucket == "<2%")
    assert small_bucket.contract_level_n == 1


def test_weekly_report_mapping_errors_counted():
    obs = make_observation(
        kalshi_market_ticker="MKT-1", game_id=None, parse_status="unresolved", family=None,
        model_probability=None, model_version=None, coverage_outcome="ticker_unresolved", pricing_status="not_priced",
    )
    row = make_corpus_row(observation=obs)
    report = reporting.build_weekly_report(
        season=2026, week_label="wk01", rows=[row], settlement_rows=[], generated_at=NOW
    )
    assert report.mapping_errors == 1


def test_season_report_aggregates_across_weeks_and_versions_incrementally():
    rows_v1 = [_row("MKT-1", "cfb-2026-wk01-a-at-b")]
    report_v1 = reporting.build_season_report(
        season=2026, report_version=1, all_rows=rows_v1, settlement_rows=[], weeks_included=["wk01"],
        generated_at=NOW,
    )
    assert report_v1.report_version == 1
    assert report_v1.total_observations == 1

    rows_v2 = rows_v1 + [_row("MKT-2", "cfb-2026-wk02-e-at-f")]
    report_v2 = reporting.build_season_report(
        season=2026, report_version=2, all_rows=rows_v2, settlement_rows=[], weeks_included=["wk01", "wk02"],
        generated_at=NOW,
    )
    assert report_v2.report_version == 2
    assert report_v2.total_observations == 2
    assert report_v1.total_observations == 1  # prior version object untouched -- never mutated


def test_season_report_model_version_history():
    rows = [_row("MKT-1", "cfb-2026-wk01-a-at-b")]
    report = reporting.build_season_report(
        season=2026, report_version=1, all_rows=rows, settlement_rows=[], weeks_included=["wk01"], generated_at=NOW
    )
    assert "test-model-1.0" in report.model_version_history


def test_weekly_report_never_contains_a_bet_recommendation_field():
    rows = [_row("MKT-1", "cfb-2026-wk01-a-at-b")]
    report = reporting.build_weekly_report(
        season=2026, week_label="wk01", rows=rows, settlement_rows=[], generated_at=NOW
    )
    dumped = report.model_dump()
    forbidden = ("bet", "stake", "play", "tier")
    for key in dumped:
        lowered = key.lower()
        assert not any(f in lowered for f in forbidden), f"field {key!r} looks like a recommendation field"
