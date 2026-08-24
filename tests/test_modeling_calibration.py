import numpy as np
import pytest

from cfb_edge_finder.modeling.calibration import (
    EPS,
    MIN_CALIBRATION_HISTORY,
    MIN_ISOTONIC_HISTORY,
    calibrate,
    fit_isotonic,
    fit_platt,
)


def _synthetic_miscalibrated_history(n=2000, seed=0):
    """raw_p is systematically underconfident: true win probability is
    always higher than raw_p by a fixed logistic-shift amount -- exactly
    the shape a correct calibration fit should detect and correct."""
    rng = np.random.default_rng(seed)
    raw_p = rng.uniform(0.05, 0.95, n)
    true_logit = 1.3 * np.log(raw_p / (1 - raw_p)) + 0.4
    true_p = 1 / (1 + np.exp(-true_logit))
    outcomes = (rng.uniform(0, 1, n) < true_p).astype(float)
    return raw_p, outcomes


def test_calibrate_falls_back_to_identity_below_min_history():
    raw_p, outcomes = _synthetic_miscalibrated_history(n=MIN_CALIBRATION_HISTORY - 1)
    target = np.array([0.2, 0.5, 0.8])
    calibrated = calibrate(method="platt", history_raw_probs=raw_p, history_outcomes=outcomes, target_raw_probs=target)
    assert np.array_equal(calibrated, target)


def test_calibrate_method_none_is_always_identity():
    raw_p, outcomes = _synthetic_miscalibrated_history(n=5000)
    target = np.array([0.2, 0.5, 0.8])
    calibrated = calibrate(method="none", history_raw_probs=raw_p, history_outcomes=outcomes, target_raw_probs=target)
    assert np.array_equal(calibrated, target)


def test_calibrate_isotonic_falls_back_below_its_own_higher_threshold():
    n = MIN_ISOTONIC_HISTORY - 1
    assert n >= MIN_CALIBRATION_HISTORY  # sanity: this n clears Platt's floor but not isotonic's
    raw_p, outcomes = _synthetic_miscalibrated_history(n=n)
    target = np.array([0.2, 0.5, 0.8])
    calibrated = calibrate(
        method="isotonic", history_raw_probs=raw_p, history_outcomes=outcomes, target_raw_probs=target
    )
    assert np.array_equal(calibrated, target)


@pytest.mark.parametrize("method", ["platt", "isotonic"])
def test_calibrated_probabilities_stay_in_valid_bounds(method):
    raw_p, outcomes = _synthetic_miscalibrated_history(n=3000)
    target = np.linspace(0.01, 0.99, 50)
    calibrated = calibrate(
        method=method, history_raw_probs=raw_p, history_outcomes=outcomes, target_raw_probs=target
    )
    assert np.all(calibrated >= 0.0)
    assert np.all(calibrated <= 1.0)


@pytest.mark.parametrize("method", ["platt", "isotonic"])
def test_calibration_is_monotonic_in_raw_probability(method):
    raw_p, outcomes = _synthetic_miscalibrated_history(n=3000)
    target = np.linspace(0.01, 0.99, 50)  # already sorted ascending
    calibrated = calibrate(
        method=method, history_raw_probs=raw_p, history_outcomes=outcomes, target_raw_probs=target
    )
    assert np.all(np.diff(calibrated) >= -1e-9)


def test_platt_corrects_a_genuine_underconfidence_pattern():
    raw_p, outcomes = _synthetic_miscalibrated_history(n=5000)
    high_conf_raw = np.array([0.9])
    calibrated = calibrate(
        method="platt", history_raw_probs=raw_p, history_outcomes=outcomes, target_raw_probs=high_conf_raw
    )
    # The synthetic history is underconfident at high raw_p (true_p > raw_p there) --
    # a correct fit should push the calibrated value higher, not lower or unchanged.
    assert calibrated[0] > high_conf_raw[0]


def test_fit_platt_falls_back_to_identity_when_slope_would_be_nonpositive():
    # raw_p is INVERTED relative to the outcome (higher raw_p -> LESS
    # likely to win) -- a real fit here produces a negative slope; must
    # fall back to identity rather than ever return an inverted/
    # non-monotonic calibration mapping.
    raw_p = np.linspace(0.05, 0.95, 500)
    outcomes = (raw_p < 0.5).astype(float)
    params = fit_platt(raw_p, outcomes)
    assert params.is_identity_fallback
    assert np.array_equal(params.apply(raw_p), raw_p)


def test_fit_isotonic_produces_nondecreasing_breakpoints():
    raw_p, outcomes = _synthetic_miscalibrated_history(n=3000)
    model = fit_isotonic(raw_p, outcomes)
    assert np.all(np.diff(model.fitted_values) >= -1e-9)
    assert np.all(np.diff(model.breakpoints) > 0)  # unique, strictly increasing x


def test_isotonic_apply_clips_outside_training_range():
    raw_p, outcomes = _synthetic_miscalibrated_history(n=3000)
    model = fit_isotonic(raw_p, outcomes)
    below = model.apply(np.array([-1.0]))
    above = model.apply(np.array([2.0]))
    # apply() clips its final output to [EPS, 1-EPS] regardless of the
    # raw (unclipped) fitted_values PAVA produced.
    assert below[0] == pytest.approx(np.clip(model.fitted_values[0], EPS, 1 - EPS))
    assert above[0] == pytest.approx(np.clip(model.fitted_values[-1], EPS, 1 - EPS))
