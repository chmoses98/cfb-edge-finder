"""Walk-forward team state: opponent-adjusted metric strengths as of a
(season, week) cutoff, from STRICTLY PRIOR games only.

*** THE ONE SOLVE PER AS-OF TRICK ***
For every metric m observed for team T against opponent O in a prior
game g, the model is

    m_off(T, g) = mu_m + off_m[T] - def_m[O] + hfa_m * home_ind + e

All metrics share the SAME design matrix (rows = team-games, columns =
[mu, hfa, off_1..off_k, def_1..def_k]) and the SAME recency weights, so
one weighted ridge normal-equation solve with a multi-column right-hand
side yields every metric's offense/defense strengths at once.

Rows: for each prior game, two rows (each side's offense). The offense
metric for the row is the team's `o_*`; the defensive strength of the
opponent is what the `- def[O]` term absorbs. Defensive strengths are
therefore "what this team's defense allows", opponent-adjusted; a LOWER
def_m for PPA-like metrics means a better defense... to keep signs
intuitive, `def_m` is stored as +(allowed), i.e. higher = worse defense.

FCS sides are pooled into one pseudo-team.

Leakage: the caller passes `history` (rows strictly before the cutoff)
and `cutoff`; every row is asserted to be strictly prior.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
import scipy.sparse as sp

FCS_ID = "__fcs__"


@dataclass
class StateConfig:
    season_decay: float = 0.5
    lam: float = 6.0
    fcs_lam: float = 2.0
    min_games_full: int = 6
    """Not a hard threshold -- ridge shrinkage handles sparsity; kept for reporting."""

    def to_dict(self) -> dict:
        return {"season_decay": self.season_decay, "lam": self.lam, "fcs_lam": self.fcs_lam}


def side_rows(games: pd.DataFrame, tg: pd.DataFrame, metrics: list[str]) -> pd.DataFrame:
    """Long table: one row per (game, side) with offense metric values,
    opponent, home indicator, season, week. Values missing in `tg` stay NaN.
    """
    base_cols = ["game_id", "season", "week", "neutral", "home", "away", "home_class", "away_class",
                 "home_points", "away_points"]
    g = games[base_cols]
    home = pd.DataFrame({
        "game_id": g.game_id, "season": g.season, "week": g.week,
        "team": g.home, "opp": g.away, "team_fbs": g.home_class == "fbs", "opp_fbs": g.away_class == "fbs",
        "home_ind": np.where(g.neutral, 0.0, 1.0),
        "pts_for": g.home_points, "pts_against": g.away_points,
    })
    away = pd.DataFrame({
        "game_id": g.game_id, "season": g.season, "week": g.week,
        "team": g.away, "opp": g.home, "team_fbs": g.away_class == "fbs", "opp_fbs": g.home_class == "fbs",
        "home_ind": np.where(g.neutral, 0.0, -1.0),
        "pts_for": g.away_points, "pts_against": g.home_points,
    })
    long = pd.concat([home, away], ignore_index=True)
    if len(tg):
        cols = ["game_id", "team"] + [m for m in metrics if m in tg.columns]
        long = long.merge(tg[cols], on=["game_id", "team"], how="left")
    for m in metrics:
        if m not in long.columns:
            long[m] = np.nan
    long["team_key"] = np.where(long.team_fbs, long.team, FCS_ID)
    long["opp_key"] = np.where(long.opp_fbs, long.opp, FCS_ID)
    long["margin"] = long.pts_for - long.pts_against
    return long


@dataclass
class TeamState:
    season: int
    week: int
    teams: list[str]
    offense: pd.DataFrame  # index team_key, columns metrics
    defense: pd.DataFrame
    mu: pd.Series
    hfa: pd.Series
    games_this_season: pd.Series
    games_weighted: pd.Series

    def get(self, team_key: str, side: str, metric: str) -> float:
        tbl = self.offense if side == "o" else self.defense
        if team_key in tbl.index:
            return float(tbl.at[team_key, metric])
        return 0.0


def fit_state(long: pd.DataFrame, metrics: list[str], *, cutoff_season: int, cutoff_week: int,
              cfg: StateConfig) -> TeamState:
    """Fit all metrics at once from rows strictly before (cutoff_season, cutoff_week)."""
    ordv = long.season.values * 100 + long.week.values
    cut = cutoff_season * 100 + cutoff_week
    hist = long[ordv < cut]
    assert (hist.season.values * 100 + hist.week.values < cut).all()
    w = np.power(cfg.season_decay, (cutoff_season - hist.season.values).astype(float))
    teams = sorted(set(hist.team_key) | set(hist.opp_key))
    idx = {t: i for i, t in enumerate(teams)}
    k = len(teams)
    n = len(hist)
    ti = np.array([idx[t] for t in hist.team_key], dtype=int)
    oi = np.array([idx[t] for t in hist.opp_key], dtype=int)
    rows = np.repeat(np.arange(n), 4)
    cols = np.stack([np.zeros(n, int), np.ones(n, int), 2 + ti, 2 + k + oi], axis=1).ravel()
    vals = np.stack([np.ones(n), hist.home_ind.values.astype(float), np.ones(n), -np.ones(n)], axis=1).ravel()
    X = sp.csr_matrix((vals, (rows, cols)), shape=(n, 2 + 2 * k))
    pen = np.full(2 + 2 * k, cfg.lam)
    pen[:2] = 1e-6
    if FCS_ID in idx:
        pen[2 + idx[FCS_ID]] = cfg.fcs_lam
        pen[2 + k + idx[FCS_ID]] = cfg.fcs_lam
    offense = pd.DataFrame(index=teams, columns=metrics, dtype=float)
    defense = pd.DataFrame(index=teams, columns=metrics, dtype=float)
    mu = pd.Series(index=metrics, dtype=float)
    hfa = pd.Series(index=metrics, dtype=float)
    # metrics may have different missingness; group by identical mask to reuse solves
    Y = hist[metrics].values.astype(float)
    masks: dict[bytes, list[int]] = {}
    for j in range(len(metrics)):
        key = np.isfinite(Y[:, j]).tobytes()
        masks.setdefault(key, []).append(j)
    for key, js in masks.items():
        m = np.frombuffer(key, dtype=bool)
        if m.sum() < 10:
            for j in js:
                offense[metrics[j]] = 0.0
                defense[metrics[j]] = 0.0
                mu[metrics[j]] = float(np.nanmean(Y[:, j])) if m.any() else 0.0
                hfa[metrics[j]] = 0.0
            continue
        Xm = X[m]
        wm = w[m]
        Xw = Xm.multiply(wm[:, None]).tocsr()
        A = (Xm.T @ Xw).toarray() + np.diag(pen)
        B = Xw.T @ Y[m][:, js]
        beta = np.linalg.solve(A, B)
        for c, j in enumerate(js):
            name = metrics[j]
            mu[name] = beta[0, c]
            hfa[name] = beta[1, c]
            offense[name] = beta[2:2 + k, c]
            defense[name] = beta[2 + k:, c]
    this_season = hist[hist.season == cutoff_season].groupby("team_key").size()
    weighted = pd.Series(np.bincount(ti, weights=w, minlength=k), index=teams)
    return TeamState(cutoff_season, cutoff_week, teams, offense, defense, mu, hfa,
                     this_season.reindex(teams).fillna(0).astype(int), weighted)
