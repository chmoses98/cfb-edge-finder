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


# --- Milestone C hardening: expanding residual pool + calibration ---


def test_calibrated_prob_home_win_is_present_and_valid():
    lines = _synthetic_corpus(n_teams=10, n_weeks=6, seasons=(2024, 2025))
    outcomes = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=0)
    for o in outcomes:
        assert 0.0 <= o.calibrated_prob_home_win <= 1.0


def test_calibration_method_none_makes_calibrated_identical_to_raw():
    lines = _synthetic_corpus(n_teams=10, n_weeks=6, seasons=(2024, 2025))
    outcomes = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, calibration_method="none"
    )
    for o in outcomes:
        assert o.calibrated_prob_home_win == pytest.approx(o.model_prob_home_win)


def test_changing_a_weeks_own_outcome_does_not_leak_into_that_weeks_own_predictions():
    """The expanding residual pool and the calibration model must both be
    built ONLY from games strictly before the week being predicted.
    Mutating a week-3 game's own final score must not change that same
    week's OWN predictions (raw or calibrated) -- but it must propagate
    forward into later weeks (which fit ratings/residual pool/calibration
    from a history that now includes the mutated week 3), proving the
    accumulator is doing real, forward-only work rather than being an
    inert no-op.
    """
    lines_a = _synthetic_corpus(n_teams=8, n_weeks=6, seasons=(2025,), seed=5)
    target_gid = next(ln.source_game_id for ln in lines_a if ln.week == 3)

    lines_b = []
    for ln in lines_a:
        if ln.source_game_id == target_gid and ln.week == 3:
            if ln.is_home:
                lines_b.append(ln.model_copy(update={"team_points": ln.team_points + 30, "opponent_points": 0}))
            else:
                lines_b.append(ln.model_copy(update={"team_points": 0, "opponent_points": ln.opponent_points + 30}))
        else:
            lines_b.append(ln)

    outcomes_a = run_walk_forward_backtest(lines_a, min_week_for_first_prediction=2, n_simulations=800, seed=0)
    outcomes_b = run_walk_forward_backtest(lines_b, min_week_for_first_prediction=2, n_simulations=800, seed=0)

    week3_a = {o.source_game_id: o for o in outcomes_a if o.week == 3}
    week3_b = {o.source_game_id: o for o in outcomes_b if o.week == 3}
    assert week3_a.keys() == week3_b.keys()
    for gid in week3_a:
        assert week3_a[gid].model_margin_mean == pytest.approx(week3_b[gid].model_margin_mean, abs=1e-9)
        assert week3_a[gid].model_prob_home_win == pytest.approx(week3_b[gid].model_prob_home_win, abs=1e-9)
        assert week3_a[gid].calibrated_prob_home_win == pytest.approx(
            week3_b[gid].calibrated_prob_home_win, abs=1e-9
        )

    week5_a = sorted((o.source_game_id, o.model_margin_mean) for o in outcomes_a if o.week == 5)
    week5_b = sorted((o.source_game_id, o.model_margin_mean) for o in outcomes_b if o.week == 5)
    assert week5_a  # sanity: week 5 exists in both
    assert any(abs(a[1] - b[1]) > 1e-6 for a, b in zip(week5_a, week5_b, strict=True))


def test_backtest_reproducible_with_same_seed_including_calibration():
    lines = _synthetic_corpus(n_teams=10, n_weeks=6, seasons=(2024, 2025))
    outcomes_1 = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=7)
    outcomes_2 = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=7)
    for o1, o2 in zip(outcomes_1, outcomes_2, strict=True):
        assert o1.model_prob_home_win == pytest.approx(o2.model_prob_home_win, abs=1e-12)
        assert o1.calibrated_prob_home_win == pytest.approx(o2.calibrated_prob_home_win, abs=1e-12)


def test_development_only_backtest_matches_full_corpus_for_the_shared_seasons():
    """Historical-integrity audit: proves the leakage-safe chronological
    model-selection procedure (development on early seasons, confirmation
    on a later held-out season -- mission Option A) is a mathematically
    sound decomposition, not an approximation. Running the walk-forward
    backtest on a DEVELOPMENT-ONLY season subset (e.g. 2022-2024) must
    produce BIT-IDENTICAL outcomes for those seasons as the corresponding
    prefix of a run over the FULL corpus (2022-2025) -- i.e. the mere
    presence of a later, held-out confirmation season anywhere in the
    input can never change a development-season prediction. This is what
    makes it safe to select hyperparameters from a development-only run
    without ever having "seen" the confirmation season's data, even
    though every input list happens to be built from the same underlying
    synthetic corpus generator here.
    """
    full_lines = _synthetic_corpus(n_teams=8, n_weeks=5, seasons=(2022, 2023, 2024, 2025), seed=11)
    dev_only_lines = [ln for ln in full_lines if ln.season in (2022, 2023, 2024)]

    full_outcomes = run_walk_forward_backtest(full_lines, min_week_for_first_prediction=2, n_simulations=500, seed=3)
    dev_outcomes = run_walk_forward_backtest(
        dev_only_lines, min_week_for_first_prediction=2, n_simulations=500, seed=3
    )

    full_dev_seasons = {o.source_game_id: o for o in full_outcomes if o.season in (2022, 2023, 2024)}
    dev_only = {o.source_game_id: o for o in dev_outcomes}
    assert full_dev_seasons.keys() == dev_only.keys()
    assert len(dev_only) > 0  # sanity: the development seasons actually produced predictions

    for gid in dev_only:
        assert full_dev_seasons[gid].model_margin_mean == pytest.approx(
            dev_only[gid].model_margin_mean, abs=1e-9
        )
        assert full_dev_seasons[gid].model_prob_home_win == pytest.approx(
            dev_only[gid].model_prob_home_win, abs=1e-9
        )
        assert full_dev_seasons[gid].calibrated_prob_home_win == pytest.approx(
            dev_only[gid].calibrated_prob_home_win, abs=1e-9
        )

    # And the held-out 2025 season must be entirely absent from the
    # development-only run -- it was never even loaded, let alone leaked.
    assert all(o.season != 2025 for o in dev_outcomes)
    assert any(o.season == 2025 for o in full_outcomes)  # sanity: the full run does cover it


# --- Milestone C.2 Part 3: favorite-tail margin correction ---


def test_margin_correction_method_none_is_the_default_and_a_true_no_op():
    lines = _synthetic_corpus(n_teams=10, n_weeks=6, seasons=(2024, 2025))
    with_default = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=0)
    with_explicit_none = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="none"
    )
    for a, b in zip(with_default, with_explicit_none, strict=True):
        assert a.model_margin_mean == pytest.approx(b.model_margin_mean, abs=1e-12)
        assert a.model_margin_p05 == pytest.approx(b.model_margin_p05, abs=1e-12)
        assert a.model_margin_p95 == pytest.approx(b.model_margin_p95, abs=1e-12)


def test_margin_correction_never_touches_win_probability():
    # margin_calibration.py's whole design point: the correction is a
    # separate channel from win probability. Proven directly here, not
    # just asserted in a docstring -- with the SAME seed, only
    # margin/interval fields may differ between "none" and "linear".
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    none_outcomes = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="none"
    )
    linear_outcomes = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="linear"
    )
    for a, b in zip(none_outcomes, linear_outcomes, strict=True):
        assert a.model_prob_home_win == pytest.approx(b.model_prob_home_win, abs=1e-12)
        assert a.calibrated_prob_home_win == pytest.approx(b.calibrated_prob_home_win, abs=1e-12)
    # Sanity: the correction must have actually engaged for at least some
    # games (enough history accumulated) -- otherwise this test would
    # pass trivially without exercising the real code path.
    assert any(
        abs(a.model_margin_mean - b.model_margin_mean) > 1e-6
        for a, b in zip(none_outcomes, linear_outcomes, strict=True)
    )


def test_margin_correction_shifts_mean_and_both_interval_bounds_by_the_same_delta():
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    none_outcomes = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="none"
    )
    linear_outcomes = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="linear"
    )
    engaged = False
    for a, b in zip(none_outcomes, linear_outcomes, strict=True):
        delta_mean = b.model_margin_mean - a.model_margin_mean
        delta_p05 = b.model_margin_p05 - a.model_margin_p05
        delta_p95 = b.model_margin_p95 - a.model_margin_p95
        assert delta_p05 == pytest.approx(delta_mean, abs=1e-6)
        assert delta_p95 == pytest.approx(delta_mean, abs=1e-6)
        if abs(delta_mean) > 1e-6:
            engaged = True
    assert engaged  # the correction must have actually applied somewhere


def test_margin_correction_never_touches_fbs_vs_fcs_games():
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    # Add a handful of FBS-vs-FCS games each week so both classes coexist.
    fcs_lines = []
    for season in (2022, 2023, 2024):
        for week in range(2, 13):
            fcs_lines.append(
                _line("team0", "fcs-visitor", 35, 10, 68, True, week=week, season=season, opp_class="fcs")
            )
            fcs_lines.append(
                _line("fcs-visitor", "team0", 10, 35, 55, False, week=week, season=season, team_class="fcs")
            )
    all_lines = lines + fcs_lines

    none_outcomes = run_walk_forward_backtest(
        all_lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="none"
    )
    linear_outcomes = run_walk_forward_backtest(
        all_lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="linear"
    )
    none_by_id = {o.source_game_id: o for o in none_outcomes if not o.is_fbs_vs_fbs}
    linear_by_id = {o.source_game_id: o for o in linear_outcomes if not o.is_fbs_vs_fbs}
    assert none_by_id  # sanity: FCS games actually predicted
    for gid in none_by_id:
        assert none_by_id[gid].model_margin_mean == pytest.approx(linear_by_id[gid].model_margin_mean, abs=1e-9)
        assert none_by_id[gid].model_margin_p05 == pytest.approx(linear_by_id[gid].model_margin_p05, abs=1e-9)
        assert none_by_id[gid].model_margin_p95 == pytest.approx(linear_by_id[gid].model_margin_p95, abs=1e-9)


def test_margin_correction_does_not_use_a_weeks_own_or_future_outcomes():
    # Same mutation pattern as the calibration/residual-pool leakage proof
    # above, now for margin correction specifically: mutating a week-3
    # game's own final score must not change that week's OWN corrected
    # margin, but must be free to change a LATER week's correction (once
    # that mutated game enters the strictly-prior history).
    lines_a = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024), seed=5)
    target_gid = next(ln.source_game_id for ln in lines_a if ln.week == 3)

    lines_b = []
    for ln in lines_a:
        if ln.source_game_id == target_gid and ln.week == 3:
            if ln.is_home:
                lines_b.append(ln.model_copy(update={"team_points": ln.team_points + 30, "opponent_points": 0}))
            else:
                lines_b.append(ln.model_copy(update={"team_points": 0, "opponent_points": ln.opponent_points + 30}))
        else:
            lines_b.append(ln)

    outcomes_a = run_walk_forward_backtest(
        lines_a, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="linear"
    )
    outcomes_b = run_walk_forward_backtest(
        lines_b, min_week_for_first_prediction=2, n_simulations=500, seed=0, margin_correction_method="linear"
    )

    # Week 3 of the SAME season (2022) as the mutated game is the "own
    # week" check -- week numbers repeat across seasons 2022/2023/2024,
    # so this must be season-scoped, not just week-scoped.
    week3_a = {o.source_game_id: o for o in outcomes_a if o.week == 3 and o.season == 2022}
    week3_b = {o.source_game_id: o for o in outcomes_b if o.week == 3 and o.season == 2022}
    assert week3_a
    for gid in week3_a:
        assert week3_a[gid].model_margin_mean == pytest.approx(week3_b[gid].model_margin_mean, abs=1e-9)

    # A clearly LATER (season, week) -- 2024 week 12 -- must be free to
    # change, proving the mutation genuinely propagates forward once it
    # enters strictly-prior history.
    later_a = sorted((o.source_game_id, o.model_margin_mean) for o in outcomes_a if o.week == 12 and o.season == 2024)
    later_b = sorted((o.source_game_id, o.model_margin_mean) for o in outcomes_b if o.week == 12 and o.season == 2024)
    assert later_a
    assert any(abs(a[1] - b[1]) > 1e-6 for a, b in zip(later_a, later_b, strict=True))


def test_margin_correction_reproducible_with_same_seed():
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    outcomes_1 = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=7, margin_correction_method="isotonic"
    )
    outcomes_2 = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=7, margin_correction_method="isotonic"
    )
    for o1, o2 in zip(outcomes_1, outcomes_2, strict=True):
        assert o1.model_margin_mean == pytest.approx(o2.model_margin_mean, abs=1e-12)


# --- Milestone C.2 Part 3: favorite-tail/garbage-time total correction ---


def test_total_correction_method_none_is_the_default_and_a_true_no_op():
    lines = _synthetic_corpus(n_teams=10, n_weeks=6, seasons=(2024, 2025))
    with_default = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=0)
    with_explicit_none = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, total_correction_method="none"
    )
    for a, b in zip(with_default, with_explicit_none, strict=True):
        assert a.model_total_mean == pytest.approx(b.model_total_mean, abs=1e-12)
        assert a.model_total_p05 == pytest.approx(b.model_total_p05, abs=1e-12)
        assert a.model_total_p95 == pytest.approx(b.model_total_p95, abs=1e-12)


@pytest.mark.parametrize("predictor", ["total", "margin_magnitude"])
def test_total_correction_never_touches_win_probability_or_margin(predictor):
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    none_outcomes = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, total_correction_method="none"
    )
    corrected_outcomes = run_walk_forward_backtest(
        lines,
        min_week_for_first_prediction=2,
        n_simulations=500,
        seed=0,
        total_correction_method="linear",
        total_correction_predictor=predictor,
    )
    for a, b in zip(none_outcomes, corrected_outcomes, strict=True):
        assert a.model_prob_home_win == pytest.approx(b.model_prob_home_win, abs=1e-12)
        assert a.calibrated_prob_home_win == pytest.approx(b.calibrated_prob_home_win, abs=1e-12)
        assert a.model_margin_mean == pytest.approx(b.model_margin_mean, abs=1e-12)
    assert any(
        abs(a.model_total_mean - b.model_total_mean) > 1e-6
        for a, b in zip(none_outcomes, corrected_outcomes, strict=True)
    )


@pytest.mark.parametrize("predictor", ["total", "margin_magnitude"])
def test_total_correction_shifts_mean_and_both_interval_bounds_by_the_same_delta(predictor):
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    none_outcomes = run_walk_forward_backtest(
        lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, total_correction_method="none"
    )
    corrected_outcomes = run_walk_forward_backtest(
        lines,
        min_week_for_first_prediction=2,
        n_simulations=500,
        seed=0,
        total_correction_method="linear",
        total_correction_predictor=predictor,
    )
    engaged = False
    for a, b in zip(none_outcomes, corrected_outcomes, strict=True):
        delta_mean = b.model_total_mean - a.model_total_mean
        delta_p05 = b.model_total_p05 - a.model_total_p05
        delta_p95 = b.model_total_p95 - a.model_total_p95
        assert delta_p05 == pytest.approx(delta_mean, abs=1e-6)
        assert delta_p95 == pytest.approx(delta_mean, abs=1e-6)
        if abs(delta_mean) > 1e-6:
            engaged = True
    assert engaged


def test_total_correction_never_touches_fbs_vs_fcs_games():
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    fcs_lines = []
    for season in (2022, 2023, 2024):
        for week in range(2, 13):
            fcs_lines.append(
                _line("team0", "fcs-visitor", 35, 10, 68, True, week=week, season=season, opp_class="fcs")
            )
            fcs_lines.append(
                _line("fcs-visitor", "team0", 10, 35, 55, False, week=week, season=season, team_class="fcs")
            )
    all_lines = lines + fcs_lines

    none_outcomes = run_walk_forward_backtest(
        all_lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, total_correction_method="none"
    )
    corrected_outcomes = run_walk_forward_backtest(
        all_lines, min_week_for_first_prediction=2, n_simulations=500, seed=0, total_correction_method="linear"
    )
    none_by_id = {o.source_game_id: o for o in none_outcomes if not o.is_fbs_vs_fbs}
    corrected_by_id = {o.source_game_id: o for o in corrected_outcomes if not o.is_fbs_vs_fbs}
    assert none_by_id
    for gid in none_by_id:
        assert none_by_id[gid].model_total_mean == pytest.approx(corrected_by_id[gid].model_total_mean, abs=1e-9)


def test_total_correction_does_not_use_a_weeks_own_or_future_outcomes():
    lines_a = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024), seed=5)
    target_gid = next(ln.source_game_id for ln in lines_a if ln.week == 3)

    lines_b = []
    for ln in lines_a:
        if ln.source_game_id == target_gid and ln.week == 3:
            if ln.is_home:
                lines_b.append(ln.model_copy(update={"team_points": ln.team_points + 30, "opponent_points": 0}))
            else:
                lines_b.append(ln.model_copy(update={"team_points": 0, "opponent_points": ln.opponent_points + 30}))
        else:
            lines_b.append(ln)

    outcomes_a = run_walk_forward_backtest(
        lines_a, min_week_for_first_prediction=2, n_simulations=500, seed=0, total_correction_method="linear"
    )
    outcomes_b = run_walk_forward_backtest(
        lines_b, min_week_for_first_prediction=2, n_simulations=500, seed=0, total_correction_method="linear"
    )

    week3_a = {o.source_game_id: o for o in outcomes_a if o.week == 3 and o.season == 2022}
    week3_b = {o.source_game_id: o for o in outcomes_b if o.week == 3 and o.season == 2022}
    assert week3_a
    for gid in week3_a:
        assert week3_a[gid].model_total_mean == pytest.approx(week3_b[gid].model_total_mean, abs=1e-9)

    later_a = sorted((o.source_game_id, o.model_total_mean) for o in outcomes_a if o.week == 12 and o.season == 2024)
    later_b = sorted((o.source_game_id, o.model_total_mean) for o in outcomes_b if o.week == 12 and o.season == 2024)
    assert later_a
    assert any(abs(a[1] - b[1]) > 1e-6 for a, b in zip(later_a, later_b, strict=True))


def test_total_correction_unknown_predictor_raises():
    lines = _synthetic_corpus(n_teams=16, n_weeks=12, seasons=(2022, 2023, 2024))
    with pytest.raises(ValueError, match="unknown total_correction_predictor"):
        run_walk_forward_backtest(
            lines,
            min_week_for_first_prediction=2,
            n_simulations=500,
            seed=0,
            total_correction_method="linear",
            total_correction_predictor="bogus",
        )


def test_compute_metrics_calibrated_prob_attr_uses_model_margin_not_naive():
    lines = _synthetic_corpus(n_teams=10, n_weeks=6, seasons=(2024, 2025))
    outcomes = run_walk_forward_backtest(lines, min_week_for_first_prediction=2, n_simulations=500, seed=0)
    calibrated_metrics = compute_metrics(outcomes, prob_attr="calibrated_prob_home_win")
    model_metrics = compute_metrics(outcomes, prob_attr="model_prob_home_win")
    # Margin/total metrics are identical between raw and calibrated views --
    # calibration only touches the win-probability, never margin/total.
    assert calibrated_metrics.margin_mae == pytest.approx(model_metrics.margin_mae)
    assert calibrated_metrics.total_mae == pytest.approx(model_metrics.total_mae)
