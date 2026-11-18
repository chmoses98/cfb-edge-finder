"""Phase 15 extensions of the #307 challenger (research only; nothing in production imports this).

1. Centred ridge ratings: in season S team effects are shrunk toward rho * (end-of-season S-1 rating) instead of 0,
   so weeks 0-3 start from a regressed prior-season rating (`Params15.lam_c`, `rho`).
2. Week-dependent uncertainty: log sigma = g0 + g1 * k/(k + games played), fitted on out-of-fold residuals.
3. Period ratings: the same walk-forward ridge on period points (Q1..Q4, 1H, 2H excl. OT), and a period layer fitted
   to period outcomes; shape = discretised normal or an empirical nearest-neighbour PMF (lumpy football scores).

Every feature for a game on date d uses only team-games dated strictly before d (see `walk_forward`).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
import sys

import numpy as np
import pandas as pd
from scipy.stats import norm

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from challenger import model as M  # noqa: E402

PERIODS = ("q1", "q2", "q3", "q4", "h1", "h2")
PMETRICS = tuple(f"pp_{p}" for p in PERIODS)


@dataclass
class Params15:
    lam: float = 1.0           # #307 frozen
    w_prev: float = 0.1        # #307 frozen
    w_prev2: float = 0.1       # #307 frozen
    lam_c: float = 0.0         # extra penalty toward the regressed prior-season rating (0 = #307 exactly)
    rho: float = 0.0           # regression of the prior-season end rating toward 0
    periods: bool = False      # also rate period points
    metrics: tuple = field(default=M.METRICS)


def add_period_points(tg: pd.DataFrame, per: pd.DataFrame) -> pd.DataFrame:
    """Attach period points for/against per team-game (NaN where linescores invalid)."""
    p = per[per.valid].copy()
    rows = []
    for side, opp, tid in (("h", "a", "home"), ("a", "h", "away")):
        d = pd.DataFrame({"game_id": p.game_id.astype(str), "team_id": p[tid].astype(str)})
        for q in range(1, 5):
            d[f"pp_q{q}"] = p[f"{side}_q{q}"].to_numpy(float)
            d[f"pa_q{q}"] = p[f"{opp}_q{q}"].to_numpy(float)
        rows.append(d)
    d = pd.concat(rows)
    for pre in ("pp", "pa"):
        d[f"{pre}_h1"] = d[f"{pre}_q1"] + d[f"{pre}_q2"]
        d[f"{pre}_h2"] = d[f"{pre}_q3"] + d[f"{pre}_q4"]
    out = tg.merge(d, on=["game_id", "team_id"], how="left")
    assert len(out) == len(tg)
    return out


def fit_ratings_c(rows: pd.DataFrame, weights: np.ndarray, lam: float, metrics, centre=None, lam_c: float = 0.0):
    """Weighted ridge y = mu + hfa*h + O[off] - D[dfn]; team effects penalised by lam*||b||^2 + lam_c*||b - c||^2."""
    teams = pd.Index(sorted(set(rows["off"]) | set(rows["dfn"])))
    T = len(teams)
    oi, di = teams.get_indexer(rows["off"]), teams.get_indexer(rows["dfn"])
    n = len(rows)
    r = np.arange(n)
    X = np.zeros((n, 2 * T + 2))
    X[:, 0] = 1.0
    X[:, 1] = rows["hflag"].to_numpy(float)
    X[r, 2 + oi] = 1.0
    X[r, 2 + T + di] = -1.0
    pen = np.diag(np.r_[0.0, 0.0, np.full(2 * T, lam + lam_c)])
    out = {}
    for m in metrics:
        y = rows[m].to_numpy(float)
        ok = np.isfinite(y)
        w = weights * ok
        Xw = X * w[:, None]
        rhs = Xw.T @ np.where(ok, y, 0.0)
        if centre is not None and lam_c > 0 and m in centre:
            c = centre[m]
            cvec = np.r_[0.0, 0.0, [c["O"].get(t, 0.0) for t in teams], [c["D"].get(t, 0.0) for t in teams]]
            rhs = rhs + lam_c * cvec
        beta = np.linalg.solve(X.T @ Xw + pen + 1e-9 * np.eye(2 * T + 2), rhs)
        out[m] = {"mu": beta[0], "hfa": beta[1], "O": dict(zip(teams, beta[2:2 + T])), "D": dict(zip(teams, beta[2 + T:]))}
    return out


def _metrics(p: Params15):
    return tuple(p.metrics) + (PMETRICS if p.periods else ())


def season_centre(t: pd.DataFrame, s: int, p: Params15) -> dict | None:
    """rho * end-of-season (S-1) rating, fitted only on seasons S-2 (weight w_prev2) and S-1 (weight 1)."""
    if p.lam_c <= 0:
        return None
    base = t[t.season.isin([s - 2, s - 1])]
    if base.empty:
        return None
    w = np.where(base.season == s - 1, 1.0, p.w_prev2)
    R = fit_ratings_c(base, w, p.lam, _metrics(p))
    return {m: {"O": {k: p.rho * v for k, v in r["O"].items()}, "D": {k: p.rho * v for k, v in r["D"].items()}}
            for m, r in R.items()}


def walk_forward(tg: pd.DataFrame, games: pd.DataFrame, pre: pd.DataFrame, p: Params15, seasons,
                 dates=None) -> pd.DataFrame:
    """Features per game, ratings fit on team-games dated strictly before the game date. `tg` must already carry
    period columns when p.periods. With lam_c = 0 and periods = False this equals #307's walk_forward_features."""
    t = M.team_game_metrics(tg)
    pre_ix = pre.set_index(["season", "team"])
    mets = _metrics(p)
    feats = []
    for s in seasons:
        base = t[t.season.isin([s - 2, s - 1])]
        bw = np.where(base.season == s - 1, p.w_prev, p.w_prev2)
        cur = t[t.season == s]
        gs = games[games.season == s]
        centre = season_centre(t[t.season < s], s, p)
        for d in sorted(gs.date.unique()):
            if dates is not None and d not in dates:
                continue
            c = cur[cur.date < d]
            rows = pd.concat([base, c])
            w = np.r_[bw, np.ones(len(c))]
            R = fit_ratings_c(rows, w, p.lam, mets, centre, p.lam_c)
            gp = c.groupby("team_id").size()
            for g in gs[gs.date == d].itertuples():
                f = {"game_id": g.game_id}
                hk = g.home if g.home_div == "fbs" else M.FCS
                ak = g.away if g.away_div == "fbs" else M.FCS
                hf = 0.0 if g.neutral else 1.0
                for m in mets:
                    r = R[m]
                    f[f"{m}_h"] = r["mu"] + r["hfa"] * hf + r["O"].get(hk, 0.0) - r["D"].get(ak, 0.0)
                    f[f"{m}_a"] = r["mu"] - r["hfa"] * hf + r["O"].get(ak, 0.0) - r["D"].get(hk, 0.0)
                for side, team in (("h", g.home), ("a", g.away)):
                    k = (s, team)
                    f[f"tal_{side}"] = float(pre_ix.loc[k, "talent_z"]) if k in pre_ix.index else 0.0
                    f[f"ret_{side}"] = float(pre_ix.loc[k, "ret_z"]) if k in pre_ix.index else 0.0
                    f[f"n_{side}"] = gp.get(team, 0)
                f["hf"] = hf
                feats.append(f)
    return pd.DataFrame(feats)


# ---------------------------------------------------------------- week-dependent sigma
def fit_sigma_curve(resid: np.ndarray, n_avg: np.ndarray, k: float) -> dict:
    """MLE of log sigma = g0 + g1 * k/(k + n) for normal residuals."""
    from scipy.optimize import minimize
    x = k / (k + n_avg)

    def nll(g):
        ls = g[0] + g[1] * x
        return np.sum(ls + 0.5 * resid ** 2 * np.exp(-2 * ls))
    g = minimize(nll, np.array([np.log(np.std(resid)), 0.0]), method="BFGS").x
    return {"g0": float(g[0]), "g1": float(g[1]), "k": k}


def sigma_of(curve: dict, n_avg) -> np.ndarray:
    x = curve["k"] / (curve["k"] + np.asarray(n_avg, float))
    return np.exp(curve["g0"] + curve["g1"] * x)


def win_prob(margin, sigma):
    """P(margin > 0) with the 0.5 continuity correction (same convention as #307 / production)."""
    return norm.sf((0.5 - np.asarray(margin, float)) / np.asarray(sigma, float))


# ---------------------------------------------------------------- period layer
def period_design(F: pd.DataFrame, side: str, per: str, fg_for, fg_against) -> np.ndarray:
    """Team-period design: full-game team points forecast for/against + opponent-adjusted period expectation."""
    o = "a" if side == "h" else "h"
    sgn = 1.0 if side == "h" else -1.0
    return np.c_[fg_for, fg_against, F[f"pp_{per}_{side}"], F[f"pp_{per}_{o}"], sgn * F.hf]


def ridge_fit(X, y, alpha=1.0):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.c_[np.ones(len(X)), (X - mu) / sd]
    b = np.linalg.solve(Z.T @ Z + np.diag(np.r_[0.0, np.full(X.shape[1], alpha)]), Z.T @ y)
    return {"mu": mu.tolist(), "sd": sd.tolist(), "b": b.tolist()}


def ridge_apply(c, X):
    return np.c_[np.ones(len(X)), (X - np.array(c["mu"])) / np.array(c["sd"])] @ np.array(c["b"])


class NeighbourPMF:
    """Empirical PMF of integer team-period points given a predicted mean: the outcomes of the `k` training rows
    whose predicted mean is closest (sorted-array window), lightly smoothed toward the global PMF."""

    def __init__(self, mu_train: np.ndarray, y_train: np.ndarray, k: int = 1500, nmax: int = 60, smooth: float = 0.02):
        o = np.argsort(mu_train)
        self.mu, self.y = np.asarray(mu_train, float)[o], np.clip(np.asarray(y_train, int)[o], 0, nmax - 1)
        self.k, self.nmax, self.smooth = min(k, len(self.mu)), nmax, smooth
        self.glob = np.bincount(self.y, minlength=nmax) / len(self.y)
        self.cum = np.zeros((len(self.y) + 1, nmax))
        self.cum[1:] = np.cumsum(np.eye(nmax)[self.y], axis=0)

    def pmf(self, m: float) -> np.ndarray:
        i = int(np.searchsorted(self.mu, m))
        lo = int(np.clip(i - self.k // 2, 0, len(self.mu) - self.k))
        h = (self.cum[lo + self.k] - self.cum[lo]) / self.k
        return (1 - self.smooth) * h + self.smooth * self.glob


def joint_independent(ph: np.ndarray, pa: np.ndarray, tie_scale: float = 1.0) -> np.ndarray:
    """Joint (home, away) PMF from two marginals; tie_scale rescales the diagonal (fitted dependence)."""
    J = np.outer(ph, pa)
    if tie_scale != 1.0:
        d = np.diag_indices_from(J)
        J[d] *= tie_scale
    return J / J.sum()
