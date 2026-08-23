from datetime import UTC, datetime

import numpy as np
import pytest

from cfb_edge_finder.modeling.backtest import compute_metrics, run_walk_forward_backtest, segment
from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.modeling.naive_benchmark import fit_naive_benchmark, naive_expected_scores

NOW = datetime(2026, 8, 23, tzinfo=UTC)


def _line(team, opp, pts, opp_pts, plays, home, neutral=False, week=1, season=2025, team_class="fbs", opp_class="fbs"):
    return TeamGameLine(
        source_game_id=f"{season}-{'-'.join(sorted([team, opp]))}-{week}",
        season=season,
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


def _synthetic_corpus(n_teams=16, seasons=(2024, 2025), n_weeks=6, seed=21):
    rng = np.random.default_rng(seed)
    teams = [f"team{i}" for i in range(n_teams)]
    strength = {t: rng.normal(0, 0.05) for t in teams}
    lines = []
    for season in seasons:
        for week in range(1, n_weeks + 1):
            shuffled = teams[:]
            rng.shuffle(shuffled)
            for i in range(0, len(shuffled), 2):
                home, away = shuffled[i], shuffled[i + 1]
                home_pts = max(int(rng.normal(28 + strength[home] * 180 + 2, 9)), 0)
                away_pts = max(int(rng.normal(24 + strength[away] * 180, 9)), 0)
                lines.append(_line(home, away, home_pts, away_pts, 68, True, week=week, season=season))
                lines.append(_line(away, home, away_pts, home_pts, 66, False, week=week, season=season))
    return lines


def test_naive_benchmark_leakage_check_raises_on_future_row():
    from cfb_edge_finder.modeling.leakage import LeakageError

    future = _line("a", "b", 20, 10, 60, True, week=9)
    with pytest.raises(LeakageError):
        fit_naive_benchmark([future], AsOf(season=2025, week=3))


def test_naive_benchmark_produces_a_valid_expected_score_pair():
    lines = _synthetic_corpus(n_weeks=3, seasons=(2025,))
    naive = fit_naive_benchmark(lines, AsOf(season=2025, week=4))
    home, away = naive_expected_scores(naive, "team0", "team1", is_neutral_site=False)
    assert home >= 0
    assert away >= 0


def test_walk_forward_backtest_never_predicts_week_before_min_week():
    lines = _synthetic_corpus(n_weeks=5, seasons=(2025,))
    outcomes = run_walk_forward_backtest(lines, min_week_for_first_prediction=3, n_simulations=500, seed=0)
    assert all(o.week >= 3 for o in outcomes)


def test_walk_forward_backtest_covers_every_predicted_week_games():
    lines = _synthetic_corpus(n_teams=8, n_weeks=4, seasons=(2025,))
    outcomes = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=0)
    # 8 teams -> 4 games/week; weeks 2,3,4 predicted (week 1 has no history) = 12 games
    assert len(outcomes) == 12


def test_compute_metrics_produces_valid_probability_derived_stats():
    lines = _synthetic_corpus(n_teams=10, n_weeks=6, seasons=(2024, 2025))
    outcomes = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=1000, seed=0)
    metrics = compute_metrics(outcomes, prob_attr="model_prob_home_win")
    assert metrics.n_games == len(outcomes)
    assert metrics.winner_log_loss >= 0
    assert 0 <= metrics.winner_brier <= 1
    assert 0 <= metrics.margin_interval_coverage_90 <= 1
    assert 0 <= metrics.total_interval_coverage_90 <= 1
    for b in metrics.calibration_bins:
        assert 0 <= b["predicted_prob"] <= 1
        assert 0 <= b["observed_win_rate"] <= 1
        assert b["n"] > 0


def test_compute_metrics_raises_on_empty_outcomes():
    with pytest.raises(ValueError, match="zero outcomes"):
        compute_metrics([])


def test_segment_filters_correctly():
    lines = _synthetic_corpus(n_teams=8, n_weeks=4, seasons=(2025,))
    outcomes = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=0)
    early = segment(outcomes, lambda o: o.week <= 2)
    later = segment(outcomes, lambda o: o.week > 2)
    assert len(early) + len(later) == len(outcomes)
    assert all(o.week <= 2 for o in early)
    assert all(o.week > 2 for o in later)


def test_backtest_naive_and_model_use_the_same_held_out_games():
    lines = _synthetic_corpus(n_teams=8, n_weeks=4, seasons=(2025,))
    outcomes = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=0)
    naive_metrics = compute_metrics(outcomes, prob_attr="naive_prob_home_win")
    model_metrics = compute_metrics(outcomes, prob_attr="model_prob_home_win")
    assert naive_metrics.n_games == model_metrics.n_games
