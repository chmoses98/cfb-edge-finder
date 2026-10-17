"""Accuracy, distribution and calibration metrics with game-clustered uncertainty.

Conventions
-----------
* bias = mean(prediction - actual). Positive = over-projection.
* Every comparison between two models is paired on the same observations, and its
  uncertainty comes from a cluster bootstrap over games (players in one game share pace,
  script and weather, so player-games are not independent).
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .dists import DistBatch


def point_metrics(y, pred) -> dict:
    y = np.asarray(y, float)
    pred = np.asarray(pred, float)
    e = pred - y
    return {
        "n": int(len(y)),
        "mae": float(np.mean(np.abs(e))),
        "rmse": float(np.sqrt(np.mean(e ** 2))),
        "bias": float(np.mean(e)),
        "median_error": float(np.median(e)),
        "corr": float(np.corrcoef(y, pred)[0, 1]) if len(y) > 2 and np.std(pred) > 0 else float("nan"),
        "mean_actual": float(np.mean(y)),
        "mean_pred": float(np.mean(pred)),
    }


def dist_metrics(y, dist: DistBatch, thresholds=None, rng_seed: int = 0) -> dict:
    y = np.asarray(y, float)
    out = {
        "log_score": float(-np.mean(dist.logpmf(y))),
        "crps": float(np.mean(dist.crps(y))),
        "mae_median": float(np.mean(np.abs(dist.median - y))),
    }
    pit = dist.pit(y, np.random.default_rng(rng_seed))
    for lvl in (0.5, 0.8, 0.9):
        a = (1 - lvl) / 2
        # cover_X: randomised-PIT coverage (exactly X under calibration, comparable across stats)
        out[f"cover_{int(lvl*100)}"] = float(np.mean((pit >= a) & (pit <= 1 - a)))
        # range_cover_X: coverage of the published integer range [q(a), q(1-a)] (conservative on a lattice)
        lo, hi = dist.interval(lvl)
        out[f"range_cover_{int(lvl*100)}"] = float(np.mean((y >= lo) & (y <= hi)))
    hist, _ = np.histogram(pit, bins=10, range=(0, 1))
    frac = hist / max(1, len(pit))
    out["pit_deciles"] = [round(float(f), 4) for f in frac]
    out["pit_max_dev"] = float(np.max(np.abs(frac - 0.1)))
    # PIT variance: 1/12 if calibrated; > 1/12 => overconfident (too narrow), < => underconfident
    out["pit_var_ratio"] = float(np.var(pit) / (1 / 12))
    if thresholds:
        th = {}
        for k in thresholds:
            p = dist.prob_ge(k)
            o = (y >= k).astype(float)
            if 0 < o.mean() < 1:
                th[str(k)] = {
                    "brier": float(np.mean((p - o) ** 2)),
                    "log_loss": float(-np.mean(o * np.log(np.clip(p, 1e-6, 1)) + (1 - o) * np.log(np.clip(1 - p, 1e-6, 1)))),
                    "mean_p": float(p.mean()),
                    "hit_rate": float(o.mean()),
                }
        out["thresholds"] = th
    return out


def calibration_table(p, o, bins=10) -> list[dict]:
    p = np.asarray(p, float)
    o = np.asarray(o, float)
    edges = np.linspace(0, 1, bins + 1)
    b = np.clip(np.digitize(p, edges) - 1, 0, bins - 1)
    rows = []
    for i in range(bins):
        m = b == i
        if m.sum():
            rows.append({"bin": f"{edges[i]:.1f}-{edges[i+1]:.1f}", "n": int(m.sum()),
                         "mean_p": float(p[m].mean()), "hit_rate": float(o[m].mean())})
    return rows


def ece(p, o, bins=10) -> float:
    rows = calibration_table(p, o, bins)
    n = sum(r["n"] for r in rows)
    return float(sum(r["n"] * abs(r["mean_p"] - r["hit_rate"]) for r in rows) / max(n, 1))


def cluster_bootstrap_diff(loss_a, loss_b, clusters, n_boot: int = 2000, seed: int = 0) -> dict:
    """Mean of (loss_a - loss_b) with a 95% cluster-bootstrap CI. Negative = A better."""
    d = np.asarray(loss_a, float) - np.asarray(loss_b, float)
    cl = pd.factorize(np.asarray(clusters))[0]
    k = cl.max() + 1
    sums = np.bincount(cl, weights=d, minlength=k)
    cnts = np.bincount(cl, minlength=k).astype(float)
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, k, size=(n_boot, k))
    boot = sums[idx].sum(axis=1) / cnts[idx].sum(axis=1)
    mean = float(d.mean())
    se = float(boot.std(ddof=1))
    return {
        "mean_diff": mean,
        "ci95": [float(np.quantile(boot, 0.025)), float(np.quantile(boot, 0.975))],
        "se": se,
        "z": float(mean / se) if se > 0 else float("nan"),
        "n_obs": int(len(d)),
        "n_clusters": int(k),
    }


def cluster_ci(values, clusters, n_boot: int = 2000, seed: int = 0) -> list[float]:
    v = np.asarray(values, float)
    return cluster_bootstrap_diff(v, np.zeros_like(v), clusters, n_boot, seed)["ci95"]


def segment_table(df: pd.DataFrame, by, y_col: str, pred_col: str, min_n: int = 50) -> list[dict]:
    """Per-segment bias/MAE (conditional failure modes)."""
    rows = []
    for key, g in df.groupby(by, observed=True):
        if len(g) < min_n:
            continue
        e = g[pred_col] - g[y_col]
        rows.append({"segment": key if not isinstance(key, tuple) else " | ".join(map(str, key)),
                     "n": int(len(g)), "mae": float(e.abs().mean()), "bias": float(e.mean()),
                     "mean_actual": float(g[y_col].mean()), "mean_pred": float(g[pred_col].mean())})
    return rows
