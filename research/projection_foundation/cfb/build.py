"""Stage 1: the walk-forward team-game frame (every model's point prediction, PIT) + incumbents + market.

Cached at .cache/built/team_frame.parquet; rebuilt when inputs change (ratings are cached per config).
"""
from __future__ import annotations

import gzip
import json
import time

import numpy as np
import pandas as pd

from . import data, defense_diag, paths, ratings
from . import team_models as tm

SEASONS = list(range(2015, 2027))
# per-target ridge alpha (games of pull toward league average), chosen on development (see tune_alphas)
DEFAULT_ALPHA = 5.0
DECAY = 0.5
HISTORY = 3

RIDGE_TARGETS = ["pts", "plays", "ppp", "pass_att", "pass_yds", "rush_att", "rush_yds", "pass_cmp", "ypa", "ypc",
                 "ypc_rec", "epa", "sr", "epa_ng", "drives"]
RAW_COLS = ["pts", "plays", "ppp", "pass_att", "pass_yds", "rush_att", "rush_yds", "ypa", "ypc", "epa", "sr",
            "pass_cmp", "pass_yds_per_pass_att", "rush_yds_per_rush_att", "pts_per_plays"]


def add_derived(T: pd.DataFrame) -> pd.DataFrame:
    T = T.copy()
    T["ypa"] = T["pass_yds"] / T["pass_att"].where(T["pass_att"] >= 5)
    T["ypc"] = T["rush_yds"] / T["rush_att"].where(T["rush_att"] >= 5)
    T["ypc_rec"] = T["pass_yds"] / T["pass_cmp"].where(T["pass_cmp"] >= 3)
    T["pass_yds_per_pass_att"] = T["ypa"]
    T["rush_yds_per_rush_att"] = T["ypc"]
    T["pts_per_plays"] = T["ppp"]
    T["ppd"] = T["pts"] / T["drives"].where(T["drives"] >= 5)
    T.loc[T["drives"] < 5, "drives"] = np.nan
    return T


def tune_alphas(T: pd.DataFrame, targets, alphas=(2.5, 5.0, 10.0, 20.0), seasons=(2018, 2019)) -> dict:
    """Per-target alpha on development seasons only (decay/history fixed from the points grid)."""
    out = {}
    for y in targets:
        best = None
        rows = []
        for a in alphas:
            X = tm.ridge_pred(T, y, list(seasons), "tmp", alpha=a, decay=DECAY, history=HISTORY, fcs_mode="pool")
            m = X["season"].isin(seasons) & ~X["post"] & X["fbs_game"] & X[y].notna() & X["tmp_pred"].notna()
            mae = float((X.loc[m, "tmp_pred"] - X.loc[m, y]).abs().mean())
            rows.append({"alpha": a, "mae": mae})
            if best is None or mae < best[1]:
                best = (a, mae)
        out[y] = {"alpha": best[0], "grid": rows}
    return out


def build(force: bool = False, alphas: dict | None = None, log=print) -> pd.DataFrame:
    fp = paths.BUILT / "team_frame.parquet"
    G, T = data.load()
    T = add_derived(T)
    stamp = {"n": len(T), "pts": float(T["pts"].sum()), "alphas": alphas, "v": 4}
    sp = paths.BUILT / "team_frame.stamp.json"
    if fp.exists() and sp.exists() and not force and json.load(open(sp)) == stamp:
        return pd.read_parquet(fp)
    t0 = time.time()
    X = tm.raw_features(T, RAW_COLS)
    alphas = alphas or {}
    for y in RIDGE_TARGETS:
        a = alphas.get(y, DEFAULT_ALPHA)
        R = ratings.walkforward(T, y, SEASONS, alpha=a, decay=DECAY, history=HISTORY, fcs_mode="pool")
        X = ratings.attach(X, R, f"D_{y}")
        log(f"ridge {y} alpha={a} done {time.time() - t0:.0f}s")
    # FCS-individual comparator (points) for the FCS/SOS experiments
    R = ratings.walkforward(T, "pts", SEASONS, alpha=alphas.get("pts", DEFAULT_ALPHA), decay=DECAY,
                            history=HISTORY, fcs_mode="individual")
    X = ratings.attach(X, R, "Di_pts", fcs_mode="individual")
    # current-season-only ridge (no carry) to isolate the value of prior-season information
    R = ratings.walkforward(T, "pts", SEASONS, alpha=alphas.get("pts", DEFAULT_ALPHA), decay=0.0, history=0,
                            fcs_mode="pool", use_prior_seasons=False)
    X = ratings.attach(X, R, "Dcs_pts")
    # script-engine replay (production code) -> its scoring baseline and defensive ratings
    SE = defense_diag.replay_script_engine(T, SEASONS)
    SE.to_parquet(paths.BUILT / "se_replay.parquet")
    X["se_pred"] = defense_diag.se_team_points(X, SE).to_numpy()
    log(f"script-engine replay done {time.time() - t0:.0f}s")
    # incumbents: retired production engine (B1 raw expected points) and archived v2 research predictions
    sc = pd.DataFrame([json.loads(x) for x in gzip.open(paths.REPAIR / "source_contract_2014_2025.jsonl.gz")])
    sc["game_id"] = sc["game_id"].astype(str)
    X = X.merge(G[["game_id", "home_id", "mkt_home_pts", "mkt_away_pts", "close_spread", "close_total"]],
                on="game_id", how="left")
    X = X.merge(sc[["game_id", "sc_eh", "sc_ea"]], on="game_id", how="left")
    home = X["team_id"] == X["home_id"]
    X["inc_prod_pred"] = np.where(home, X["sc_eh"], X["sc_ea"])
    X["mkt_pred"] = np.where(home, X["mkt_home_pts"], X["mkt_away_pts"])
    X = X.drop(columns=["sc_eh", "sc_ea", "mkt_home_pts", "mkt_away_pts"])
    X.to_parquet(fp)
    json.dump(stamp, open(sp, "w"))
    log(f"team frame built {X.shape} in {time.time() - t0:.0f}s")
    return X


def v2_research_preds() -> pd.DataFrame:
    """Archived out-of-sample predictions of the 2026 'v2' research models (margin ensemble, total ridge)."""
    m = pd.read_parquet(paths.V2_RESEARCH / "preds" / "ens_margin_d025_eq.parquet")[["game_id", "pred_margin"]]
    t = pd.read_parquet(paths.V2_RESEARCH / "preds" / "tot_eff_ridge_d025_affine.parquet")[["game_id", "pred_total"]]
    out = m.merge(t, on="game_id", how="outer")
    out["game_id"] = out["game_id"].astype(str)
    return out.rename(columns={"pred_margin": "v2_margin", "pred_total": "v2_total"})
