from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.schemas.common import MarketFamily, MarketStatus, SeasonType, Side
from cfb_edge_finder.schemas.coverage import CoverageLedgerEntry, StatusTransition
from cfb_edge_finder.schemas.game import GameRecord
from cfb_edge_finder.schemas.market import MarketRecord
from cfb_edge_finder.schemas.projection import (
    GameDistribution,
    ProjectionRecord,
    UncertaintyProfile,
)
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion

NOW = datetime(2026, 8, 22, tzinfo=UTC)


def make_game_record(**overrides) -> GameRecord:
    defaults = dict(
        game_id=canonical_game_id(2026, "wk01", "baylor", "auburn"),
        season=2026,
        week_label="wk01",
        season_type=SeasonType.REGULAR,
        home_team_id="auburn",
        away_team_id="baylor",
        home_team_name="Auburn Tigers",
        away_team_name="Baylor Bears",
        kickoff_utc=NOW,
        discovered_at=NOW,
        last_updated_at=NOW,
    )
    defaults.update(overrides)
    return GameRecord(**defaults)


def test_game_record_round_trip_serialization_is_deterministic():
    game = make_game_record()
    dumped_1 = game.model_dump_json()
    dumped_2 = game.model_dump_json()
    assert dumped_1 == dumped_2
    restored = GameRecord.model_validate_json(dumped_1)
    assert restored == game


def test_game_record_rejects_mismatched_game_id():
    with pytest.raises(ValidationError):
        make_game_record(game_id="cfb-2026-wk01-not-the-right-id-at-auburn")


def test_game_record_rejects_malformed_week_label():
    with pytest.raises(ValueError):
        make_game_record(game_id=canonical_game_id(2026, "week1", "baylor", "auburn"), week_label="week1")


def test_game_distribution_requires_positive_standard_deviations():
    with pytest.raises(ValidationError):
        GameDistribution(home_mean=28, away_mean=24, home_sd=0, away_sd=10)


def test_game_distribution_correlation_bounded():
    with pytest.raises(ValidationError):
        GameDistribution(home_mean=28, away_mean=24, home_sd=10, away_sd=10, correlation=1.5)


def make_projection_record(**overrides) -> ProjectionRecord:
    defaults = dict(
        projection_id="proj-1",
        game_id=canonical_game_id(2026, "wk01", "baylor", "auburn"),
        model_version=ModelVersion(model_version="0.1.0", pricing_engine_version="0.1.0"),
        provenance=DataProvenance(schedule_source="cfbd", data_timestamp=NOW),
        projection_timestamp=NOW,
        distribution=GameDistribution(home_mean=28, away_mean=24, home_sd=10, away_sd=10),
        uncertainty=UncertaintyProfile(
            data_completeness=0.8, qb_status_confirmed=True, early_season_prior_weight=0.5
        ),
    )
    defaults.update(overrides)
    return ProjectionRecord(**defaults)


def test_projection_record_rejects_data_timestamp_after_projection_timestamp():
    with pytest.raises(ValidationError):
        make_projection_record(
            provenance=DataProvenance(schedule_source="cfbd", data_timestamp=datetime(2026, 8, 23, tzinfo=UTC)),
            projection_timestamp=NOW,
        )


def test_uncertainty_profile_bounds():
    with pytest.raises(ValidationError):
        UncertaintyProfile(data_completeness=1.5, qb_status_confirmed=True, early_season_prior_weight=0.5)


def test_market_record_accepts_unmapped_market():
    record = MarketRecord(
        market_ticker="KXNCAAFGAME-26AUG29UNCTCU-UNC",
        discovered_at=NOW,
        last_seen_at=NOW,
        status=MarketStatus.DISCOVERED,
    )
    assert record.game_id is None
    assert record.market_family is None


def test_market_record_accepts_full_mapping():
    record = MarketRecord(
        market_ticker="KXNCAAFGAME-26AUG29UNCTCU-UNC",
        game_id=canonical_game_id(2026, "wk01", "tcu", "unc"),
        market_family=MarketFamily.SPREAD,
        line=-3.5,
        side=Side.HOME,
        discovered_at=NOW,
        last_seen_at=NOW,
        status=MarketStatus.ACCEPTED,
    )
    assert record.market_family == MarketFamily.SPREAD


def test_coverage_ledger_entry_requires_nonempty_history():
    with pytest.raises(ValidationError):
        CoverageLedgerEntry(market_ticker="X", current_status=MarketStatus.DISCOVERED, history=[])


def test_coverage_ledger_entry_requires_current_status_matches_last_transition():
    with pytest.raises(ValidationError):
        CoverageLedgerEntry(
            market_ticker="X",
            current_status=MarketStatus.ACCEPTED,
            history=[StatusTransition(status=MarketStatus.DISCOVERED, at=NOW)],
        )
