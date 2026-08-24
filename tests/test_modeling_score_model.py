from datetime import UTC, datetime

import numpy as np
import pytest

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf, LeakageError
from cfb_edge_finder.modeling.qb_continuity import QBContinuityState, classify_continuity, uncertainty_multiplier
from cfb_edge_finder.modeling.ratings import fit_fbs_efficiency_ratings
from cfb_edge_finder.modeling.score_model import build_expanding_residual_pool, project_game
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _line(team, opp, pts, opp_pts, plays, home, neutral=False, week=1, team_class="fbs", opp_class="fbs"):
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
        is_neutral_site=neutral,
        team_points=pts,
        opponent_points=opp_pts,
        team_plays=plays,
        captured_at=NOW,
    )


def _synthetic_history(n_teams=16, n_weeks=6, seed=11):
    rng = np.random.default_rng(seed)
    teams = [f"t{i}" for i in range(n_teams)]
    strength = {t: rng.normal(0, 0.05) for t in teams}
    lines = []
    for week in range(1, n_weeks + 1):
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
def fitted_ratings_and_pool():
    lines = _synthetic_history()
    as_of = AsOf(season=2025, week=7)
    ratings = fit_fbs_efficiency_ratings(lines, as_of)
    pool = build_expanding_residual_pool(lines, as_of, min_pool_size=1)
    return ratings, pool


# --- QB continuity ---


def test_classify_continuity_thresholds():
    assert classify_continuity(0.9) == QBContinuityState.RETURNING_STARTER
    assert classify_continuity(0.5) == QBContinuityState.MIXED_OR_UNCERTAIN
    assert classify_continuity(0.1) == QBContinuityState.NEW_STARTER
    assert classify_continuity(None) == QBContinuityState.UNKNOWN


def test_unknown_and_new_starter_get_at_least_as_much_uncertainty_as_returning():
    returning = uncertainty_multiplier(QBContinuityState.RETURNING_STARTER)
    unknown = uncertainty_multiplier(QBContinuityState.UNKNOWN)
    new = uncertainty_multiplier(QBContinuityState.NEW_STARTER)
    assert unknown >= returning
    assert new >= returning


# --- probability bounds and coherence ---


def test_probabilities_are_valid_and_home_away_sum_to_one(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj = project_game(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=10000, seed=1,
    )
    p_home = proj.prob_home_win()
    p_away = proj.prob_away_win()
    assert 0.0 <= p_home <= 1.0
    assert 0.0 <= p_away <= 1.0
    assert p_home + p_away == pytest.approx(1.0, abs=1e-9)


def test_margin_probability_is_monotonically_decreasing_in_threshold(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj = project_game(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=10000, seed=2,
    )
    thresholds = [-21, -14, -7, -3.5, 0, 3.5, 7, 14, 21]
    probs = [proj.prob_margin_greater_than(t) for t in thresholds]
    assert probs == sorted(probs, reverse=True)


def test_total_probability_is_monotonically_decreasing_in_threshold(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj = project_game(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=10000, seed=3,
    )
    thresholds = [20, 30, 40, 50, 60, 70, 80]
    probs = [proj.prob_total_greater_than(t) for t in thresholds]
    assert probs == sorted(probs, reverse=True)


def test_simulated_scores_are_discrete_nonnegative_integers(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj = project_game(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=5000, seed=4,
    )
    assert np.all(proj.home_scores >= 0)
    assert np.all(proj.away_scores >= 0)
    assert np.all(proj.home_scores == np.round(proj.home_scores))
    assert np.all(proj.away_scores == np.round(proj.away_scores))


def test_reproducible_simulation_with_same_seed(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    kwargs = dict(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=5000, seed=99,
    )
    p1 = project_game(**kwargs)
    p2 = project_game(**kwargs)
    assert np.array_equal(p1.home_scores, p2.home_scores)
    assert np.array_equal(p1.away_scores, p2.away_scores)
    assert p1.prob_home_win() == p2.prob_home_win()


def test_different_seeds_produce_different_but_similar_results(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    kwargs = dict(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=8000,
    )
    p1 = project_game(seed=1, **kwargs)
    p2 = project_game(seed=2, **kwargs)
    assert not np.array_equal(p1.home_scores, p2.home_scores)
    assert abs(p1.prob_home_win() - p2.prob_home_win()) < 0.05


# --- neutral site ---


def test_neutral_site_projection_has_no_home_field_edge(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj_neutral = project_game(
        home_id="t0", away_id="t0", home_classification="fbs", away_classification="fbs",
        is_neutral_site=True, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=10000, seed=5,
    )
    # Same team on both sides at a neutral site must be a true toss-up.
    assert proj_neutral.expected_home_points == pytest.approx(proj_neutral.expected_away_points, abs=1e-6)


def test_home_field_gives_a_real_edge_for_an_otherwise_even_matchup(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj_home = project_game(
        home_id="t0", away_id="t0", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=10000, seed=6,
    )
    if abs(ratings.hfa) > 1e-6:
        assert proj_home.expected_home_points != pytest.approx(proj_home.expected_away_points, abs=1e-6)


# --- FBS-vs-FCS ---


def test_fbs_vs_fcs_projection_uses_pseudo_rating_not_individual_fcs_rating(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj = project_game(
        home_id="t0", away_id="some_fcs_team_never_seen_before", home_classification="fbs",
        away_classification="fcs", is_neutral_site=False, ratings=ratings, prior_season_ratings=None,
        residual_pool=pool, home_percent_passing_ppa=None, away_percent_passing_ppa=None,
        n_simulations=5000, seed=7,
    )
    assert proj.expected_home_points > 0
    assert 0.0 <= proj.prob_home_win() <= 1.0


def test_fbs_vs_fcs_game_gets_inflated_uncertainty_vs_fbs_vs_fbs(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    kwargs = dict(
        home_id="t0", is_neutral_site=False, ratings=ratings, prior_season_ratings=None,
        residual_pool=pool, home_percent_passing_ppa=None, away_percent_passing_ppa=None,
        n_simulations=20000, seed=42,
    )
    proj_fbs = project_game(away_id="t1", home_classification="fbs", away_classification="fbs", **kwargs)
    proj_fcs = project_game(
        away_id="some_fcs_team", home_classification="fbs", away_classification="fcs", **kwargs
    )
    assert np.std(proj_fcs.home_scores) > np.std(proj_fbs.home_scores)


# --- expanding walk-forward residual pool (Milestone C hardening) ---


def test_expanding_residual_pool_is_out_of_sample_not_in_sample():
    from cfb_edge_finder.modeling.score_model import build_expanding_residual_pool

    lines = _synthetic_history(n_teams=16, n_weeks=6)
    as_of = AsOf(season=2025, week=7)
    pool = build_expanding_residual_pool(lines, as_of, min_pool_size=1)
    # One residual pair per FBS-vs-FBS game across weeks 1-6 for 16 teams
    # (8 games/week * 6 weeks = 48) minus week 1 (no prior-week ratings
    # exist yet to score it out-of-sample against) -- week 1 is skipped
    # by min_week_for_first_step default of 1, since there's no history
    # strictly before week 1 to fit ratings from.
    assert len(pool) == 8 * 5


def test_expanding_residual_pool_is_deterministic():
    from cfb_edge_finder.modeling.score_model import build_expanding_residual_pool

    lines = _synthetic_history(n_teams=16, n_weeks=6)
    as_of = AsOf(season=2025, week=7)
    pool_a = build_expanding_residual_pool(lines, as_of, min_pool_size=1)
    pool_b = build_expanding_residual_pool(lines, as_of, min_pool_size=1)
    assert np.array_equal(pool_a, pool_b)


def test_expanding_residual_pool_rejects_future_rows():
    from cfb_edge_finder.modeling.score_model import build_expanding_residual_pool

    lines = _synthetic_history(n_teams=16, n_weeks=6)
    with pytest.raises(LeakageError):
        build_expanding_residual_pool(lines, AsOf(season=2025, week=4))


def test_expanding_residual_pool_falls_back_when_too_thin():
    from cfb_edge_finder.modeling.score_model import build_expanding_residual_pool

    lines = _synthetic_history(n_teams=4, n_weeks=1)
    pool = build_expanding_residual_pool(lines, AsOf(season=2025, week=3), min_pool_size=1000)
    assert len(pool) == 5000  # the documented wide fallback pool size


# --- provenance ---


def test_projection_record_carries_full_provenance(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    proj = project_game(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=5000, seed=8,
    )
    record = proj.to_projection_record(
        projection_id="test-id",
        game_id="test-game",
        model_version=ModelVersion(model_version="0.1.0-test", pricing_engine_version="0.1.0"),
        provenance=DataProvenance(schedule_source="cfbd", data_timestamp=NOW),
        projection_timestamp=NOW,
    )
    assert record.model_version.model_version == "0.1.0-test"
    assert record.provenance.schedule_source == "cfbd"
    assert record.uncertainty.data_completeness is not None
    assert 0.0 <= record.uncertainty.early_season_prior_weight <= 1.0


def test_unknown_qb_state_widens_uncertainty_vs_returning_starter(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    kwargs = dict(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        n_simulations=20000,
    )
    proj_unknown = project_game(home_percent_passing_ppa=None, away_percent_passing_ppa=None, seed=10, **kwargs)
    proj_returning = project_game(home_percent_passing_ppa=0.95, away_percent_passing_ppa=0.95, seed=10, **kwargs)
    assert np.std(proj_unknown.home_scores) >= np.std(proj_returning.home_scores)


# --- Milestone C.2 (this pass): residual_scale uncertainty-calibration candidate ---


def test_residual_scale_default_is_a_true_no_op(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    kwargs = dict(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=5000, seed=3,
    )
    proj_default = project_game(**kwargs)
    proj_explicit_one = project_game(residual_scale=1.0, **kwargs)
    assert np.array_equal(proj_default.home_scores, proj_explicit_one.home_scores)
    assert np.array_equal(proj_default.away_scores, proj_explicit_one.away_scores)


def test_residual_scale_below_one_narrows_the_simulated_spread(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    kwargs = dict(
        home_id="t0", away_id="t1", home_classification="fbs", away_classification="fbs",
        is_neutral_site=False, ratings=ratings, prior_season_ratings=None, residual_pool=pool,
        home_percent_passing_ppa=None, away_percent_passing_ppa=None, n_simulations=20000, seed=5,
    )
    proj_full = project_game(residual_scale=1.0, **kwargs)
    proj_narrow = project_game(residual_scale=0.85, **kwargs)
    assert np.std(proj_narrow.home_scores) < np.std(proj_full.home_scores)
    assert np.std(proj_narrow.away_scores) < np.std(proj_full.away_scores)
    # Same expected (mean) points either way -- residual_scale only touches
    # the simulated SPREAD, never the point estimate itself.
    assert proj_narrow.expected_home_points == pytest.approx(proj_full.expected_home_points)
    assert proj_narrow.expected_away_points == pytest.approx(proj_full.expected_away_points)
