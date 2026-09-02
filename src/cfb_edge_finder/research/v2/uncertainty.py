"""Uncertainty models for V2 point predictions.

Given walk-forward OUT-OF-SAMPLE predictions (from tournament.py) the
residuals are genuinely predictive errors. This module studies them and
fits, chronologically, a conditional-scale model:

    sd(residual | x) = exp(b0 + b1 * |pred_margin| + b2 * early_w + ...)

and an empirical residual distribution (standardised residual quantiles)
so contract probabilities can be priced either parametrically
(Normal / Student-t with fitted df) or by empirical quantile lookup.

All fits are rolling-origin: the scale model used for season Y is fit on
out-of-sample residuals from seasons < Y only.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from scipy import optimize, stats


@dataclass
class ScaleModel:
    coef: np.ndarray
    cols: list[str]
    df_t: float | None  # Student-t degrees of freedom on standardised residuals (None = Normal)
    std_quantiles: np.ndarray  # empirical quantiles of standardised residuals (for nonparametric pricing)
    grid: np.ndarray

    def sd(self, F: pd.DataFrame) -> np.ndarray:
        Z = np.column_stack([np.ones(len(F))] + [F[c].values.astype(float) for c in self.cols])
        return np.exp(Z @ self.coef)


def fit_scale_model(resid: np.ndarray, F: pd.DataFrame, cols: list[str], *, fit_t: bool = True) -> ScaleModel:
    """Gaussian log-likelihood fit of log-sd as a linear function of `cols`."""
    Z = np.column_stack([np.ones(len(F))] + [F[c].values.astype(float) for c in cols])
    r = np.asarray(resid, float)

    def nll(b):
        s = Z @ b
        return np.sum(s + 0.5 * (r**2) * np.exp(-2 * s))

    b0 = np.zeros(Z.shape[1])
    b0[0] = np.log(np.std(r))
    res = optimize.minimize(nll, b0, method="L-BFGS-B")
    coef = res.x
    z = r / np.exp(Z @ coef)
    df_t = None
    if fit_t:
        # profile the t df on standardised residuals
        best, best_ll = None, -np.inf
        for df in (3, 4, 5, 6, 8, 10, 15, 20, 30, 50, 100):
            scale = np.sqrt((df - 2) / df) if df > 2 else 1.0
            ll = stats.t.logpdf(z / scale, df).sum() - len(z) * np.log(scale)
            if ll > best_ll:
                best, best_ll = df, ll
        ll_n = stats.norm.logpdf(z).sum()
        df_t = best if best_ll > ll_n + 2 else None
    grid = np.linspace(0.001, 0.999, 999)
    return ScaleModel(coef, cols, df_t, np.quantile(z, grid), grid)


def prob_greater(pred: np.ndarray, sd: np.ndarray, threshold: float, model: ScaleModel | None = None,
                 method: str = "normal", continuity: float = 0.5) -> np.ndarray:
    """P(outcome > threshold) for an integer-valued outcome around `pred`.

    Half-point thresholds: P(X > 3.5) = P(X >= 4) = 1 - F(3.5) exactly under
    a continuity-corrected continuous model, so the correction is applied
    only when the threshold is an integer (P(X > 3) = 1 - F(3.5))."""
    t = np.asarray(threshold, float)
    is_int = np.isclose(t, np.round(t))
    cut = np.where(is_int, t + continuity, t)
    z = (cut - pred) / sd
    if method == "normal" or model is None:
        return 1 - stats.norm.cdf(z)
    if method == "t" and model.df_t:
        df = model.df_t
        scale = np.sqrt((df - 2) / df)
        return 1 - stats.t.cdf(z / scale, df)
    if method == "empirical":
        # F(z) from standardised-residual quantiles
        return 1 - np.interp(z, model.std_quantiles, model.grid, left=0.0, right=1.0)
    return 1 - stats.norm.cdf(z)
