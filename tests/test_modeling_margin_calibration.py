import numpy as np
import pytest

from cfb_edge_finder.modeling.margin_calibration import (
    MIN_MARGIN_CALIBRATION_HISTORY,
    MIN_MARGIN_ISOTONIC_HISTORY,
    correct_margin,
    fit_isotonic_margin,
    fit_linear_margin,
)


def test_fit_linear_margin_falls_back_to_identity_below_min_history():
    rng = np.random.default_rng(0)
    x = rng.normal(0, 10, size=MIN_MARGIN_CALIBRATION_HISTORY - 1)
    y = 1.3 * x + 2.0
    params = fit_linear_margin(x, y)
    assert params.is_identity_fallback
    assert params.a == 1.0
    assert params.b == 0.0


def test_fit_linear_margin_recovers_a_known_relationship_above_min_history():
    # actual = 1.3 * projected + 2.0 + small noise -- a genuine "the model
    # under-predicts by a scale factor" pattern, the exact shape mission
    # section 2 diagnosed for large favorites.
    rng = np.random.default_rng(1)
    n = MIN_MARGIN_CALIBRATION_HISTORY + 400
    x = rng.uniform(-25, 25, size=n)
    y = 1.3 * x + 2.0 + rng.normal(0, 1.0, size=n)
    params = fit_linear_margin(x, y)
    assert not params.is_identity_fallback
    assert params.a == pytest.approx(1.3, abs=0.05)
    assert params.b == pytest.approx(2.0, abs=0.5)


def test_fit_linear_margin_falls_back_to_identity_on_degenerate_fit():
    # No real relationship between projected and actual -- the fit should
    # not confidently invert or distort predictions from noise alone.
    rng = np.random.default_rng(2)
    n = MIN_MARGIN_CALIBRATION_HISTORY + 100
    x = rng.uniform(-10, 10, size=n)
    y = -1.0 * x + rng.normal(0, 0.01, size=n)  # genuinely inverted (a < 0)
    params = fit_linear_margin(x, y)
    assert params.is_identity_fallback


def test_linear_margin_params_apply_is_affine():
    from cfb_edge_finder.modeling.margin_calibration import LinearMarginParams

    params = LinearMarginParams(a=1.5, b=3.0)
    out = params.apply(np.array([0.0, 10.0, -10.0]))
    assert out == pytest.approx([3.0, 18.0, -12.0])


def test_fit_isotonic_margin_falls_back_to_identity_below_min_history():
    rng = np.random.default_rng(3)
    x = rng.uniform(-20, 20, size=MIN_MARGIN_ISOTONIC_HISTORY - 1)
    y = x + rng.normal(0, 1, size=len(x))
    model = fit_isotonic_margin(x, y)
    assert model.is_identity_fallback
    out = model.apply(np.array([5.0, -5.0]))
    assert out == pytest.approx([5.0, -5.0])


def test_fit_isotonic_margin_is_monotonic_non_decreasing():
    rng = np.random.default_rng(4)
    n = MIN_MARGIN_ISOTONIC_HISTORY + 200
    x = rng.uniform(-30, 30, size=n)
    # A genuinely convex "compression" relationship: actual grows faster
    # than projected once |projected| is large, consistent with mission
    # section 2's diagnosis.
    y = x + 0.02 * x**3 / 30 + rng.normal(0, 1.5, size=n)
    model = fit_isotonic_margin(x, y)
    assert not model.is_identity_fallback
    assert np.all(np.diff(model.fitted_values) >= -1e-9)

    # Sign/order coherence: a strictly larger projected margin can never
    # map to a strictly smaller corrected margin.
    probe = np.array([-25.0, -10.0, 0.0, 10.0, 25.0])
    out = model.apply(probe)
    assert np.all(np.diff(out) >= -1e-9)


def test_fit_isotonic_margin_recovers_amplified_tail_from_synthetic_compression():
    # Directly test the "does it correct compression" property: actual
    # margin has a LARGER magnitude than projected in the tails, and the
    # fitted isotonic model should recover that (large |projected| maps
    # to an even larger-magnitude corrected value on average).
    rng = np.random.default_rng(5)
    n = MIN_MARGIN_ISOTONIC_HISTORY + 300
    x = rng.uniform(-30, 30, size=n)
    y = np.sign(x) * (np.abs(x) ** 1.15) + rng.normal(0, 1.0, size=n)
    model = fit_isotonic_margin(x, y)
    corrected_tail = model.apply(np.array([28.0]))[0]
    assert corrected_tail > 28.0  # amplified, not compressed further
    corrected_small = model.apply(np.array([1.0]))[0]
    assert abs(corrected_small) < abs(corrected_tail)  # small games barely touched


def test_correct_margin_none_is_a_true_no_op():
    target = np.array([5.0, -5.0, 20.0])
    out = correct_margin(
        method="none",
        history_projected=np.array([1.0, 2.0]),
        history_actual=np.array([1.0, 2.0]),
        target_projected=target,
    )
    assert out == pytest.approx(target)


def test_correct_margin_unknown_method_raises():
    with pytest.raises(ValueError, match="unknown margin correction method"):
        correct_margin(
            method="bogus",
            history_projected=np.array([1.0]),
            history_actual=np.array([1.0]),
            target_projected=np.array([1.0]),
        )


def test_correct_margin_dispatches_to_linear_and_isotonic():
    rng = np.random.default_rng(6)
    n = MIN_MARGIN_CALIBRATION_HISTORY + 200
    x = rng.uniform(-20, 20, size=n)
    y = 1.2 * x + rng.normal(0, 1, size=n)
    out_linear = correct_margin(
        method="linear", history_projected=x, history_actual=y, target_projected=np.array([10.0])
    )
    assert out_linear[0] == pytest.approx(12.0, abs=1.0)

    out_isotonic = correct_margin(
        method="isotonic", history_projected=x, history_actual=y, target_projected=np.array([10.0])
    )
    # Below MIN_MARGIN_ISOTONIC_HISTORY (800), isotonic falls back to identity.
    assert out_isotonic[0] == pytest.approx(10.0)
