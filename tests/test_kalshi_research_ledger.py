"""ResearchLedger append-only/no-duplicate/coverage-accounting behavior,
and ResearchReadiness derivation (mission section 19)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason, to_coverage_outcome
from cfb_edge_finder.kalshi.research_ledger import DuplicateObservationError, ResearchLedger, derive_research_readiness
from cfb_edge_finder.schemas.kalshi_observation import KalshiResearchObservation, SnapshotTiming
from cfb_edge_finder.schemas.provenance import DataProvenance

NOW = datetime(2026, 8, 25, tzinfo=UTC)
PROVENANCE = DataProvenance(schedule_source="cfbd", data_timestamp=NOW)
TIMING = SnapshotTiming(label="EARLY_OPEN")


def make_observation(**overrides) -> KalshiResearchObservation:
    defaults = dict(
        snapshot_id="snap-1",
        captured_at=NOW,
        snapshot_timing=TIMING,
        game_id="g1",
        kalshi_event_ticker="EVT-1",
        kalshi_market_ticker="MKT-1",
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


# --- append-only / duplicate rejection ------------------------------------


def test_append_and_len():
    ledger = ResearchLedger()
    ledger.append(make_observation())
    assert len(ledger) == 1


def test_duplicate_snapshot_and_ticker_rejected():
    ledger = ResearchLedger()
    ledger.append(make_observation())
    with pytest.raises(DuplicateObservationError):
        ledger.append(make_observation())


def test_same_ticker_different_snapshot_is_allowed():
    ledger = ResearchLedger()
    ledger.append(make_observation(snapshot_id="snap-1"))
    ledger.append(make_observation(snapshot_id="snap-2"))
    assert len(ledger) == 2


def test_different_ticker_same_snapshot_is_allowed():
    ledger = ResearchLedger()
    ledger.append(make_observation(kalshi_market_ticker="MKT-1"))
    ledger.append(make_observation(kalshi_market_ticker="MKT-2"))
    assert len(ledger) == 2


# --- coverage accounting sums exactly ------------------------------------


def test_coverage_outcome_counts_sum_to_row_count():
    ledger = ResearchLedger()
    ledger.append(make_observation(kalshi_market_ticker="MKT-1"))
    ledger.append(
        make_observation(
            kalshi_market_ticker="MKT-2",
            coverage_outcome=to_coverage_outcome(KalshiCfbCoverageReason.MAPPED_SUPPORTED),
            coverage_reason=KalshiCfbCoverageReason.MAPPED_SUPPORTED.value,
        )
    )
    counts = ledger.coverage_outcome_counts()
    assert sum(counts.values()) == len(ledger) == 2


def test_rows_for_snapshot_filters_correctly():
    ledger = ResearchLedger()
    ledger.append(make_observation(snapshot_id="snap-1", kalshi_market_ticker="MKT-1"))
    ledger.append(make_observation(snapshot_id="snap-2", kalshi_market_ticker="MKT-1"))
    assert len(ledger.rows_for_snapshot("snap-1")) == 1
    assert len(ledger.rows_for_snapshot("snap-2")) == 1


# --- research readiness derivation -----------------------------------------


def test_readiness_unresolved_when_parse_failed():
    obs = make_observation(parse_status="unresolved")
    assert derive_research_readiness(obs) == "unresolved"


def test_readiness_unsupported_when_pricing_status_unsupported():
    obs = make_observation(
        parse_status="confirmed_live",
        pricing_status="unsupported_population",
        family=None,
        game_id="g1",
    )
    assert derive_research_readiness(obs) == "unsupported"


def test_readiness_research_comparable_when_fully_priced_and_gapped():
    from cfb_edge_finder.schemas.common import MarketFamily
    from cfb_edge_finder.schemas.projection import UncertaintyProfile
    from cfb_edge_finder.schemas.provenance import ModelVersion

    obs = make_observation(
        parse_status="confirmed_live",
        pricing_status="model_priced",
        family=MarketFamily.SPREAD,
        model_probability=0.6,
        executable_yes_price=0.5,
        research_probability_gap=0.1,
        model_version=ModelVersion(model_version="v1", pricing_engine_version="0.1.0"),
        uncertainty=UncertaintyProfile(data_completeness=1.0, qb_status_confirmed=True, early_season_prior_weight=0.0),
        coverage_outcome=to_coverage_outcome(KalshiCfbCoverageReason.MAPPED_SUPPORTED),
        coverage_reason=KalshiCfbCoverageReason.MAPPED_SUPPORTED.value,
    )
    assert derive_research_readiness(obs) == "research_comparable"


def test_readiness_model_priced_without_gap_when_no_executable_price():
    from cfb_edge_finder.schemas.common import MarketFamily

    obs = make_observation(
        parse_status="confirmed_live",
        pricing_status="model_priced",
        family=MarketFamily.SPREAD,
        model_probability=0.6,
        executable_yes_price=None,
        research_probability_gap=None,
        coverage_outcome=to_coverage_outcome(KalshiCfbCoverageReason.MAPPED_SUPPORTED),
        coverage_reason=KalshiCfbCoverageReason.MAPPED_SUPPORTED.value,
    )
    assert derive_research_readiness(obs) == "model_priced"


def test_readiness_semantics_verified_when_parsed_but_not_priced():
    from cfb_edge_finder.schemas.common import MarketFamily

    obs = make_observation(
        parse_status="confirmed_live",
        pricing_status="not_priced",
        family=MarketFamily.SPREAD,
        game_id="g1",
    )
    assert derive_research_readiness(obs) == "semantics_verified"


def test_readiness_unresolved_when_game_id_missing():
    obs = make_observation(parse_status="confirmed_live", pricing_status="not_priced", game_id=None)
    assert derive_research_readiness(obs) == "unresolved"


def test_derive_research_readiness_never_returns_a_recommendation_state():
    from cfb_edge_finder.kalshi.research_ledger import ResearchReadiness

    valid_values = {r.value for r in ResearchReadiness}
    assert valid_values.isdisjoint({"watch", "early_value", "actionable", "pass"})
