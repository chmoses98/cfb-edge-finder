"""Paired, game-clustered comparison of challenger vs production on identical games."""
from __future__ import annotations

import numpy as np
import pandas as pd


def calibration_slope(p, y) -> float:
    """Slope of a logistic regression of the outcome on logit(p) (1 = calibrated, <1 = over-confident)."""
    p = np.clip(np.asarray(p, float), 1e-6, 1 - 1e-6)
    x = np.log(p / (1 - p))
    b = np.zeros(2)
    X = np.c_[np.ones_like(x), x]
    for _ in range(50):
        mu = 1 / (1 + np.exp(-X @ b))
        W = mu * (1 - mu)
        b = b + np.linalg.solve(X.T @ (X * W[:, None]) + 1e-9 * np.eye(2), X.T @ (y - mu))
    return float(b[1])


def per_game_losses(df: pd.DataFrame, tag: str) -> pd.DataFrame:
    """df has actual home/away pts and {tag}_margin, {tag}_total, {tag}_p, {tag}_home, {tag}_away."""
    am, at = df.home_pts - df.away_pts, df.home_pts + df.away_pts
    y = (am > 0).astype(float)
    p = np.clip(df[f"{tag}_p"], 1e-6, 1 - 1e-6)
    return pd.DataFrame({
        "margin_ae": (df[f"{tag}_margin"] - am).abs(),
        "total_ae": (df[f"{tag}_total"] - at).abs(),
        "team_pts_ae": ((df[f"{tag}_home"] - df.home_pts).abs() + (df[f"{tag}_away"] - df.away_pts).abs()) / 2,
        "brier": (p - y) ** 2,
        "logloss": -(y * np.log(p) + (1 - y) * np.log(1 - p)),
    })


def compare(df: pd.DataFrame, a: str = "ch", b: str = "prod", n_boot: int = 2000, seed: int = 0) -> dict:
    """Per-metric means and paired difference a - b (negative = a better) with a game-clustered bootstrap CI.
    Each game is one cluster (its team-points loss is the mean over its two team rows)."""
    la, lb = per_game_losses(df, a), per_game_losses(df, b)
    rng = np.random.default_rng(seed)
    n = len(df)
    idx = rng.integers(0, n, size=(n_boot, n))
    y = (df.home_pts > df.away_pts).astype(float).to_numpy()
    out = {"n_games": int(n)}
    for m in la.columns:
        d = (la[m] - lb[m]).to_numpy()
        bs = d[idx].mean(1)
        out[m] = {a: float(la[m].mean()), b: float(lb[m].mean()), "diff": float(d.mean()),
                  "ci95": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))]}
    pa, pb = df[f"{a}_p"].to_numpy(), df[f"{b}_p"].to_numpy()
    sa, sb = calibration_slope(pa, y), calibration_slope(pb, y)
    bs = [calibration_slope(pa[i], y[i]) - calibration_slope(pb[i], y[i]) for i in idx[:400]]
    out["calib_slope"] = {a: sa, b: sb, "diff": sa - sb,
                          "ci95": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))]}
    return out
