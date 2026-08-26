"""Leakage-safe probability recalibration (Milestone C hardening pass).

*** THE PROBLEM ***
The original Milestone C backtest showed material winner-probability
miscalibration in roughly the 0.60-0.90 predicted range (the raw
bootstrap-simulated win probability was underconfident there -- observed
win rates ran higher than predicted). This module recalibrates that raw
probability with a second, much simpler model fit ONLY on games that
completed strictly before the game being predicted.

*** LEAKAGE RULE ***
A calibration model used for a prediction may be fit ONLY on
(raw_probability, actual_outcome) pairs from games that were already
final at prediction time. It is never fit on the same holdout window it
is evaluated against -- see backtest.py's `run_walk_forward_backtest`,
which fits a fresh calibration model at every walk-forward step using
only the outcomes accumulated so far, exactly the same discipline it
already applies to the ratings themselves.

*** TWO METHODS, ONE DEFAULT ***
- Platt/logistic calibration: calibrated_logit = A * logit(raw_p) + B,
  fit by 2-parameter Newton-Raphson logistic regression. Only 2 free
  parameters, so it stays stable even with a few hundred games of
  history -- the natural choice while accumulated history is thin.
- Isotonic regression (pool-adjacent-violators): a fully flexible
  monotonic step function. More expressive, but needs materially more
  data to avoid overfitting noise into small predicted-probability bins.

Both are monotonic-non-decreasing IN raw_p by construction (isotonic
directly; Platt is enforced by falling back to identity if the fit
degenerates to A <= 0 -- see `fit_platt`). `MIN_CALIBRATION_HISTORY` (and
the higher `MIN_ISOTONIC_HISTORY`) are the "insufficient history -> fall
back safely" rule mission section 3 requires: below threshold, the raw
probability passes through unchanged rather than being recalibrated
against too little evidence.

Method selection is NOT re-decided per walk-forward step (mission
section 3: "do not blindly choose the method with the best in-sample
fit," and switching methods week-to-week would itself be a source of
instability). It is fixed once, in code, based on a genuine held-out
comparison run against live CFBD data -- see
docs/MILESTONE_C.md "Calibration" for that comparison and the resulting
choice.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

MIN_CALIBRATION_HISTORY = 200
"""Below this many strictly-prior outcomes, calibration is skipped
entirely (identity passthrough) -- 2-parameter Platt scaling is already
close to the smallest model that can safely be fit at all, and even it
needs a few hundred points before its 2 parameters are well-determined
rather than noise-fit."""

MIN_ISOTONIC_HISTORY = 800
"""Isotonic regression has effectively as many degrees of freedom as
distinct raw-probability values in the training set, so it needs
materially more history than Platt scaling before it is trusted over
Platt -- see module docstring."""

EPS = 1e-6


def _logit(p: np.ndarray) -> np.ndarray:
    p_clipped = np.clip(p, EPS, 1 - EPS)
    return np.log(p_clipped / (1 - p_clipped))


def _sigmoid(z: np.ndarray) -> np.ndarray:
    # Clipped so a degenerate/near-separable fit's large |z| can't overflow
    # np.exp -- purely a numerical-stability guard, doesn't change any
    # value that wasn't already going to round to 0.0/1.0 anyway.
    return 1.0 / (1.0 + np.exp(-np.clip(z, -500, 500)))


@dataclass(frozen=True)
class PlattParams:
    a: float
    b: float
    is_identity_fallback: bool = False

    def apply(self, raw_probs: np.ndarray) -> np.ndarray:
        if self.is_identity_fallback:
            return raw_probs
        z = self.a * _logit(raw_probs) + self.b
        return _sigmoid(z)


def fit_platt(raw_probs: np.ndarray, outcomes: np.ndarray, *, max_iter: int = 50, tol: float = 1e-8) -> PlattParams:
    """2-parameter logistic regression of `outcomes` on `logit(raw_probs)`,
    via Newton-Raphson (IRLS). Ridge-stabilized (tiny diagonal epsilon) so
    a perfectly- or near-perfectly-separable training window can't blow up
    the Hessian. If the fit produces a non-positive slope (A <= 0) --
    meaning raw_probs carries no usable monotonic signal in this training
    window, which should not happen with a reasonable model but is
    checked rather than assumed -- falls back to identity so a caller
    never receives a NON-monotonic or inverted calibration.
    """
    x = _logit(np.asarray(raw_probs, dtype=float))
    y = np.asarray(outcomes, dtype=float)
    n = len(x)
    if n == 0:
        return PlattParams(a=1.0, b=0.0, is_identity_fallback=True)

    z_design = np.column_stack([x, np.ones(n)])
    beta = np.array([1.0, 0.0])
    ridge_eps = 1e-6
    for _ in range(max_iter):
        eta = z_design @ beta
        p = _sigmoid(eta)
        w = np.clip(p * (1 - p), 1e-10, None)
        hessian = z_design.T @ (z_design * w[:, None]) + ridge_eps * np.eye(2)
        gradient = z_design.T @ (y - p)
        step = np.linalg.solve(hessian, gradient)
        beta = beta + step
        if np.max(np.abs(step)) < tol:
            break

    a, b = float(beta[0]), float(beta[1])
    if a <= 0:
        return PlattParams(a=1.0, b=0.0, is_identity_fallback=True)
    return PlattParams(a=a, b=b)


def _pava(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Pool-adjacent-violators: the classic O(n) weighted isotonic-
    regression fit. Returns a monotonic-non-decreasing array the same
    length as `values` (each input point's fitted level)."""
    n = len(values)
    level = list(values.astype(float))
    weight = list(weights.astype(float))
    count = [1] * n

    stack_level: list[float] = []
    stack_weight: list[float] = []
    stack_count: list[int] = []

    for i in range(n):
        cur_level, cur_weight, cur_count = level[i], weight[i], count[i]
        while stack_level and stack_level[-1] > cur_level:
            pl, pw, pc = stack_level.pop(), stack_weight.pop(), stack_count.pop()
            cur_level = (pl * pw + cur_level * cur_weight) / (pw + cur_weight)
            cur_weight = pw + cur_weight
            cur_count = pc + cur_count
        stack_level.append(cur_level)
        stack_weight.append(cur_weight)
        stack_count.append(cur_count)

    fitted: list[float] = []
    for lvl, cnt in zip(stack_level, stack_count, strict=True):
        fitted.extend([lvl] * cnt)
    return np.array(fitted)


@dataclass(frozen=True)
class IsotonicModel:
    breakpoints: np.ndarray
    fitted_values: np.ndarray

    def apply(self, raw_probs: np.ndarray) -> np.ndarray:
        if len(self.breakpoints) == 0:
            return raw_probs
        interpolated = np.interp(
            raw_probs, self.breakpoints, self.fitted_values, left=self.fitted_values[0], right=self.fitted_values[-1]
        )
        return np.clip(interpolated, EPS, 1 - EPS)


def fit_isotonic(raw_probs: np.ndarray, outcomes: np.ndarray) -> IsotonicModel:
    """Weighted isotonic regression of `outcomes` on `raw_probs` (raw
    probability itself, not its logit -- isotonic regression makes no
    parametric-shape assumption, so no transform is needed). Duplicate
    raw_prob values are pooled (mean outcome, summed weight) before PAVA
    so the fitted breakpoints are strictly increasing and safe to
    `np.interp` against.
    """
    x = np.asarray(raw_probs, dtype=float)
    y = np.asarray(outcomes, dtype=float)
    if len(x) == 0:
        return IsotonicModel(breakpoints=np.array([]), fitted_values=np.array([]))

    order = np.argsort(x)
    xs, ys = x[order], y[order]
    unique_x, inverse = np.unique(xs, return_inverse=True)
    sums = np.zeros(len(unique_x))
    counts = np.zeros(len(unique_x))
    np.add.at(sums, inverse, ys)
    np.add.at(counts, inverse, 1.0)
    means = sums / counts

    fitted = _pava(means, counts)
    return IsotonicModel(breakpoints=unique_x, fitted_values=fitted)


def calibrate(
    *,
    method: str,
    history_raw_probs: np.ndarray,
    history_outcomes: np.ndarray,
    target_raw_probs: np.ndarray,
) -> np.ndarray:
    """The single entry point backtest.py uses at each walk-forward step.
    `history_*` must already be restricted by the caller to strictly-prior
    games (this module has no notion of chronology itself -- see
    backtest.py). Falls back to identity (unchanged `target_raw_probs`)
    below the relevant minimum-history threshold, or for method="none".
    """
    n_history = len(history_raw_probs)
    if method == "none" or n_history < MIN_CALIBRATION_HISTORY:
        return np.asarray(target_raw_probs, dtype=float)

    if method == "platt":
        params = fit_platt(np.asarray(history_raw_probs), np.asarray(history_outcomes))
        return params.apply(np.asarray(target_raw_probs, dtype=float))

    if method == "isotonic":
        if n_history < MIN_ISOTONIC_HISTORY:
            return np.asarray(target_raw_probs, dtype=float)
        model = fit_isotonic(np.asarray(history_raw_probs), np.asarray(history_outcomes))
        return model.apply(np.asarray(target_raw_probs, dtype=float))

    raise ValueError(f"unknown calibration method: {method!r}")
