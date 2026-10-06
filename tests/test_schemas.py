import math
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from cfb_edge_finder.ids import canonical_game_id
from cfb_edge_finder.schemas.common import CoverageOutcome, MarketFamily, SeasonType, Side
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
NAIVE_NOW = datetime(2026, 8, 22)


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


@pytest.mark.parametrize("field", ["kickoff_utc", "discovered_at", "last_updated_at"])
def test_game_record_rejects_naive_datetimes(field):
    with pytest.raises(ValidationError):
        make_game_record(**{field: NAIVE_NOW})


# --- Neutral-site semantics ---


def test_game_record_represents_neutral_site_game_explicitly():
    game = make_game_record(
        game_id=canonical_game_id(2026, "wk01", "baylor", "auburn", neutral_site=True),
        neutral_site=True,
        venue="Mercedes-Benz Stadium",
    )
    assert game.neutral_site is True
    assert game.venue == "Mercedes-Benz Stadium"
    # home/away are still populated -- bookkeeping designation only, never
    # to be read as evidence of a real home-field edge (see
    # cfb_edge_finder.ratings.home_field_advantage_points).
    assert game.home_team_id == "auburn"
    assert game.away_team_id == "baylor"


def test_game_record_neutral_site_game_id_must_use_neutral_form():
    # A neutral-site GameRecord constructed with the site-based (away-at-home)
    # id form must be rejected -- the id and neutral_site flag must agree.
    with pytest.raises(ValidationError):
        make_game_record(
            game_id=canonical_game_id(2026, "wk01", "baylor", "auburn", neutral_site=False),
            neutral_site=True,
        )


def test_home_field_advantage_points_is_zero_on_neutral_site():
    from cfb_edge_finder.ratings import home_field_advantage_points

    assert home_field_advantage_points(base_hfa=2.5, neutral_site=True) == 0.0
    assert home_field_advantage_points(base_hfa=2.5, neutral_site=False) == 2.5


# --- GameDistribution safety ---


def test_game_distribution_requires_positive_standard_deviations():
    with pytest.raises(ValidationError):
        GameDistribution(home_mean=28, away_mean=24, home_sd=0, away_sd=10)


def test_game_distribution_correlation_bounded():
    with pytest.raises(ValidationError):
        GameDistribution(home_mean=28, away_mean=24, home_sd=10, away_sd=10, correlation=1.5)


def test_game_distribution_rejects_negative_mean():
    with pytest.raises(ValidationError):
        GameDistribution(home_mean=-1, away_mean=24, home_sd=10, away_sd=10)


@pytest.mark.parametrize("value", [math.nan, math.inf, -math.inf])
def test_game_distribution_rejects_non_finite_mean(value):
    with pytest.raises(ValidationError):
        GameDistribution(home_mean=value, away_mean=24, home_sd=10, away_sd=10)


@pytest.mark.parametrize("value", [math.inf])
def test_game_distribution_rejects_infinite_standard_deviation(value):
    # NaN sd is already excluded by gt=0 (nan > 0 is False); infinite sd is
    # the gap that constraint alone doesn't close (inf > 0 is True).
    with pytest.raises(ValidationError):
        GameDistribution(home_mean=28, away_mean=24, home_sd=value, away_sd=10)


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


def test_projection_record_rejects_naive_projection_timestamp():
    with pytest.raises(ValidationError):
        make_projection_record(projection_timestamp=NAIVE_NOW)


def test_projection_record_requires_model_version_and_provenance():
    # Both are required (no default), so a stored ProjectionRecord can never
    # silently omit the fields needed to answer "what did the model know at
    # the time it made this estimate?" (mission section 8/9).
    with pytest.raises(ValidationError):
        ProjectionRecord(
            projection_id="proj-1",
            game_id=canonical_game_id(2026, "wk01", "baylor", "auburn"),
            projection_timestamp=NOW,
            distribution=GameDistribution(home_mean=28, away_mean=24, home_sd=10, away_sd=10),
            uncertainty=UncertaintyProfile(
                data_completeness=0.8, qb_status_confirmed=True, early_season_prior_weight=0.5
            ),
        )


def test_uncertainty_profile_bounds():
    with pytest.raises(ValidationError):
        UncertaintyProfile(data_completeness=1.5, qb_status_confirmed=True, early_season_prior_weight=0.5)


def test_market_record_accepts_unmapped_market():
    record = MarketRecord(
        market_ticker="KXNCAAFGAME-26AUG29UNCTCU-UNC",
        discovered_at=NOW,
        last_seen_at=NOW,
        coverage_outcome=CoverageOutcome.DISCOVERED,
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
        coverage_outcome=CoverageOutcome.EVALUATED,
    )
    assert record.market_family == MarketFamily.SPREAD


def test_market_record_team_total_requires_team():
    with pytest.raises(ValidationError):
        MarketRecord(
            market_ticker="KXNCAAFTEAMTOTAL-X",
            market_family=MarketFamily.TEAM_TOTAL,
            side=Side.OVER,
            line=27.5,
            discovered_at=NOW,
            last_seen_at=NOW,
            coverage_outcome=CoverageOutcome.EVALUATED,
        )


def test_market_record_team_total_with_team_is_valid():
    record = MarketRecord(
        market_ticker="KXNCAAFTEAMTOTAL-X",
        market_family=MarketFamily.TEAM_TOTAL,
        side=Side.OVER,
        team=Side.HOME,
        line=27.5,
        discovered_at=NOW,
        last_seen_at=NOW,
        coverage_outcome=CoverageOutcome.EVALUATED,
    )
    assert record.team == Side.HOME


def test_market_record_rejects_team_on_non_team_total_market():
    with pytest.raises(ValidationError):
        MarketRecord(
            market_ticker="KXNCAAFGAME-X",
            market_family=MarketFamily.SPREAD,
            side=Side.HOME,
            team=Side.HOME,
            line=-3.5,
            discovered_at=NOW,
            last_seen_at=NOW,
            coverage_outcome=CoverageOutcome.EVALUATED,
        )


def test_market_record_rejects_readiness_without_evaluated_outcome():
    from cfb_edge_finder.schemas.common import RecommendationReadiness

    with pytest.raises(ValidationError):
        MarketRecord(
            market_ticker="KXNCAAFGAME-X",
            discovered_at=NOW,
            last_seen_at=NOW,
            coverage_outcome=CoverageOutcome.MISSING_INPUT,
            recommendation_readiness=RecommendationReadiness.WATCH,
        )


def test_coverage_ledger_entry_requires_nonempty_history():
    with pytest.raises(ValidationError):
        CoverageLedgerEntry(market_ticker="X", current_outcome=CoverageOutcome.DISCOVERED, history=[])


def test_coverage_ledger_entry_requires_current_outcome_matches_last_transition():
    with pytest.raises(ValidationError):
        CoverageLedgerEntry(
            market_ticker="X",
            current_outcome=CoverageOutcome.EVALUATED,
            history=[StatusTransition(outcome=CoverageOutcome.DISCOVERED, at=NOW)],
        )
