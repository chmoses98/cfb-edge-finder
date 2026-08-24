"""Tests for score_model.CorrectedGameProjection/apply_margin_correction --
the live single-game projection path's wiring of Milestone C.2 Part 3's
margin_correction_method="linear" (docs/MILESTONE_C2.md's closure/parity
pass). See tests/test_build_cfb_baseline_cli.py for end-to-end CLI-level
coverage of the same properties."""

from datetime import UTC, datetime

import numpy as np
import pytest

from cfb_edge_finder.modeling.corpus import TeamGameLine
from cfb_edge_finder.modeling.leakage import AsOf
from cfb_edge_finder.modeling.margin_calibration import IsotonicMarginModel, LinearMarginParams
from cfb_edge_finder.modeling.ratings import fit_fbs_efficiency_ratings
from cfb_edge_finder.modeling.score_model import (
    CorrectedGameProjection,
    apply_margin_correction,
    build_expanding_residual_pool,
    project_game,
)

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


def _project(ratings, pool, *, home="t0", away="t1", home_class="fbs", away_class="fbs", seed=1):
    return project_game(
        home_id=home,
        away_id=away,
        home_classification=home_class,
        away_classification=away_class,
        is_neutral_site=False,
        ratings=ratings,
        prior_season_ratings=None,
        residual_pool=pool,
        home_percent_passing_ppa=None,
        away_percent_passing_ppa=None,
        n_simulations=8000,
        seed=seed,
    )


TRAINING_CUTOFF = AsOf(season=2025, week=8)
INSIDE_AS_OF = AsOf(season=2025, week=8)  # not strictly before cutoff -> eligible
OUTSIDE_AS_OF = AsOf(season=2025, week=7)  # strictly before cutoff -> leakage guard trips


# --- "none" is a true no-op ---


def test_method_none_is_a_true_no_op(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="none",
        correction_model=None,
        artifact_version=None,
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.margin_delta == 0.0
    assert corrected.correction_applied is False
    assert corrected.correction_skip_reason == "method_none"
    # Bit-for-bit: reproduces pre-C.2 (uncorrected) behavior exactly.
    assert corrected.expected_home_points == raw.expected_home_points
    assert corrected.expected_away_points == raw.expected_away_points
    assert corrected.prob_home_win() == raw.prob_home_win()
    assert corrected.prob_away_win() == raw.prob_away_win()
    assert np.array_equal(corrected.raw.home_scores, raw.home_scores)


# --- FBS-vs-FCS is never corrected ---


def test_non_fbs_vs_fbs_game_is_never_corrected(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool, away="some_fcs_team", away_class="fcs")
    params = LinearMarginParams(a=1.3, b=0.8)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=False,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.margin_delta == 0.0
    assert corrected.correction_applied is False
    assert corrected.correction_skip_reason == "not_fbs_vs_fbs"


# --- leakage guard: as-of predating the frozen artifact's training cutoff ---


def test_as_of_predating_training_cutoff_skips_correction(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3, b=0.8)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=OUTSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.margin_delta == 0.0
    assert corrected.correction_applied is False
    assert corrected.correction_skip_reason == "as_of_predates_training_cutoff"


def test_as_of_equal_to_training_cutoff_is_eligible_not_strictly_before(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3, b=0.8)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=TRAINING_CUTOFF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.correction_applied is True
    assert corrected.correction_skip_reason is None


def test_identity_fallback_correction_model_skips(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    identity = LinearMarginParams(a=1.0, b=0.0, is_identity_fallback=True)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=identity,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.margin_delta == 0.0
    assert corrected.correction_applied is False
    assert corrected.correction_skip_reason == "identity_fallback"


# --- the correction reuses margin_calibration's actual classes, not a duplicate ---


def test_frozen_correction_model_is_a_real_margin_calibration_type():
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    assert isinstance(params, LinearMarginParams)
    isotonic = IsotonicMarginModel(breakpoints=np.array([0.0, 10.0]), fitted_values=np.array([0.0, 15.0]))
    assert isinstance(isotonic, IsotonicMarginModel)


# --- correction math: delta, sign/order coherence ---


def test_applies_linear_params_and_computes_matching_delta(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3, b=0.8)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    raw_margin = raw.expected_home_points - raw.expected_away_points
    expected_corrected_margin = params.a * raw_margin + params.b
    assert corrected.expected_margin == pytest.approx(expected_corrected_margin, abs=1e-6)
    assert corrected.margin_delta == pytest.approx(expected_corrected_margin - raw_margin, abs=1e-6)


def test_correction_preserves_relative_order_across_different_matchups(fitted_ratings_and_pool):
    # A larger raw margin must never map to a smaller corrected margin --
    # the underlying LinearMarginParams/IsotonicMarginModel are both
    # monotonic non-decreasing by construction (margin_calibration.py),
    # so this must hold end-to-end through apply_margin_correction too.
    ratings, pool = fitted_ratings_and_pool
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    raw_margins = []
    corrected_margins = []
    for seed in range(1, 6):
        raw = _project(ratings, pool, home=f"t{seed}", away=f"t{seed + 8}", seed=seed)
        corrected = apply_margin_correction(
            raw,
            is_fbs_vs_fbs=True,
            method="linear",
            correction_model=params,
            artifact_version="test-v1",
            as_of=INSIDE_AS_OF,
            training_cutoff=TRAINING_CUTOFF,
        )
        raw_margins.append(corrected.raw_expected_margin)
        corrected_margins.append(corrected.expected_margin)
    order = np.argsort(raw_margins)
    assert list(np.array(corrected_margins)[order]) == sorted(corrected_margins)


# --- total invariance, win-probability decoupling ---


def test_total_expectation_is_unchanged_by_margin_correction(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    raw_total = raw.expected_home_points + raw.expected_away_points
    assert corrected.expected_total == pytest.approx(raw_total, abs=1e-9)
    assert corrected.margin_delta != 0.0  # sanity: the correction genuinely did something


def test_win_probability_is_unchanged_by_margin_correction(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.prob_home_win() == raw.prob_home_win()
    assert corrected.prob_away_win() == raw.prob_away_win()


def test_home_minus_away_equals_expected_margin_and_sum_equals_total(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.expected_home_points - corrected.expected_away_points == pytest.approx(
        corrected.expected_margin, abs=1e-6
    )
    assert corrected.expected_home_points + corrected.expected_away_points == pytest.approx(
        corrected.expected_total, abs=1e-6
    )


# --- probability bounds and monotonicity ---


def test_probability_bounds_remain_valid_after_correction(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert 0.0 <= corrected.prob_home_win() <= 1.0
    assert 0.0 <= corrected.prob_away_win() <= 1.0
    assert corrected.prob_home_win() + corrected.prob_away_win() == pytest.approx(1.0, abs=1e-9)
    for threshold in (-21, -14, -7, 0, 7, 14, 21):
        assert 0.0 <= corrected.prob_margin_greater_than(threshold) <= 1.0
        assert 0.0 <= corrected.prob_total_greater_than(threshold) <= 1.0


def test_margin_threshold_probabilities_remain_monotonic_after_correction(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    thresholds = [-21, -14, -7, -3.5, 0, 3.5, 7, 14, 21]
    probs = [corrected.prob_margin_greater_than(t) for t in thresholds]
    assert probs == sorted(probs, reverse=True)


def test_prob_margin_greater_than_shifts_exactly_by_delta(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    delta = corrected.margin_delta
    for threshold in (-14.0, -7.0, 0.0, 7.0, 14.0):
        # P(shifted margin > t) must equal P(raw margin > t - delta).
        assert corrected.prob_margin_greater_than(threshold) == pytest.approx(
            raw.prob_margin_greater_than(threshold - delta), abs=1e-9
        )


def test_total_threshold_probabilities_unchanged_by_margin_correction(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    for threshold in (35, 42, 49, 56, 63):
        assert corrected.prob_total_greater_than(threshold) == raw.prob_total_greater_than(threshold)


# --- GameDistribution: means move, spread/correlation are shift-invariant ---


def test_game_distribution_means_shift_but_sd_and_correlation_are_shift_invariant(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    raw_dist = raw.to_game_distribution()
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    dist = corrected.to_game_distribution()
    delta = corrected.margin_delta
    assert dist.home_mean == pytest.approx(raw_dist.home_mean + delta / 2, abs=1e-6)
    assert dist.away_mean == pytest.approx(raw_dist.away_mean - delta / 2, abs=1e-6)
    assert dist.home_sd == raw_dist.home_sd
    assert dist.away_sd == raw_dist.away_sd
    assert dist.correlation == raw_dist.correlation


def test_game_distribution_means_stay_nonnegative_for_a_large_negative_delta(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool, home="t0", away="t1")
    # An extreme, deliberately unrealistic correction to exercise the 0.0 floor.
    extreme = LinearMarginParams(a=1.0, b=-10_000.0)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=extreme,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.expected_home_points >= 0.0
    assert corrected.expected_away_points >= 0.0
    assert corrected.to_game_distribution().home_mean >= 0.0
    assert corrected.to_game_distribution().away_mean >= 0.0


def test_corrected_game_projection_reproduces_raw_uncertainty_profile(fitted_ratings_and_pool):
    ratings, pool = fitted_ratings_and_pool
    raw = _project(ratings, pool)
    params = LinearMarginParams(a=1.3413121461524347, b=0.8117267938452581)
    corrected = apply_margin_correction(
        raw,
        is_fbs_vs_fbs=True,
        method="linear",
        correction_model=params,
        artifact_version="test-v1",
        as_of=INSIDE_AS_OF,
        training_cutoff=TRAINING_CUTOFF,
    )
    assert corrected.to_uncertainty_profile() == raw.to_uncertainty_profile()


def test_corrected_game_projection_is_frozen_dataclass():
    assert CorrectedGameProjection.__dataclass_params__.frozen
