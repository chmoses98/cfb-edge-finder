"""Leakage-safe, walk-forward post-model TOTAL calibration (Milestone
C.2 Part 3, mission section 5).

*** REUSES margin_calibration.py's GENERIC FIT ENGINE ***
`fit_linear_margin`/`fit_isotonic_margin` (and the `LinearMarginParams`/
`IsotonicMarginModel` they return) have no margin-specific assumption
anywhere in their math -- they are a generic 2-parameter OLS fit and a
generic pool-adjacent-violators isotonic fit of one array on another
(see margin_calibration.py's own docstring for why `_pava` itself is
ALREADY reused from calibration.py's win-probability calibration). This
module reuses them directly for TOTALS rather than re-implementing the
same two algorithms a third time under new names.

*** TWO INDEPENDENT PREDICTORS, MATCHING THE TWO-MECHANISM DIAGNOSIS ***
Milestone C.2 Part 3's totals diagnosis (docs/MILESTONE_C2.md) found
total-points bias has two distinct, differently-driven mechanisms:
  (A) garbage-time suppression in large-projected-margin games (total
      OVER-predicted) -- correlates with |projected margin|, not
      projected total itself (diagnostics.py's
      `source_of_total_bias_summary`: large_projected_margin_bias and
      fbs_vs_fcs_bias are both strongly negative, while high/low_tempo
      and offense/defense-strength bias are all near zero).
  (B) shootout under-prediction in high-projected-total games (total
      UNDER-predicted) -- correlates with the model's own projected
      total, sharply so above roughly 63 projected points.
Because the two mechanisms have DIFFERENT drivers, this module offers
two independent, single-predictor candidates rather than forcing one
input to explain both:
  - `predictor="total"`: direct fit of actual_total on projected_total
    (mirrors margin_calibration.py's design exactly) -- targets (B).
  - `predictor="margin_magnitude"`: fit of the RESIDUAL
    (actual_total - projected_total) on |projected margin|, added back
    onto the model's own projected_total -- targets (A). A residual fit
    is used here (rather than a direct fit like the "total" predictor)
    because the predictor (margin magnitude) and the corrected quantity
    (total) are different units; a direct fit would discard the model's
    own projected_total signal entirely, while a residual fit keeps it
    as the baseline and only adds a garbage-time-shaped adjustment.
Each candidate is tested and reported independently (mission section 5's
"test a small number... one family at a time" instruction) before any
combined candidate is considered.

*** FBS-vs-FBS ONLY, LOCATION SHIFT ONLY, DECOUPLED FROM WIN PROBABILITY
*** -- identical rationale to margin_calibration.py; see that module's
docstring. backtest.py applies this module's correction as a uniform
shift to `model_total_mean` AND both `model_total_p05`/`model_total_p95`,
never a rescale, preserving Part 2's `residual_scale` coverage gain.
"""

from __future__ import annotations

import numpy as np

from cfb_edge_finder.modeling.margin_calibration import (
    MIN_MARGIN_CALIBRATION_HISTORY,
    MIN_MARGIN_ISOTONIC_HISTORY,
    fit_isotonic_margin,
    fit_linear_margin,
)

MIN_TOTAL_CALIBRATION_HISTORY = MIN_MARGIN_CALIBRATION_HISTORY
"""Same value/rationale as margin_calibration.py's
MIN_MARGIN_CALIBRATION_HISTORY -- reused, not re-derived."""

MIN_TOTAL_ISOTONIC_HISTORY = MIN_MARGIN_ISOTONIC_HISTORY
"""Same value/rationale as margin_calibration.py's
MIN_MARGIN_ISOTONIC_HISTORY -- reused, not re-derived."""


def correct_total_direct(
    *,
    method: str,
    history_predictor: np.ndarray,
    history_actual_total: np.ndarray,
    target_predictor: np.ndarray,
) -> np.ndarray:
    """`predictor="total"` candidate: direct fit of actual_total on the
    predictor (the model's own projected_total). method="none" returns
    `target_predictor` unchanged (a true no-op, since target_predictor
    IS the projected total in this mode)."""
    if method == "none":
        return np.asarray(target_predictor, dtype=float)
    if method == "linear":
        params = fit_linear_margin(history_predictor, history_actual_total)
        return params.apply(np.asarray(target_predictor, dtype=float))
    if method == "isotonic":
        model = fit_isotonic_margin(history_predictor, history_actual_total)
        return model.apply(np.asarray(target_predictor, dtype=float))
    raise ValueError(f"unknown total correction method: {method!r}")


def correct_total_via_margin_residual(
    *,
    method: str,
    history_margin_magnitude: np.ndarray,
    history_total_residual: np.ndarray,
    target_margin_magnitude: np.ndarray,
    target_projected_total: np.ndarray,
) -> np.ndarray:
    """`predictor="margin_magnitude"` candidate: fits the RESIDUAL
    (actual_total - projected_total) as a function of |projected margin|,
    then returns `target_projected_total + fitted_residual`, i.e. the
    model's own projected_total baseline plus a garbage-time-shaped
    adjustment. method="none" returns `target_projected_total`
    unchanged.

    *** WHY THE MARGIN-MAGNITUDE PREDICTOR IS NEGATED BEFORE FITTING ***
    Mechanism (A)'s genuine, diagnosed relationship is NEGATIVE: the
    residual (actual total minus projected total) DECREASES as |margin|
    grows (bigger blowouts -> more garbage-time suppression). But both
    `fit_linear_margin` (falls back to identity if the fitted slope is
    <= 0, since that guard exists to catch a genuinely degenerate fit in
    the margin-to-margin use case, where the relationship SHOULD be
    positive) and `fit_isotonic_margin`/PAVA (enforces a NON-DECREASING
    fit) both assume an increasing relationship by construction. Fitting
    them directly on |margin| here would either always identity-fallback
    (linear: a genuinely negative slope reads as "degenerate") or collapse
    the real signal toward a near-flat fit (isotonic: forcing a
    non-decreasing shape onto systematically decreasing data destroys
    most of the pattern). Fitting on the NEGATED predictor (`-|margin|`)
    instead makes the genuine relationship increasing in the fitted
    variable -- mathematically equivalent to allowing a decreasing fit in
    `|margin|` itself, and applied consistently at both fit and predict
    time, so the correction is exactly correct, just computed in mirrored
    coordinates.
    """
    target_projected_total = np.asarray(target_projected_total, dtype=float)
    if method == "none":
        return target_projected_total
    negated_history_x = -np.asarray(history_margin_magnitude, dtype=float)
    negated_target_x = -np.asarray(target_margin_magnitude, dtype=float)
    if method == "linear":
        params = fit_linear_margin(negated_history_x, history_total_residual)
        residual = params.apply(negated_target_x)
    elif method == "isotonic":
        model = fit_isotonic_margin(negated_history_x, history_total_residual)
        residual = model.apply(negated_target_x)
    else:
        raise ValueError(f"unknown total correction method: {method!r}")
    return target_projected_total + residual
