"""Shared test-only factories for Milestone E research tests. Not a
test module itself (no test_ prefix) -- imported by tests/test_research_*.py.
"""

from __future__ import annotations

from datetime import UTC, datetime

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason, to_coverage_outcome
from cfb_edge_finder.research.identity import CAPTURE_WINDOW_VERSION, observation_key
from cfb_edge_finder.schemas.common import CoverageOutcome, MarketFamily, Side
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.data_versions import DataVersionManifest
from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation, SnapshotTiming
from cfb_edge_finder.schemas.projection import UncertaintyProfile
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion

NOW = datetime(2026, 9, 5, 18, 0, tzinfo=UTC)
PROVENANCE = DataProvenance(schedule_source="cfbd", data_timestamp=NOW)
MODEL_VERSION = ModelVersion(model_version="test-model-1.0", pricing_engine_version="0.1.0")
UNCERTAINTY = UncertaintyProfile(data_completeness=0.9, qb_status_confirmed=True, early_season_prior_weight=0.2)


def make_observation(**overrides) -> KalshiResearchObservation:
    defaults = dict(
        snapshot_id="snap-1",
        captured_at=NOW,
        snapshot_timing=SnapshotTiming(label="EARLY_OPEN"),
        game_id="cfb-2026-wk01-away-team-at-home-team",
        kalshi_event_ticker="EVT-1",
        kalshi_market_ticker="MKT-1",
        family=MarketFamily.MONEYLINE,
        team=Side.HOME,
        model_probability=0.62,
        executable_yes_price=0.55,
        research_probability_gap=0.07,
        gross_probability_gap=0.07,
        estimated_taker_fee=0.01,
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        fee_verification_status="VERIFIED_CURRENT",
        fee_adjusted_research_gap=0.06,
        fee_status="VERIFIED_CURRENT",
        model_version=MODEL_VERSION,
        training_cutoff="strictly before season=2026 week=1",
        coverage_outcome=to_coverage_outcome(KalshiCfbCoverageReason.MAPPED_SUPPORTED),
        coverage_reason=KalshiCfbCoverageReason.MAPPED_SUPPORTED.value,
        parse_status="confirmed_live",
        pricing_status="model_priced",
        provenance=PROVENANCE,
        uncertainty=UNCERTAINTY,
    )
    defaults.update(overrides)
    return KalshiResearchObservation(**defaults)


def make_unresolved_observation(**overrides) -> KalshiResearchObservation:
    defaults = dict(
        snapshot_id="snap-2",
        captured_at=NOW,
        snapshot_timing=SnapshotTiming(label="EARLY_OPEN"),
        game_id=None,
        kalshi_event_ticker="EVT-2",
        kalshi_market_ticker="MKT-2",
        family=None,
        fee_status="unverified",
        coverage_outcome=to_coverage_outcome(KalshiCfbCoverageReason.PARSE_UNRESOLVED),
        coverage_reason=KalshiCfbCoverageReason.PARSE_UNRESOLVED.value,
        parse_status="unresolved",
        pricing_status="not_priced",
        provenance=PROVENANCE,
    )
    defaults.update(overrides)
    return KalshiResearchObservation(**defaults)


def make_data_versions(**overrides) -> DataVersionManifest:
    defaults = dict(
        model_version=MODEL_VERSION.model_version,
        feature_version="features_v1_c2_ratings",
        cfbd_capture_timestamp=NOW,
        kalshi_capture_timestamp=NOW,
        mapping_version="kalshi_game_mapping_v1",
        fee_schedule_version="kalshi_fee_schedule_2026_07_07_taker",
        settlement_version=None,
        snapshot_schema_version="research_corpus_v1",
    )
    defaults.update(overrides)
    return DataVersionManifest(**defaults)


def make_corpus_row(
    *, season: int = 2026, observation: KalshiResearchObservation | None = None, **overrides
) -> ResearchCorpusRow:
    obs = observation or make_observation()
    key = observation_key(
        season=season,
        game_id=obs.game_id or "unmapped",
        market_ticker=obs.kalshi_market_ticker,
        timing_label=obs.snapshot_timing.label,
        model_version=obs.model_version.model_version if obs.model_version else "unpriced",
    )
    defaults = dict(
        observation_key=key,
        capture_window_version=CAPTURE_WINDOW_VERSION,
        season=season,
        kickoff_utc_at_capture=None,
        game_status_at_capture="scheduled",
        schedule_source_timestamp=NOW,
        data_versions=make_data_versions(),
        observation=obs,
        run_id="test-run-1",
    )
    defaults.update(overrides)
    return ResearchCorpusRow(**defaults)


__all__ = [
    "MODEL_VERSION",
    "NOW",
    "PROVENANCE",
    "UNCERTAINTY",
    "CoverageOutcome",
    "make_corpus_row",
    "make_data_versions",
    "make_observation",
    "make_unresolved_observation",
]
