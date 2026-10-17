"""Reusable candidate-model builders for the A..F ladder.

A  season/career average, shrunk to a point-in-time group prior
B  recency-weighted (EWMA) average, shrunk
C  opportunity x efficiency (each component estimated and shrunk separately)
D  C x schedule-adjusted opponent factor (strength of adjustment fitted on development data)
E  gradient-boosted trees / regularised GLM on point-in-time features (walk-forward refits)
F  stacked ensemble with non-negative weights fitted on development predictions only

All inputs are point-in-time feature columns produced by pit.lagged_features (already shifted).
Distribution parameters are fitted on development residuals and then frozen.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import dists
from .pit import eb_shrink


def group_prior_pit(df: pd.DataFrame, group_cols: list[str], date_col: str, col: str,
                    fallback: float | None = None) -> pd.Series:
    """Mean of `col` for the entity's group (e.g. position, lineup slot) using only rows on
    earlier dates. Same-date rows are excluded (they are concurrent games)."""
    d = df[group_cols + [date_col, col]].copy()
    d["_x"] = d[col].astype(float)
    agg = d.groupby(group_cols + [date_col])["_x"].agg(["sum", "count"]).reset_index()
    agg = agg.sort_values(group_cols + [date_col])
    g = agg.groupby(group_cols, sort=False)
    agg["_cs"] = g["sum"].cumsum() - agg["sum"]
    agg["_cc"] = g["count"].cumsum() - agg["count"]
    agg["_prior"] = agg["_cs"] / agg["_cc"].replace(0, np.nan)
    out = d.merge(agg[group_cols + [date_col, "_prior"]], on=group_cols + [date_col], how="left")["_prior"]
    out.index = df.index
    if fallback is None:
        fallback = float(df[col].mean())
    return out.fillna(fallback)


def est_A(df: pd.DataFrame, col: str, prior: pd.Series, k: float, prev_weight: float = 0.5,
          prev_cap: float = 8.0) -> pd.Series:
    """Season-to-date mean blended with last season (each prior-season game worth prev_weight
    current games, capped), shrunk to the group prior with strength k."""
    n_s = df[f"{col}_std_n"].fillna(0).to_numpy(float)
    m_s = df[f"{col}_std"].to_numpy(float)
    n_p = np.minimum(df[f"{col}_prev_n"].fillna(0).to_numpy(float) * prev_weight, prev_cap)
    m_p = df[f"{col}_prev"].to_numpy(float)
    num = np.nan_to_num(m_s) * n_s + np.nan_to_num(m_p) * n_p
    n = n_s + n_p
    mean = np.where(n > 0, num / np.maximum(n, 1e-9), np.nan)
    return pd.Series(eb_shrink(mean, n, prior.to_numpy(float), k), index=df.index)


def est_B(df: pd.DataFrame, col: str, prior: pd.Series, k: float, halflife: int,
          n_cap: float | None = None) -> pd.Series:
    """EWMA (halflife in games) shrunk to prior. Effective evidence is capped because an EWMA
    with halflife h carries at most ~2.9h games of information."""
    n = df[f"{col}_n"].fillna(0).to_numpy(float)
    cap = n_cap if n_cap is not None else 2.9 * halflife
    n_eff = np.minimum(n, cap)
    m = df[f"{col}_ewm{halflife}"].to_numpy(float)
    return pd.Series(eb_shrink(m, n_eff, prior.to_numpy(float), k), index=df.index)


def ratio_rate(df: pd.DataFrame, num_col: str, den_col: str, halflife: int, prior_rate: pd.Series,
               k_den: float) -> pd.Series:
    """Efficiency (num per unit of den, e.g. yards per target) from EWMA sums, shrunk toward a
    prior rate with strength k_den measured in opportunity units (not games)."""
    num = df[f"{num_col}_ewm{halflife}"].to_numpy(float)
    den = df[f"{den_col}_ewm{halflife}"].to_numpy(float)
    n_games = np.minimum(df[f"{den_col}_n"].fillna(0).to_numpy(float), 2.9 * halflife)
    units = np.nan_to_num(den) * n_games
    raw = np.where(den > 0, num / np.where(den > 0, den, 1), np.nan)
    return pd.Series(eb_shrink(raw, units, prior_rate.to_numpy(float), k_den), index=df.index)


def fit_opponent_beta(y, base_mu, log_factor) -> float:
    """Elasticity of the outcome to the opponent factor: y ~ base_mu * exp(beta * log_factor).
    Poisson-likelihood 1-D fit on development data. beta=0 means opponent adds nothing."""
    from scipy.optimize import minimize_scalar
    y = np.asarray(y, float)
    b = np.maximum(np.asarray(base_mu, float), 1e-6)
    lf = np.nan_to_num(np.asarray(log_factor, float))

    def nll(beta):
        mu = b * np.exp(beta * lf)
        return float(np.sum(mu - y * np.log(mu)))

    return float(minimize_scalar(nll, bounds=(-0.5, 2.0), method="bounded").x)


def fit_calibration_scale(y, mu) -> float:
    """Single multiplicative de-bias factor sum(y)/sum(mu) (fitted on development only)."""
    s = float(np.sum(mu))
    return float(np.sum(y) / s) if s > 0 else 1.0


# --------------------------------------------------------------------------- E
def lgbm_walkforward(df: pd.DataFrame, features: list[str], target: str, block_col: str,
                     objective: str = "poisson", min_train_blocks: int = 1, refit_every: int = 1,
                     params: dict | None = None, train_mask_col: str | None = None) -> pd.Series:
    """Gradient-boosted trees refitted walk-forward (train on blocks < b). Returns predictions
    for every row in blocks after the warm-up; NaN for warm-up rows."""
    import lightgbm as lgb
    p = dict(objective=objective, learning_rate=0.05, n_estimators=300, num_leaves=31,
             min_child_samples=100, subsample=0.8, subsample_freq=1, colsample_bytree=0.8,
             reg_lambda=1.0, verbose=-1, n_jobs=2)
    if params:
        p.update(params)
    blocks = sorted(df[block_col].unique())
    pred = pd.Series(np.nan, index=df.index)
    model = None
    for i, b in enumerate(blocks):
        if i < min_train_blocks:
            continue
        if model is None or (i - min_train_blocks) % refit_every == 0:
            tr = df[df[block_col] < b]
            if train_mask_col:
                tr = tr[tr[train_mask_col].astype(bool)]
            tr = tr[tr[target].notna()]
            model = lgb.LGBMRegressor(**p)
            model.fit(tr[features], tr[target])
        te = df[block_col] == b
        pred.loc[te] = model.predict(df.loc[te, features])
    return pred


def ridge_glm_walkforward(df: pd.DataFrame, features: list[str], target: str, block_col: str,
                          alpha: float = 1.0, min_train_blocks: int = 1, refit_every: int = 1,
                          log_link: bool = True) -> pd.Series:
    """Regularised Poisson GLM (log link) or ridge (identity) on standardised PIT features."""
    from sklearn.linear_model import PoissonRegressor, Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    from sklearn.impute import SimpleImputer
    blocks = sorted(df[block_col].unique())
    pred = pd.Series(np.nan, index=df.index)
    model = None
    for i, b in enumerate(blocks):
        if i < min_train_blocks:
            continue
        if model is None or (i - min_train_blocks) % refit_every == 0:
            tr = df[(df[block_col] < b) & df[target].notna()]
            est = PoissonRegressor(alpha=alpha, max_iter=300) if log_link else Ridge(alpha=alpha)
            model = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), est)
            model.fit(tr[features], np.maximum(tr[target], 0) if log_link else tr[target])
        te = df[block_col] == b
        pred.loc[te] = model.predict(df.loc[te, features])
    return pred


# --------------------------------------------------------------------------- F
def fit_stack_weights(y, preds: np.ndarray) -> np.ndarray:
    """Non-negative weights summing to 1 minimising squared error (fit on development only)."""
    from scipy.optimize import nnls
    X = np.asarray(preds, float)
    w, _ = nnls(X, np.asarray(y, float))
    s = w.sum()
    return w / s if s > 0 else np.full(X.shape[1], 1 / X.shape[1])


# --------------------------------------------------------------------------- distributions
def fit_dist_params(kind: str, y, mu, **kw) -> dict:
    y = np.asarray(y, float)
    mu = np.asarray(mu, float)
    if kind == "poisson":
        return {"kind": kind}
    if kind == "nb":
        return {"kind": kind, "alpha": dists.fit_nb_alpha(y, mu)}
    if kind == "nb_binned":
        e, a = dists.fit_nb_alpha_binned(y, mu, kw.get("n_bins", 6))
        return {"kind": kind, "edges": e.tolist(), "alphas": a.tolist()}
    if kind == "normal_binned":
        e, s = dists.fit_sd_binned(y, mu, kw.get("n_bins", 6))
        return {"kind": kind, "edges": e.tolist(), "sds": s.tolist(), "lo": kw.get("lo", 0), "K": kw["K"],
                "floor_at": kw.get("floor_at")}
    if kind in ("empirical_ratio", "empirical_resid"):
        # residual samples per mean bin (quantile mapping)
        n_bins = kw.get("n_bins", 6)
        m = kw.get("m", 400)
        qs = np.unique(np.quantile(mu, np.linspace(0, 1, n_bins + 1)))
        edges = qs[1:-1]
        b = np.digitize(mu, edges)
        samples = []
        for i in range(len(edges) + 1):
            sel = b == i
            r = (y[sel] / np.maximum(mu[sel], 1e-6)) if kind == "empirical_ratio" else (y[sel] - mu[sel])
            if len(r) == 0:
                r = (y / np.maximum(mu, 1e-6)) if kind == "empirical_ratio" else (y - mu)
            # use quantiles (deterministic, smooth) rather than raw draws
            samples.append(np.quantile(r, (np.arange(m) + 0.5) / m).tolist())
        return {"kind": kind, "edges": edges.tolist(), "samples": samples, "lo": kw.get("lo", 0), "K": kw["K"]}
    raise ValueError(kind)


def make_dist(params: dict, mu, p_zero=None) -> dists.DistBatch:
    mu = np.asarray(mu, float)
    k = params["kind"]
    if k == "poisson":
        d = dists.poisson(mu, params.get("K"))
    elif k == "nb":
        d = dists.negbin(mu, params["alpha"], params.get("K"))
    elif k == "nb_binned":
        a = dists.alpha_from_bins(mu, np.array(params["edges"]), np.array(params["alphas"]))
        d = dists.negbin(mu, a, params.get("K"))
    elif k == "normal_binned":
        sd = np.array(params["sds"])[np.digitize(mu, np.array(params["edges"]))]
        d = dists.discretized_normal(mu, sd, params["lo"], params["K"], params.get("floor_at"))
    elif k in ("empirical_ratio", "empirical_resid"):
        b = np.digitize(mu, np.array(params["edges"]))
        S = np.array(params["samples"])[b]
        d = dists.empirical_scale(mu, S, params["lo"], params["K"], additive=(k == "empirical_resid"))
    else:
        raise ValueError(k)
    if p_zero is not None:
        d = dists.hurdle(p_zero, d)
    return d
