"""Leakage-safe, walk-forward post-model MARGIN calibration (Milestone
C.2 Part 3, mission section 3).

*** WHY THIS EXISTS ***
Part 3's diagnosis (docs/MILESTONE_C2.md, diagnostics.py's
`favorite_tail_margin_diagnosis`) found the model systematically
compresses large projected margins toward zero, concentrated in
FBS-vs-FBS home-favorite games specifically. This module fits a SECOND,
much simpler post-hoc model -- exactly the same architectural pattern
calibration.py already uses for win probability (a 2-parameter linear
fit, or pool-adjacent-violators isotonic regression, both monotonic by
construction, both refit walk-forward from ONLY strictly-prior outcomes)
-- but targeting the model's own projected MARGIN instead of its win
probability.

*** LEAKAGE RULE, IDENTICAL TO calibration.py ***
A margin-correction model used for a prediction may be fit ONLY on
(projected_margin, actual_margin) pairs from games that were already
final at prediction time -- see backtest.py's `run_walk_forward_backtest`,
which refits this model at every walk-forward step from only the
outcomes accumulated so far, exactly the same discipline already applied
to ratings and probability calibration.

*** WHY FBS-vs-FBS ONLY ***
The fitting history backtest.py passes in is restricted to FBS-vs-FBS
outcomes only (mission's explicit instruction: FBS-vs-FCS is not the
optimization target here, and its much larger, differently-shaped bias
would otherwise dominate/contaminate a shared fit). FBS-vs-FCS games are
never corrected by this module -- their `model_margin_mean` passes
through unchanged, consistent with FBS-vs-FCS remaining
UNSUPPORTED_FOR_PRICING and untouched this pass.

*** WHY THIS TOUCHES ONLY THE MARGIN CHANNEL, NEVER WIN PROBABILITY ***
`model_prob_home_win`/`calibrated_prob_home_win` are produced directly
from the Monte Carlo simulation draws, upstream of and independent from
this module -- exactly like calibration.py's Platt/isotonic fit for
probability is ALREADY a separate, independently-fit channel from the
margin/total point estimates in this codebase (Milestone C.2 Part 1
section 4 found the margin-accuracy gain and the winner-calibration gain
to be genuinely separate axes). This module never reads or writes any
probability field, so it structurally cannot distort winner calibration
-- the two channels remain exactly as decoupled as they already were.

*** WHY A LOCATION SHIFT, NOT A RESCALED DISTRIBUTION ***
backtest.py applies this module's correction as a uniform SHIFT to the
entire simulated margin distribution (the mean AND both p05/p95 interval
bounds move by the same delta = corrected_margin - original_margin),
never a rescaling of its spread. This preserves Milestone C.2 Part 2's
interval-coverage gain (`residual_scale`) exactly, while still
correcting the LOCATION this module's diagnosis found systematically
off.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from cfb_edge_finder.modeling.calibration import _pava

MIN_MARGIN_CALIBRATION_HISTORY = 200
"""Same value as calibration.py's MIN_CALIBRATION_HISTORY -- reused
rather than re-derived, since it already reflects a genuine, documented
judgment about how much history a 2-parameter fit needs at this
codebase's walk-forward cadence, not a new arbitrary number."""

MIN_MARGIN_ISOTONIC_HISTORY = 800
"""Same value as calibration.py's MIN_ISOTONIC_HISTORY, same reuse
rationale -- isotonic regression has effectively as many degrees of
freedom as distinct projected-margin values in the training set."""


@dataclass(frozen=True)
class LinearMarginParams:
    a: float
    b: float
    is_identity_fallback: bool = False

    def apply(self, projected: np.ndarray) -> np.ndarray:
        if self.is_identity_fallback:
            return np.asarray(projected, dtype=float)
        return self.a * np.asarray(projected, dtype=float) + self.b


def fit_linear_margin(projected: np.ndarray, actual: np.ndarray) -> LinearMarginParams:
    """Ordinary-least-squares fit of actual margin on projected margin:
    actual ~= a * projected + b (closed-form 2x2 normal equations -- no
    iteration needed, unlike Platt's logistic fit). Falls back to
    identity (a=1, b=0) if the fit is degenerate (a <= 0, meaning the
    projected margin carries no usable positive-monotonic signal in this
    training window -- should not happen with a reasonable model, but
    checked rather than assumed, exactly like `fit_platt`'s a<=0 guard)
    or if fewer than `MIN_MARGIN_CALIBRATION_HISTORY` prior FBS-vs-FBS
    games are available."""
    x = np.asarray(projected, dtype=float)
    y = np.asarray(actual, dtype=float)
    n = len(x)
    if n < MIN_MARGIN_CALIBRATION_HISTORY:
        return LinearMarginParams(a=1.0, b=0.0, is_identity_fallback=True)

    x_mean, y_mean = float(np.mean(x)), float(np.mean(y))
    denom = float(np.sum((x - x_mean) ** 2))
    if denom < 1e-9:
        return LinearMarginParams(a=1.0, b=0.0, is_identity_fallback=True)
    a = float(np.sum((x - x_mean) * (y - y_mean)) / denom)
    b = y_mean - a * x_mean
    if a <= 0:
        return LinearMarginParams(a=1.0, b=0.0, is_identity_fallback=True)
    return LinearMarginParams(a=a, b=b)


@dataclass(frozen=True)
class IsotonicMarginModel:
    breakpoints: np.ndarray
    fitted_values: np.ndarray
    is_identity_fallback: bool = False

    def apply(self, projected: np.ndarray) -> np.ndarray:
        if self.is_identity_fallback or len(self.breakpoints) == 0:
            return np.asarray(projected, dtype=float)
        return np.interp(
            np.asarray(projected, dtype=float),
            self.breakpoints,
            self.fitted_values,
            left=self.fitted_values[0],
            right=self.fitted_values[-1],
        )


def fit_isotonic_margin(projected: np.ndarray, actual: np.ndarray) -> IsotonicMarginModel:
    """Weighted isotonic (pool-adjacent-violators) regression of actual
    margin on projected margin -- reuses calibration.py's `_pava`, the
    same generic, already-tested O(n) primitive already used for
    win-probability isotonic calibration (the algorithm itself has no
    probability-specific logic; only `calibration.IsotonicModel.apply`'s
    [0,1] clip is probability-specific, which is why this module has its
    own `.apply()` without it -- clipping a margin correction into [0,1]
    would be wrong). Monotonic-non-decreasing by construction, so it
    cannot invert the relative order of two games' projected margins --
    mission section 3's "preserve sign/order coherence" requirement is
    satisfied structurally, not by a hand-checked property. Falls back
    to identity below `MIN_MARGIN_ISOTONIC_HISTORY`."""
    x = np.asarray(projected, dtype=float)
    y = np.asarray(actual, dtype=float)
    if len(x) < MIN_MARGIN_ISOTONIC_HISTORY:
        return IsotonicMarginModel(breakpoints=np.array([]), fitted_values=np.array([]), is_identity_fallback=True)

    order = np.argsort(x)
    xs, ys = x[order], y[order]
    unique_x, inverse = np.unique(xs, return_inverse=True)
    sums = np.zeros(len(unique_x))
    counts = np.zeros(len(unique_x))
    np.add.at(sums, inverse, ys)
    np.add.at(counts, inverse, 1.0)
    means = sums / counts

    fitted = _pava(means, counts)
    return IsotonicMarginModel(breakpoints=unique_x, fitted_values=fitted)


def correct_margin(
    *,
    method: str,
    history_projected: np.ndarray,
    history_actual: np.ndarray,
    target_projected: np.ndarray,
) -> np.ndarray:
    """Single entry point backtest.py uses at each walk-forward step,
    mirroring calibration.py's `calibrate()`. `history_*` must already be
    restricted by the caller to strictly-prior, FBS-vs-FBS-only games
    (this module has no notion of chronology or classification itself).
    method="none" returns `target_projected` unchanged."""
    if method == "none":
        return np.asarray(target_projected, dtype=float)
    if method == "linear":
        params = fit_linear_margin(history_projected, history_actual)
        return params.apply(np.asarray(target_projected, dtype=float))
    if method == "isotonic":
        model = fit_isotonic_margin(history_projected, history_actual)
        return model.apply(np.asarray(target_projected, dtype=float))
    raise ValueError(f"unknown margin correction method: {method!r}")
