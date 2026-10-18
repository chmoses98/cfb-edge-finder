"""G: game-level walk-forward model of margin and total from PIT team ratings, mapped back to team points.

Added after the first scoring pass showed (on VALIDATION) that the archived 2026 'v2' research predictions beat the
team-points stack on margin and total. Coherence is preserved: home = (total + margin)/2, away = (total - margin)/2,
and the same joint distribution machinery is applied to those team means.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from . import data

TARGETS = ("pts", "ppp", "epa", "sr", "plays", "pass_yds", "rush_yds", "ypa", "ypc", "drives")


def game_features(X: pd.DataFrame) -> pd.DataFrame:
    pre = data.preseason_inputs()
    X = X.merge(pre[["season", "team_id", "talent", "ret_ppa", "recruit4", "new_coach"]], on=["season", "team_id"], how="left")
    h = X[X["team_id"] == X["home_id"]].set_index("game_id")
    a = X[X["team_id"] != X["home_id"]].set_index("game_id")
    ids = h.index.intersection(a.index)
    h, a = h.loc[ids], a.loc[ids]
    F = pd.DataFrame(index=ids)
    for t in TARGETS:
        if f"D_{t}_pred" not in X:
            continue
        # expected home offence vs away defence and vice versa (the ratings' own matchup predictions)
        F[f"h_{t}"] = h[f"D_{t}_pred"].to_numpy()
        F[f"a_{t}"] = a[f"D_{t}_pred"].to_numpy()
        F[f"d_{t}"] = F[f"h_{t}"] - F[f"a_{t}"]
        F[f"s_{t}"] = F[f"h_{t}"] + F[f"a_{t}"]
    for c in ("talent", "ret_ppa", "recruit4"):
        # impute missing values explicitly (no 'has_' indicator: a season-wide gap, e.g. a source missing for the
        # live season, must not masquerade as an FCS game): non-FBS teams get the season's FBS 5th percentile,
        # FBS teams the season's FBS median (ret_ppa: median for both)
        fb = X[X["team_div"] == "fbs"].groupby("season")[c]
        lo, med = fb.quantile(0.05), fb.median()
        def imp(side):
            v = side[c].astype(float).copy()
            fill_lo = side["season"].map(med if c == "ret_ppa" else lo)
            fill_med = side["season"].map(med)
            v = v.where(v.notna(), np.where(side["team_div"] != "fbs", fill_lo, fill_med))
            return v.to_numpy(float)
        F[f"d_{c}"] = np.nan_to_num(imp(h) - imp(a))
    F["early"] = (h["week"] <= 4).astype(float).to_numpy()
    for c in ("d_talent", "d_ret_ppa"):
        F[f"{c}_early"] = F[c] * F["early"]
    F["new_coach_diff"] = np.nan_to_num(h["new_coach"].to_numpy(float)) - np.nan_to_num(a["new_coach"].to_numpy(float))
    F["neutral"] = (h["site"] == 0).astype(float).to_numpy()
    F["home_fcs"] = (h["team_div"] != "fbs").astype(float).to_numpy()
    F["away_fcs"] = (a["team_div"] != "fbs").astype(float).to_numpy()
    F["week"] = h["week"].to_numpy(float)
    F["cur_n"] = np.nan_to_num(h["D_pts_n_off"].to_numpy(float))
    F["season"] = h["season"].to_numpy()
    F["post"] = h["post"].to_numpy()
    F["margin"] = (h["pts"] - a["pts"]).to_numpy()
    F["total"] = (h["pts"] + a["pts"]).to_numpy()
    F["home_rowid"] = h["_rowid"].to_numpy()
    F["away_rowid"] = a["_rowid"].to_numpy()
    return F.reset_index()


def walkforward(F: pd.DataFrame, target: str, alpha: float, first_train: int = 2015) -> pd.Series:
    from sklearn.impute import SimpleImputer
    from sklearn.linear_model import Ridge
    from sklearn.pipeline import make_pipeline
    from sklearn.preprocessing import StandardScaler
    feats = [c for c in F.columns if c.startswith(("h_", "a_", "d_", "s_")) or c in
             ("neutral", "home_fcs", "away_fcs", "early", "d_talent_early", "d_ret_ppa_early", "new_coach_diff")]
    if target == "margin":
        feats = [c for c in feats if not c.startswith("s_")]
    pred = pd.Series(np.nan, index=F.index)
    for s in sorted(F["season"].unique()):
        if s <= first_train:
            continue
        tr = F[(F["season"] < s) & (F["season"] >= first_train) & F[target].notna()]
        if len(tr) < 500:
            continue
        m = make_pipeline(SimpleImputer(strategy="median"), StandardScaler(), Ridge(alpha=alpha))
        m.fit(tr[feats], tr[target])
        te = F["season"] == s
        pred[te] = m.predict(F.loc[te, feats])
    return pred


def team_means(F: pd.DataFrame, margin: pd.Series, total: pd.Series, n_rows: int) -> pd.Series:
    out = pd.Series(np.nan, index=range(n_rows))
    out.loc[F["home_rowid"].to_numpy()] = ((total + margin) / 2).to_numpy()
    out.loc[F["away_rowid"].to_numpy()] = ((total - margin) / 2).to_numpy()
    return out
