"""Scoring, distribution fitting and model selection (development fits, validation selects, holdout once)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core import metrics as pmx
from pf_core import models as pm

SPLITS = {
    "development": (2015, 2019),
    "covid_2020": (2020, 2020),
    "validation": (2021, 2022),
    "holdout": (2023, 2024),
    "holdout_2025": (2025, 2025),
    "live_2026": (2026, 2026),
}

# family -> (target col, lattice scale, lattice lo, lattice K, unit)
FAMILIES = {
    "team_points": ("pts", 1, 0, 101, "points"),
    "plays": ("plays", 1, 20, 101, "plays"),
    "team_passing_yards": ("pass_yds", 1, -20, 621, "yards"),
    "team_rushing_yards": ("rush_yds", 1, -60, 621, "yards"),
    "points_per_play": ("ppp", 100, 0, 201, "points/play x100"),
    "epa_per_play": ("epa", 100, -150, 401, "EPA/play x100"),
    "success_rate": ("sr", 100, 0, 101, "success % points"),
}


def split_of(season: int) -> str:
    for k, (a, b) in SPLITS.items():
        if a <= season <= b:
            return k
    return "warmup"


def fit_dist(kind: str, y, mu, lo: int, K: int) -> dict:
    y, mu = np.asarray(y, float), np.asarray(mu, float)
    if kind == "normal":
        return pm.fit_dist_params("normal_binned", y, mu, lo=lo, K=K, n_bins=6)
    if kind == "empirical":
        return pm.fit_dist_params("empirical_resid", y, mu, lo=lo, K=K, n_bins=6, m=400)
    if kind == "nb":
        p = pm.fit_dist_params("nb", np.maximum(y, 0), np.maximum(mu, 0.1))
        p["K"] = K
        return p
    raise ValueError(kind)


def make(params: dict, mu):
    mu = np.asarray(mu, float)
    if params["kind"] == "nb":
        return pm.make_dist(params, np.maximum(mu, 0.1))
    return pm.make_dist(params, mu)


def score(y, mu, dist, clusters, scale: float = 1.0) -> dict:
    """Point metrics in natural units; distribution metrics on the lattice (CRPS rescaled to natural units)."""
    yy = np.asarray(y, float) * scale
    pt = pmx.point_metrics(np.asarray(y, float), np.asarray(mu, float))
    dm = pmx.dist_metrics(np.rint(yy), dist)
    return {"n_obs": pt["n"], "n_clusters": int(pd.Series(clusters).nunique()), "mae": pt["mae"], "rmse": pt["rmse"],
            "bias": pt["bias"], "corr": pt["corr"], "crps": dm["crps"] / scale, "log_score": dm["log_score"],
            "cover_50": dm["cover_50"], "cover_80": dm["cover_80"], "cover_90": dm["cover_90"],
            "pit_var_ratio": dm["pit_var_ratio"], "pit_max_dev": dm["pit_max_dev"]}


def crps_vec(dist, y, scale: float = 1.0):
    return dist.crps(np.rint(np.asarray(y, float) * scale)) / scale


def compare(loss_a, loss_b, clusters) -> dict:
    r = pmx.cluster_bootstrap_diff(loss_a, loss_b, clusters, n_boot=1000, seed=0)
    return {"mean_diff": r["mean_diff"], "ci95": r["ci95"], "n_clusters": r["n_clusters"]}


def significant(cmp: dict) -> bool:
    lo, hi = cmp["ci95"]
    return hi < 0 or lo > 0
