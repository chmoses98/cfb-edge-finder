"""End-to-end ladder pricing tests (mission sections 16/17): one game
projection, reused to price an entire spread/total ladder cheaply and
consistently, with monotonicity checked directly against the model's own
probabilities."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pytest

from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason
from cfb_edge_finder.kalshi.game_mapping import KalshiGameMappingResult
from cfb_edge_finder.kalshi.game_projection_cache import GameProjectionCache, GameProjectionRequest
from cfb_edge_finder.kalshi.ladder_pricing import price_one_market
from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.schemas.common import MarketFamily
from cfb_edge_finder.schemas.kalshi_observation import SnapshotTiming
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion

NOW = datetime(2026, 8, 23, tzinfo=UTC)
CAPTURED_AT = NOW
SNAPSHOT_ID = "snap-test-1"
SNAPSHOT_TIMING = SnapshotTiming(label="EARLY_OPEN")
PROVENANCE = DataProvenance(schedule_source="cfbd", data_timestamp=NOW)
MODEL_VERSION = ModelVersion(model_version="test-version", pricing_engine_version="0.1.0")


def _line(team, opp, pts, opp_pts, plays, home, team_class="fbs", opp_class="fbs", week=1):
    return TeamGameLine(
        source_game_id=f"{'-'.join(sorted([team, opp]))}-{week}",
        season=2025,
        week=week,
        is_postseason=False,
        team_id=team,
        opponent_id=opp,
        team_classification=team_class,
        opponent_classification=opp_class,
        is_home=home,
        is_neutral_site=False,
        team_points=pts,
        opponent_points=opp_pts,
        team_plays=plays,
        captured_at=NOW,
    )


def _synthetic_history(seed=7):
    rng = np.random.default_rng(seed)
    teams = ["ohio-state", "texas", *[f"filler{i}" for i in range(14)]]
    strength = {t: rng.normal(0, 0.05) for t in teams}
    lines = []
    for week in range(1, 7):
        shuffled = teams[:]
        rng.shuffle(shuffled)
        for i in range(0, len(shuffled), 2):
            home, away = shuffled[i], shuffled[i + 1]
            home_pts = max(int(rng.normal(28 + strength[home] * 180 + 2, 9)), 0)
            away_pts = max(int(rng.normal(24 + strength[away] * 180, 9)), 0)
            lines.append(_line(home, away, home_pts, away_pts, 68, True, week=week))
            lines.append(_line(away, home, away_pts, home_pts, 66, False, week=week))
    return lines


@pytest.fixture(scope="module")
def cached_projection():
    cache = GameProjectionCache(_synthetic_history())
    request = GameProjectionRequest(
        game_id="ohio-state-vs-texas",
        home_id="ohio-state",
        away_id="texas",
        home_classification="fbs",
        away_classification="fbs",
        is_neutral_site=False,
        as_of_season=2025,
        as_of_week=7,
        n_simulations=2000,
        seed=0,
    )
    return cache.get_or_build(request)


SUCCESSFUL_MAPPING = KalshiGameMappingResult(
    reason=None,
    game_id="ohio-state-vs-texas",
    detail="unique match",
    home_team_id="ohio-state",
    away_team_id="texas",
)


def _spread_market(ticker, team_name, threshold):
    return {
        "ticker": ticker,
        "title": f"{team_name} wins by over {threshold} points",
        "floor_strike": threshold,
        "yes_bid_dollars": "0.10",
        "yes_ask_dollars": "0.50",
        "no_bid_dollars": "0.50",
        "no_ask_dollars": "0.90",
    }


def _total_market(ticker, threshold):
    return {
        "ticker": ticker,
        "title": f"Over {threshold} points scored",
        "floor_strike": threshold,
        "yes_bid_dollars": "0.10",
        "yes_ask_dollars": "0.50",
        "no_bid_dollars": "0.50",
        "no_ask_dollars": "0.90",
    }


def _price(market, family, mapping, home_cls="fbs", away_cls="fbs", cached=None):
    return price_one_market(
        market,
        family_hint=family,
        event_ticker="EVT-TEST",
        mapping=mapping,
        home_classification=home_cls,
        away_classification=away_cls,
        cached_projection=cached,
        captured_at=CAPTURED_AT,
        snapshot_id=SNAPSHOT_ID,
        snapshot_timing=SNAPSHOT_TIMING,
        model_version=MODEL_VERSION,
        training_cutoff="test",
        provenance=PROVENANCE,
    )


# --- happy path: MAPPED_SUPPORTED, model-priced ---------------------------


def test_mapped_supported_spread_is_model_priced(cached_projection):
    market = _spread_market("SPREAD-3.5", "Ohio State", 3.5)
    obs = _price(market, MarketFamily.SPREAD, SUCCESSFUL_MAPPING, cached=cached_projection)
    assert obs.coverage_reason == KalshiCfbCoverageReason.MAPPED_SUPPORTED.value
    assert obs.pricing_status == "model_priced"
    assert obs.model_probability is not None
    assert 0.0 <= obs.model_probability <= 1.0
    assert obs.executable_yes_price == 0.50
    assert obs.research_probability_gap == pytest.approx(obs.model_probability - 0.50)


# --- one projection reused across the whole ladder -------------------------


def test_one_cached_projection_prices_the_whole_ladder_consistently(cached_projection):
    from cfb_edge_finder.projections.distribution import price_market
    from cfb_edge_finder.schemas.common import Side

    dist = cached_projection.projection.to_game_distribution()
    for threshold in (0.5, 3.5, 7.5, 13.5):
        obs = _price(
            _spread_market(f"SPREAD-{threshold}", "Ohio State", threshold),
            MarketFamily.SPREAD,
            SUCCESSFUL_MAPPING,
            cached=cached_projection,
        )
        expected = price_market(dist, MarketFamily.SPREAD, Side.HOME, line=-threshold)
        assert obs.model_probability == expected


# --- monotonicity ------------------------------------------------------------


def test_spread_ladder_probability_decreases_as_threshold_increases(cached_projection):
    thresholds = [0.5, 3.5, 7.5, 13.5, 20.5]
    probs = []
    for t in thresholds:
        market = _spread_market(f"SPREAD-{t}", "Ohio State", t)
        obs = _price(market, MarketFamily.SPREAD, SUCCESSFUL_MAPPING, cached=cached_projection)
        probs.append(obs.model_probability)
    assert probs == sorted(probs, reverse=True)
    assert all(p1 > p2 for p1, p2 in zip(probs, probs[1:], strict=False))


def test_total_ladder_probability_decreases_as_threshold_increases(cached_projection):
    thresholds = [35.5, 45.5, 55.5, 65.5]
    probs = []
    for t in thresholds:
        market = _total_market(f"TOTAL-{t}", t)
        obs = _price(market, MarketFamily.TOTAL, SUCCESSFUL_MAPPING, cached=cached_projection)
        probs.append(obs.model_probability)
    assert probs == sorted(probs, reverse=True)
    assert all(p1 > p2 for p1, p2 in zip(probs, probs[1:], strict=False))


# --- coverage classification branches --------------------------------------


def test_fbs_vs_fcs_is_unsupported_population_and_not_priced(cached_projection):
    obs = _price(
        _spread_market("SPREAD-3.5", "Ohio State", 3.5),
        MarketFamily.SPREAD,
        SUCCESSFUL_MAPPING,
        away_cls="fcs",
        cached=cached_projection,
    )
    assert obs.coverage_reason == KalshiCfbCoverageReason.MAPPED_UNSUPPORTED_POPULATION.value
    assert obs.pricing_status == "unsupported_population"
    assert obs.model_probability is None


def test_no_cached_projection_supplied_is_not_priced():
    obs = _price(_spread_market("SPREAD-3.5", "Ohio State", 3.5), MarketFamily.SPREAD, SUCCESSFUL_MAPPING, cached=None)
    assert obs.coverage_reason == KalshiCfbCoverageReason.MAPPED_SUPPORTED.value
    assert obs.pricing_status == "not_priced"
    assert obs.model_probability is None


def test_wrong_named_team_resolves_but_finds_neither_side_is_other_explicit_reason(cached_projection):
    # A team name that resolves fine in the real registry but isn't
    # either side of the ALREADY-mapped game -- a genuine, real anomaly.
    obs = _price(
        _spread_market("SPREAD-3.5", "Michigan", 3.5), MarketFamily.SPREAD, SUCCESSFUL_MAPPING, cached=cached_projection
    )
    assert obs.coverage_reason == KalshiCfbCoverageReason.OTHER_EXPLICIT_REASON.value
    assert obs.model_probability is None


def test_unparseable_market_title_is_parse_unresolved(cached_projection):
    bad_market = {
        "ticker": "SPREAD-BAD",
        "title": "Ohio State favored by 3.5",
        "floor_strike": 3.5,
        "yes_ask_dollars": "0.50",
    }
    obs = _price(bad_market, MarketFamily.SPREAD, SUCCESSFUL_MAPPING, cached=cached_projection)
    assert obs.coverage_reason == KalshiCfbCoverageReason.PARSE_UNRESOLVED.value
    assert obs.model_probability is None


def test_fcs_vs_fcs_mapping_is_unsupported_population_not_priced():
    fcs_vs_fcs_mapping = KalshiGameMappingResult(
        reason=KalshiCfbCoverageReason.FCS_VS_FCS, game_id=None, detail="both sides deterministically FCS"
    )
    obs = _price(
        _spread_market("SPREAD-3.5", "Cornell", 3.5), MarketFamily.SPREAD, fcs_vs_fcs_mapping, cached=None
    )
    assert obs.coverage_reason == KalshiCfbCoverageReason.FCS_VS_FCS.value
    assert obs.pricing_status == "unsupported_population"
    assert obs.model_probability is None
    assert obs.game_id is None


def test_failed_game_mapping_propagates_through_to_the_observation(cached_projection):
    failed_mapping = KalshiGameMappingResult(
        reason=KalshiCfbCoverageReason.AMBIGUOUS_GAME_MAPPING, game_id=None, detail="ambiguous"
    )
    obs = _price(
        _spread_market("SPREAD-3.5", "Ohio State", 3.5), MarketFamily.SPREAD, failed_mapping, cached=None
    )
    assert obs.coverage_reason == KalshiCfbCoverageReason.AMBIGUOUS_GAME_MAPPING.value
    assert obs.game_id is None


# --- executable-vs-midpoint distinction survives into the observation ------


def test_observation_keeps_executable_and_midpoint_distinct(cached_projection):
    market = _spread_market("SPREAD-3.5", "Ohio State", 3.5)
    obs = _price(market, MarketFamily.SPREAD, SUCCESSFUL_MAPPING, cached=cached_projection)
    assert obs.executable_yes_price == 0.50
    assert obs.market_midpoint == pytest.approx((0.10 + 0.50) / 2.0)
    assert obs.executable_yes_price != obs.market_midpoint
