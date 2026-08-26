"""Milestone E, Part I: weekly and season-cumulative research reports.

Pure aggregation over already-persisted `ResearchCorpusRow`/
`MarketSettlement` rows -- no capture, no network. Every number here is a
count, rate, or descriptive statistic; nothing is a stake size or a bet
recommendation (mechanically checked -- see
tests/test_qualification_hard_disabled.py).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime

from cfb_edge_finder.research.correlation import cluster_counts
from cfb_edge_finder.research.gap_buckets import GAP_BUCKET_LABELS, gap_bucket_for
from cfb_edge_finder.research.timing import ALL_PREGAME_LABELS
from cfb_edge_finder.schemas.capture_state import CaptureState
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.report import (
    GapBucketStat,
    SeasonCumulativeReport,
    TimingBucketCoverage,
    WeeklyResearchReport,
)
from cfb_edge_finder.schemas.settlement import MarketSettlement, MarketSettlementStatus


def _timing_bucket_coverage(
    rows: list[ResearchCorpusRow], capture_states: dict[tuple, str] | None
) -> list[TimingBucketCoverage]:
    captured_by_label: dict[str, int] = dict.fromkeys(ALL_PREGAME_LABELS, 0)
    for row in rows:
        label = row.observation.snapshot_timing.label
        captured_by_label[label] = captured_by_label.get(label, 0) + 1

    missed_by_label: dict[str, int] = dict.fromkeys(ALL_PREGAME_LABELS, 0)
    not_due_by_label: dict[str, int] = dict.fromkeys(ALL_PREGAME_LABELS, 0)
    if capture_states:
        for (_game_id, _ticker, label), state in capture_states.items():
            if state == CaptureState.MISSED_WINDOW:
                missed_by_label[label] = missed_by_label.get(label, 0) + 1
            elif state == CaptureState.NOT_YET_DUE:
                not_due_by_label[label] = not_due_by_label.get(label, 0) + 1

    return [
        TimingBucketCoverage(
            label=label,
            captured=captured_by_label.get(label, 0),
            missed_window=missed_by_label.get(label, 0),
            not_yet_due=not_due_by_label.get(label, 0),
        )
        for label in ALL_PREGAME_LABELS
    ]


def _settled_hit(row: ResearchCorpusRow, settlement: MarketSettlement | None) -> bool | None:
    if settlement is None or settlement.status != MarketSettlementStatus.SETTLED:
        return None
    if settlement.derived_contract_settlement is None:
        return None
    model_favors_yes = (row.observation.model_probability or 0.0) > 0.5
    return (settlement.derived_contract_settlement.value == "yes") == model_favors_yes


def _gap_bucket_stats(
    rows: list[ResearchCorpusRow], settlements: dict[tuple[str, str], MarketSettlement]
) -> list[GapBucketStat]:
    by_bucket: dict[str, list[ResearchCorpusRow]] = {label: [] for label in GAP_BUCKET_LABELS}
    for row in rows:
        gap = row.observation.research_probability_gap
        if gap is None:
            continue
        by_bucket[gap_bucket_for(gap)].append(row)

    stats: list[GapBucketStat] = []
    for label in GAP_BUCKET_LABELS:
        bucket_rows = by_bucket[label]
        if not bucket_rows:
            stats.append(GapBucketStat(bucket=label))
            continue
        counts = cluster_counts(
            (
                r.observation.kalshi_market_ticker,
                r.observation.game_id or "unknown",
                r.observation.family.value if r.observation.family else None,
            )
            for r in bucket_rows
        )
        fee_adjusted = [
            r.observation.fee_adjusted_research_gap
            for r in bucket_rows
            if r.observation.fee_adjusted_research_gap is not None
        ]
        settled_hits: list[bool] = []
        for r in bucket_rows:
            key = (r.observation.game_id or "", r.observation.kalshi_market_ticker)
            hit = _settled_hit(r, settlements.get(key))
            if hit is not None:
                settled_hits.append(hit)

        stats.append(
            GapBucketStat(
                bucket=label,
                contract_level_n=counts.contract_level_n,
                game_level_n=counts.game_level_n,
                settled_n=len(settled_hits),
                settlement_hit_rate=(sum(settled_hits) / len(settled_hits)) if settled_hits else None,
                avg_fee_adjusted_research_gap=(sum(fee_adjusted) / len(fee_adjusted)) if fee_adjusted else None,
            )
        )
    return stats


def build_weekly_report(
    *,
    season: int,
    week_label: str,
    rows: list[ResearchCorpusRow],
    settlement_rows: Iterable[MarketSettlement],
    capture_states: dict[tuple, str] | None = None,
    generated_at: datetime,
) -> WeeklyResearchReport:
    latest_settlements = {(s.game_id, s.kalshi_market_ticker): s for s in settlement_rows}

    family_coverage: dict[str, int] = {}
    for row in rows:
        if row.observation.family is not None:
            key = row.observation.family.value
            family_coverage[key] = family_coverage.get(key, 0) + 1

    closing_rows = [r for r in rows if r.observation.snapshot_timing.label == "CLOSING"]
    settled = [
        r
        for r in rows
        if latest_settlements.get((r.observation.game_id, r.observation.kalshi_market_ticker)) is not None
        and latest_settlements[(r.observation.game_id, r.observation.kalshi_market_ticker)].status
        == MarketSettlementStatus.SETTLED
    ]

    game_ids = {r.observation.game_id for r in rows if r.observation.game_id is not None}
    tickers = {r.observation.kalshi_market_ticker for r in rows}
    coverage = _timing_bucket_coverage(rows, capture_states)

    return WeeklyResearchReport(
        season=season,
        week_label=week_label,
        generated_at=generated_at,
        games_captured=len(game_ids),
        contracts_captured=len(tickers),
        timing_bucket_coverage=coverage,
        family_coverage=family_coverage,
        missing_windows=sum(tbc.missed_window for tbc in coverage),
        mapping_errors=sum(1 for r in rows if r.observation.parse_status == "unresolved"),
        gap_bucket_distribution=_gap_bucket_stats(rows, latest_settlements),
        closing_capture_exact=sum(1 for r in closing_rows if r.observation.executable_yes_price is not None),
        closing_capture_near=0,
        closing_capture_missed=0,
        settled_observations=len(settled),
    )


def build_season_report(
    *,
    season: int,
    report_version: int,
    all_rows: list[ResearchCorpusRow],
    settlement_rows: Iterable[MarketSettlement],
    weeks_included: list[str],
    generated_at: datetime,
) -> SeasonCumulativeReport:
    settlements = {(s.game_id, s.kalshi_market_ticker): s for s in settlement_rows}
    settled_n = sum(
        1
        for r in all_rows
        if (settlement := settlements.get((r.observation.game_id, r.observation.kalshi_market_ticker))) is not None
        and settlement.status == MarketSettlementStatus.SETTLED
    )
    family_counts: dict[str, int] = {}
    for r in all_rows:
        if r.observation.family is not None:
            key = r.observation.family.value
            family_counts[key] = family_counts.get(key, 0) + 1

    model_versions = sorted(
        {r.observation.model_version.model_version for r in all_rows if r.observation.model_version is not None}
    )

    label_totals: dict[str, int] = dict.fromkeys(ALL_PREGAME_LABELS, 0)
    for r in all_rows:
        label = r.observation.snapshot_timing.label
        if label in label_totals:
            label_totals[label] += 1
    total_games = max(len({r.observation.game_id for r in all_rows if r.observation.game_id is not None}), 1)
    completeness = {label: count / total_games for label, count in label_totals.items()}

    return SeasonCumulativeReport(
        season=season,
        generated_at=generated_at,
        report_version=report_version,
        total_observations=len(all_rows),
        settled_observations=settled_n,
        family_counts=family_counts,
        timing_bucket_completeness=completeness,
        gap_bucket_distribution=_gap_bucket_stats(all_rows, settlements),
        model_version_history=model_versions,
        weeks_included=weeks_included,
    )
