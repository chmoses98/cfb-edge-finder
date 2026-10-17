"""A..F ladder for CFB team families (unit = team-game: a team's offence against an opponent's defence).

A  season-to-date mean blended with last season, shrunk to the PIT division mean (no opponent)
B  EWMA (carries across seasons = decayed preseason prior) + RAW opponent-allowed EWMA (no SOS)
C  opportunity x efficiency from B-style components: E[plays] x E[per-play rate] (raw, no SOS)
D  schedule-adjusted ridge ratings (pf_core.rolling_ridge_ratings via ratings.py), FCS pooled,
   prior seasons carried with decay; D_C = ridge plays x ridge per-play rate; D_prior = current-season
   ridge shrunk toward a talent/returning-production preseason prior
E  LightGBM on PIT features (ratings, raw form, context), refit each season on prior seasons
F  non-negative stack of B/C/D/E fitted on development predictions only
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core import models as pm
from pf_core import pit

from . import ratings

HL = 4          # EWMA halflife in games
DEV = (2015, 2019)


# ----------------------------------------------------------------------------- raw components
def _lagged(T: pd.DataFrame, entity: str, cols: list[str], prefix: str) -> pd.DataFrame:
    d = T[["game_id", entity, "kickoff", "season"] + cols].copy()
    d = pit.sort_chrono(d, entity, "kickoff", "game_id")
    d = pit.lagged_features(d, entity, cols, halflives=(HL,), season_col="season", prefix=prefix)
    keep = ["game_id", entity] + [c for c in d.columns if c.startswith(prefix)]
    return d[keep]


def raw_features(T: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Own-offence and opponent-defence-allowed lagged features (PIT: shifted one game)."""
    off = _lagged(T, "team_id", cols, "o_")
    de = _lagged(T.rename(columns={"opp_id": "def_id"}), "def_id", cols, "a_").rename(columns={"def_id": "opp_id"})
    X = T.merge(off, on=["game_id", "team_id"], how="left").merge(de, on=["game_id", "opp_id"], how="left")
    return X


def _prior(X: pd.DataFrame, col: str, group: str) -> pd.Series:
    """PIT mean of `col` among teams of the same division (only rows on earlier dates)."""
    return pm.group_prior_pit(X, [group], "kickoff", col)


def _renamed(X: pd.DataFrame, pre: str, col: str) -> pd.DataFrame:
    """est_A / est_B expect columns '<col>_std' etc.; expose the prefixed ones under a temp name."""
    m = {c: c[len(pre):] for c in X.columns if c.startswith(pre + col + "_")}
    return X[list(m)].rename(columns=m)


def model_A(X: pd.DataFrame, y: str, k: float) -> pd.Series:
    prior = _prior(X, y, "team_div")
    return pm.est_A(_renamed(X, "o_", y), y, prior, k)


def _B_component(X: pd.DataFrame, pre: str, y: str, k: float, group: str) -> pd.Series:
    prior = _prior(X, y, group)
    return pm.est_B(_renamed(X, pre, y), y, prior, k, HL)


def model_B(X: pd.DataFrame, y: str, k_off: float, k_def: float) -> pd.Series:
    own = _B_component(X, "o_", y, k_off, "team_div")
    allowed = _B_component(X, "a_", y, k_def, "opp_div")
    league = _prior(X, y, "fbs_game")
    return own + allowed - league


def model_C(X: pd.DataFrame, opp: str, num: str, k: dict) -> pd.Series:
    """E[opportunity] x E[rate], each from model_B on its own scale (rate = num/opp per game)."""
    rate = f"{num}_per_{opp}"
    plays = model_B(X, opp, k[opp], k[opp])
    r = model_B(X, rate, k[rate], k[rate])
    return plays * r


def estimate_k(X: pd.DataFrame, col: str, entity: str = "team_id") -> float:
    d = X[X["season"].between(*DEV) & X["fbs_game"]]
    return pit.estimate_shrinkage_k(d[col], d[entity].astype(str) + "_" + d["season"].astype(str))


# ----------------------------------------------------------------------------- ridge family
def ridge_pred(T: pd.DataFrame, y: str, seasons, prefix: str, **kw) -> pd.DataFrame:
    R = ratings.walkforward(T, y, seasons, **kw)
    return ratings.attach(T, R, prefix, kw.get("fcs_mode", "pool"))


def tune_ridge(T: pd.DataFrame, y: str, alphas, decays, seasons=range(2015, 2020), pop=None) -> list[dict]:
    """Development-only grid (MAE on FBS-vs-FBS regular season)."""
    out = []
    for a in alphas:
        for dcy in decays:
            X = ridge_pred(T, y, seasons, "tmp", alpha=a, decay=dcy, history=3, fcs_mode="pool")
            m = X["season"].between(seasons[0], seasons[-1]) & ~X["post"] & X["fbs_game"] & X[y].notna()
            if pop is not None:
                m &= pop
            e = (X.loc[m, "tmp_pred"] - X.loc[m, y]).abs()
            out.append({"alpha": a, "decay": dcy, "mae": float(e.mean()), "n": int(m.sum())})
    return out


# ----------------------------------------------------------------------------- preseason prior
def preseason_prior(T: pd.DataFrame, Rcarry: pd.DataFrame, pre: pd.DataFrame, y: str, seasons,
                    alpha_cur: float = 5.0) -> pd.DataFrame:
    """Per (season, team): prior off/def rating = regression of the season's own end-of-season rating
    (fitted on that season only; used ONLY as a training target for earlier folds) on last season's
    carried rating, talent and returning production. Coefficients for season s use seasons < s."""
    # last-season carried rating = the carry model's rating in season s's first block (week 1)
    first = Rcarry.groupby(Rcarry["block"] // 100)["block"].transform("min") == Rcarry["block"]
    R0 = Rcarry[first].assign(season=lambda d: d["block"] // 100)[["season", "team", "off_rating", "def_rating"]]
    R0 = R0.rename(columns={"team": "team_id", "off_rating": "last_off", "def_rating": "last_def"})
    # end-of-season single-season rating (retrospective target for fitting coefficients only)
    ends = []
    for s in sorted(set(seasons) | {min(seasons) - 1, min(seasons) - 2, min(seasons) - 3}):
        if s < 2015 or s > 2025:
            continue
        r = ratings.season_ratings(T, y, s, alpha=alpha_cur, use_prior_seasons=False, fcs_mode="pool")
        if len(r):
            last = r[r["block"] == r["block"].max()]
            ends.append(pd.DataFrame({"season": s, "team_id": last["team"], "end_off": last["off_rating"],
                                      "end_def": last["def_rating"]}))
    E = pd.concat(ends, ignore_index=True)
    D = R0.merge(pre, on=["season", "team_id"], how="left").merge(E, on=["season", "team_id"], how="left")
    D = D[D["team_id"] != "FCS"].copy()
    for c in ("talent", "recruit4", "ret_ppa", "ret_usage"):
        if c in D:
            z = D.groupby("season")[c].transform(lambda s: (s - s.mean()) / s.std())
            D[c + "_z"] = z.fillna(0.0)
        else:
            D[c + "_z"] = 0.0
    D["ret_x_last"] = D["ret_ppa_z"] * D["last_off"]
    feats_o = ["last_off", "talent_z", "ret_ppa_z", "ret_x_last"]
    feats_d = ["last_def", "talent_z", "ret_ppa_z"]
    out = []
    from sklearn.linear_model import Ridge
    for s in seasons:
        tr = D[(D["season"] < s) & (D["season"] >= 2015) & D["end_off"].notna()]
        te = D[D["season"] == s].copy()
        if len(tr) < 100:
            te["prior_off"], te["prior_def"] = te["last_off"], te["last_def"]
        else:
            mo = Ridge(alpha=1.0).fit(tr[feats_o].fillna(0), tr["end_off"])
            md = Ridge(alpha=1.0).fit(tr[feats_d].fillna(0), tr["end_def"])
            te["prior_off"] = mo.predict(te[feats_o].fillna(0))
            te["prior_def"] = md.predict(te[feats_d].fillna(0))
        out.append(te[["season", "team_id", "prior_off", "prior_def", "last_off", "last_def", "talent_z", "ret_ppa_z"]])
    return pd.concat(out, ignore_index=True)


def preseason_offset(T: pd.DataFrame, Rcarry: pd.DataFrame, P: pd.DataFrame | None) -> pd.Series:
    """Per-row preseason expectation: the carry model's week-1 (pre-season) intercept, home edge and
    pooled-FCS ratings, with FBS teams' off/def replaced by the prior table P (if given)."""
    first = Rcarry.groupby(Rcarry["block"] // 100)["block"].transform("min") == Rcarry["block"]
    R0 = Rcarry[first].assign(season=lambda d: d["block"] // 100)
    lab_o, lab_d = ratings._labels(T, "pool")
    key = R0.set_index(["season", "team"])
    o = key.reindex(pd.MultiIndex.from_arrays([T["season"], lab_o]))
    d = key.reindex(pd.MultiIndex.from_arrays([T["season"], lab_d]))
    mu = R0.groupby("season")["intercept"].first().reindex(T["season"]).to_numpy()
    h = R0.groupby("season")["home_adv"].first().reindex(T["season"]).to_numpy()
    po = o["off_rating"].to_numpy()
    pdf = d["def_rating"].to_numpy()
    if P is not None:
        pk = P.set_index(["season", "team_id"])
        qo = pk.reindex(pd.MultiIndex.from_arrays([T["season"], T["team_id"]]))["prior_off"].to_numpy()
        qd = pk.reindex(pd.MultiIndex.from_arrays([T["season"], T["opp_id"]]))["prior_def"].to_numpy()
        po = np.where((T["team_div"] == "fbs").to_numpy() & np.isfinite(qo), qo, po)
        pdf = np.where((T["opp_div"] == "fbs").to_numpy() & np.isfinite(qd), qd, pdf)
    return pd.Series(mu + np.nan_to_num(po) + np.nan_to_num(pdf) + h * T["site"].to_numpy(), index=T.index)


def ridge_with_prior(T: pd.DataFrame, y: str, offset: pd.Series, seasons, alpha: float,
                     prefix: str = "Dp") -> pd.DataFrame:
    """Current-season-only ridge on (y - preseason expectation): the season's evidence is shrunk toward
    the prior (not toward league average). Week 1 (no fit yet) falls back to the prior itself."""
    X = T.copy()
    X["_offset"] = offset.to_numpy()
    R = ratings.walkforward(X, y, seasons, alpha=alpha, decay=0.0, history=0, fcs_mode="pool",
                            offset_col="_offset", use_prior_seasons=False)
    Xa = ratings.attach(X, R, prefix)
    Xa[f"{prefix}_pred"] = Xa["_offset"] + Xa[f"{prefix}_pred"].fillna(0.0)
    return Xa
