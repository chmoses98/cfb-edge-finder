import numpy as np
import pytest

from cfb_edge_finder.modeling.margin_calibration import (
    MIN_MARGIN_CALIBRATION_HISTORY,
    MIN_MARGIN_ISOTONIC_HISTORY,
)
from cfb_edge_finder.modeling.total_calibration import (
    correct_total_direct,
    correct_total_via_margin_residual,
)


def test_correct_total_direct_none_is_a_true_no_op():
    target = np.array([45.0, 60.0, 30.0])
    out = correct_total_direct(
        method="none",
        history_predictor=np.array([50.0, 55.0]),
        history_actual_total=np.array([48.0, 58.0]),
        target_predictor=target,
    )
    assert out == pytest.approx(target)


def test_correct_total_direct_linear_recovers_known_relationship():
    # actual_total ~= 1.1 * projected_total - 3.0 -- a genuine
    # "under-predicts high totals" pattern (mechanism B).
    rng = np.random.default_rng(10)
    n = MIN_MARGIN_CALIBRATION_HISTORY + 300
    x = rng.uniform(35, 75, size=n)
    y = 1.1 * x - 3.0 + rng.normal(0, 1.5, size=n)
    out = correct_total_direct(
        method="linear", history_predictor=x, history_actual_total=y, target_predictor=np.array([70.0])
    )
    # true value: 1.1*70 - 3.0 = 74.0
    assert out[0] == pytest.approx(74.0, abs=2.0)


def test_correct_total_direct_isotonic_amplifies_high_totals():
    rng = np.random.default_rng(11)
    n = MIN_MARGIN_ISOTONIC_HISTORY + 200
    x = rng.uniform(35, 75, size=n)
    y = x + np.maximum(x - 60, 0) * 0.8 + rng.normal(0, 1.0, size=n)
    out_high = correct_total_direct(
        method="isotonic", history_predictor=x, history_actual_total=y, target_predictor=np.array([70.0])
    )[0]
    out_low = correct_total_direct(
        method="isotonic", history_predictor=x, history_actual_total=y, target_predictor=np.array([40.0])
    )[0]
    assert out_high > 70.0  # amplified for the high-total shootout region
    assert out_low == pytest.approx(40.0, abs=1.5)  # near-untouched for ordinary totals


def test_correct_total_direct_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown total correction method"):
        correct_total_direct(
            method="bogus",
            history_predictor=np.array([1.0]),
            history_actual_total=np.array([1.0]),
            target_predictor=np.array([1.0]),
        )


def test_correct_total_via_margin_residual_none_is_a_true_no_op():
    target_total = np.array([45.0, 60.0])
    out = correct_total_via_margin_residual(
        method="none",
        history_margin_magnitude=np.array([5.0, 20.0]),
        history_total_residual=np.array([1.0, -8.0]),
        target_margin_magnitude=np.array([5.0, 20.0]),
        target_projected_total=target_total,
    )
    assert out == pytest.approx(target_total)


def test_correct_total_via_margin_residual_identity_fallback_is_zero_not_raw_predictor():
    """Regression test for a real bug: below MIN_MARGIN_CALIBRATION_HISTORY
    (insufficient history), the identity-fallback residual must be 0.0
    (i.e. target_projected_total returned unchanged), NEVER the raw
    negated |margin| predictor value. A live ablation run hit this exact
    bug -- with too little history to trust, `params.apply()`'s identity
    branch was returning `-|margin|` itself as if it were the fitted
    residual, injecting a large, nonsensical, margin-scaled shift into
    every game's total instead of applying no correction at all.
    """
    # Only 5 points of history -- far below MIN_MARGIN_CALIBRATION_HISTORY
    # (200), so this must hit the identity-fallback path.
    history_margin_magnitude = np.array([2.0, 5.0, 10.0, 15.0, 20.0])
    history_total_residual = np.array([0.5, -1.0, -2.0, -3.0, -4.0])
    target_margin_magnitude = np.array([25.0])  # a large favorite
    target_projected_total = np.array([50.0])

    out = correct_total_via_margin_residual(
        method="linear",
        history_margin_magnitude=history_margin_magnitude,
        history_total_residual=history_total_residual,
        target_margin_magnitude=target_margin_magnitude,
        target_projected_total=target_projected_total,
    )
    # Correct: no correction applied, projected total passed through.
    assert out[0] == pytest.approx(50.0)
    # The bug this guards against: the wrong answer would have been
    # target_projected_total + (-target_margin_magnitude) = 50 - 25 = 25,
    # a huge, spurious 25-point swing from a fit with only 5 data points.
    assert out[0] != pytest.approx(25.0)


def test_correct_total_via_margin_residual_isotonic_identity_fallback_is_zero():
    history_margin_magnitude = np.array([2.0, 5.0, 10.0])
    history_total_residual = np.array([0.5, -1.0, -2.0])
    target_margin_magnitude = np.array([25.0])
    target_projected_total = np.array([50.0])

    out = correct_total_via_margin_residual(
        method="isotonic",
        history_margin_magnitude=history_margin_magnitude,
        history_total_residual=history_total_residual,
        target_margin_magnitude=target_margin_magnitude,
        target_projected_total=target_projected_total,
    )
    assert out[0] == pytest.approx(50.0)


def test_correct_total_via_margin_residual_linear_recovers_a_genuinely_negative_relationship():
    """Regression test for the sign-handling bug this module's docstring
    documents: the true garbage-time relationship is NEGATIVE (residual
    decreases as |margin| grows). Without correctly negating the
    predictor before calling fit_linear_margin (which falls back to
    identity for a<=0 fits), this candidate would silently do nothing.
    """
    rng = np.random.default_rng(12)
    n = MIN_MARGIN_CALIBRATION_HISTORY + 300
    margin_mag = rng.uniform(0, 30, size=n)
    # Genuine garbage-time pattern: residual = -0.4 * margin_mag + noise
    # (a real, strong negative slope in the RAW |margin| coordinate).
    residual = -0.4 * margin_mag + rng.normal(0, 1.0, size=n)

    corrected_small_margin = correct_total_via_margin_residual(
        method="linear",
        history_margin_magnitude=margin_mag,
        history_total_residual=residual,
        target_margin_magnitude=np.array([2.0]),
        target_projected_total=np.array([50.0]),
    )[0]
    corrected_large_margin = correct_total_via_margin_residual(
        method="linear",
        history_margin_magnitude=margin_mag,
        history_total_residual=residual,
        target_margin_magnitude=np.array([28.0]),
        target_projected_total=np.array([50.0]),
    )[0]
    # A big blowout must be corrected DOWNWARD relative to a near-pick'em
    # game -- proving the negative relationship was genuinely recovered,
    # not silently discarded via an identity fallback.
    assert corrected_large_margin < corrected_small_margin - 5.0
    # True value at margin_mag=28: 50 + (-0.4*28) = 38.8
    assert corrected_large_margin == pytest.approx(38.8, abs=2.5)


def test_correct_total_via_margin_residual_isotonic_recovers_a_genuinely_negative_relationship():
    rng = np.random.default_rng(13)
    n = MIN_MARGIN_ISOTONIC_HISTORY + 200
    margin_mag = rng.uniform(0, 30, size=n)
    residual = -0.3 * margin_mag + rng.normal(0, 1.0, size=n)

    corrected_small_margin = correct_total_via_margin_residual(
        method="isotonic",
        history_margin_magnitude=margin_mag,
        history_total_residual=residual,
        target_margin_magnitude=np.array([1.0]),
        target_projected_total=np.array([50.0]),
    )[0]
    corrected_large_margin = correct_total_via_margin_residual(
        method="isotonic",
        history_margin_magnitude=margin_mag,
        history_total_residual=residual,
        target_margin_magnitude=np.array([29.0]),
        target_projected_total=np.array([50.0]),
    )[0]
    assert corrected_large_margin < corrected_small_margin - 3.0


def test_correct_total_via_margin_residual_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown total correction method"):
        correct_total_via_margin_residual(
            method="bogus",
            history_margin_magnitude=np.array([1.0]),
            history_total_residual=np.array([1.0]),
            target_margin_magnitude=np.array([1.0]),
            target_projected_total=np.array([50.0]),
        )
