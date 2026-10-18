"""Stage 2: ladder predictions, distributions, selection and scoring for every team/game family."""
from __future__ import annotations

import numpy as np
import pandas as pd

from pf_core import models as pm

from . import evaluate as ev
from . import joint
from . import team_models as tm

DEV_FIT = (2016, 2019)          # E warm-up uses 2015
STACK_FIT = (2017, 2019)

C_SPEC = {"pts": ("plays", "pts_per_plays", "D_plays", "D_ppp"),
          "pass_yds": ("pass_att", "pass_yds_per_pass_att", "D_pass_att", "D_ypa"),
          "rush_yds": ("rush_att", "rush_yds_per_rush_att", "D_rush_att", "D_ypc")}

E_BASE = ["site", "week", "neutral", "conf_game", "vs_fcs", "is_fcs", "post"]


def e_features(X: pd.DataFrame, y: str) -> list[str]:
    feats = list(E_BASE)
    for t in ("pts", "plays", "ppp", "epa", "sr", "pass_yds", "rush_yds", "pass_att", "rush_att", y):
        for part in ("off", "def"):
            c = f"D_{t}_{part}"
            if c in X and c not in feats:
                feats.append(c)
        if f"D_{t}_mu" in X and f"D_{t}_mu" not in feats:
            feats.append(f"D_{t}_mu")
    for c in (f"o_{y}_ewm4", f"a_{y}_ewm4", f"o_{y}_std", f"a_{y}_std", f"o_{y}_std_n", f"o_{y}_prev",
              "o_pts_ewm4", "a_pts_ewm4", "D_pts_n_off", "D_pts_n_def"):
        if c in X and c not in feats:
            feats.append(c)
    return feats


def ladder(X: pd.DataFrame, y: str, log=print) -> dict[str, pd.Series]:
    k_off = tm.estimate_k(X, y, "team_id")
    k_def = tm.estimate_k(X, y, "opp_id")
    preds = {"A_season_avg": tm.model_A(X, y, k_off), "B_ewma_rawopp": tm.model_B(X, y, k_off, k_def)}
    if y in C_SPEC:
        opp, rate, dopp, drate = C_SPEC[y]
        k = {opp: tm.estimate_k(X, opp), rate: tm.estimate_k(X, rate)}
        preds["C_opp_x_eff"] = tm.model_C(X, opp, y, k)
        preds["D_C_ridge_opp_x_eff"] = X[f"{dopp}_pred"] * X[f"{drate}_pred"]
    preds["D_ridge_sos"] = X[f"D_{y}_pred"]
    feats = e_features(X, y)
    E = pm.lgbm_walkforward(X.assign(_blk=X["season"]), feats, y, "_blk", objective="regression",
                            min_train_blocks=1, params=dict(num_leaves=15, n_estimators=400, learning_rate=0.03,
                                                            min_child_samples=200, n_jobs=2))
    preds["E_lgbm"] = E
    log(f"  ladder {y}: k_off={k_off:.2f} k_def={k_def:.2f} E feats={len(feats)}")
    return preds


def stack(X: pd.DataFrame, y: str, preds: dict) -> tuple[pd.Series, dict]:
    names = [n for n in preds if n.startswith(("B_", "C_", "D_", "E_"))]
    P = np.column_stack([preds[n].to_numpy(float) for n in names])
    m = (X["season"].between(*STACK_FIT) & ~X["post"] & X["fbs_game"] & X[y].notna()).to_numpy() & np.isfinite(P).all(1)
    w = pm.fit_stack_weights(X.loc[m, y].to_numpy(float), P[m])
    out = pd.Series(np.where(np.isfinite(P).all(1), P @ w, np.nan), index=X.index)
    return out, dict(zip(names, map(float, w)))


def population(X: pd.DataFrame, y: str, fbs_only: bool = True) -> pd.Series:
    m = X[y].notna() & ~X["post"] & X["season"].between(2016, 2026)
    if fbs_only:
        m &= X["fbs_game"]
    return m


def choose_dist_kind(X, y, mu, fam) -> tuple[str, dict]:
    _, scale, lo, K, _ = ev.FAMILIES[fam]
    d = X["season"].between(*DEV_FIT) & population(X, y) & mu.notna()
    yy, mm = X.loc[d, y].to_numpy() * scale, mu[d].to_numpy() * scale
    kinds = ["normal", "empirical"] + (["nb"] if fam in ("team_points", "plays") else [])
    res = {}
    for kd in kinds:
        p = ev.fit_dist(kd, yy, mm, lo, K)
        res[kd] = float(ev.make(p, mm).crps(np.rint(yy)).mean() / scale)
    # in-sample on development for every kind (same fit/eval data), used only to pick the family shape
    return min(res, key=res.get), res


def score_family(X: pd.DataFrame, fam: str, preds: dict, kind: str, incumbents: dict, market: str | None,
                 baseline: str = "A_season_avg"):
    """Returns (rows, selected, holdout, per-row frame). Selection on validation CRPS among ladder models."""
    y, scale, lo, K, unit = ev.FAMILIES[fam]
    pop = population(X, y)
    params = {}
    allp = dict(preds)
    allp.update(incumbents)
    if market:
        allp["market_close"] = X[market]
    for n, p in allp.items():
        d = X["season"].between(*DEV_FIT) & pop & p.notna()
        if d.sum() < 200:
            d = X["season"].between(2016, 2020) & pop & p.notna()
        params[n] = ev.fit_dist(kind, X.loc[d, y].to_numpy() * scale, p[d].to_numpy() * scale, lo, K)
    rows, losses = [], {}

    def run(n, split, mask):
        p = allp[n]
        m = mask & p.notna()
        if m.sum() < 30:
            return None
        dist = ev.make(params[n], p[m].to_numpy() * scale)
        r = ev.score(X.loc[m, y], p[m], dist, X.loc[m, "game_id"], scale)
        losses[(n, split)] = pd.Series(ev.crps_vec(dist, X.loc[m, y], scale), index=X.index[m])
        r.update({"model": n, "split": split})
        return r

    ladder_names = [n for n in preds]
    for split in ("development", "validation"):
        a, b = ev.SPLITS[split]
        mask = pop & X["season"].between(max(a, 2016), b)
        for n in list(allp):
            r = run(n, split, mask)
            if r:
                rows.append(r)
    val = {r["model"]: r["crps"] for r in rows if r["split"] == "validation" and r["model"] in ladder_names}
    best = min(val, key=val.get)
    # holdout splits: frozen winner + baseline + incumbents + market only (evaluated once)
    hold_models = [best, baseline] + list(incumbents) + (["market_close"] if market else [])
    for split in ("holdout", "holdout_2025", "live_2026"):
        a, b = ev.SPLITS[split]
        mask = pop & X["season"].between(a, b)
        for n in dict.fromkeys(hold_models):
            r = run(n, split, mask)
            if r:
                rows.append(r)
    # paired comparisons (CRPS, game-clustered)
    first_inc = next(iter(incumbents), None)
    for r in rows:
        key = (r["model"], r["split"])
        la = losses[key]
        for tag, other in (("vs_baseline", baseline), ("vs_incumbent", first_inc), ("vs_best", best), ("vs_market", "market_close")):
            if other is None or (other, r["split"]) not in losses or other == r["model"]:
                r[tag] = None
                continue
            lb = losses[(other, r["split"])]
            idx = la.index.intersection(lb.index)
            r[tag] = ev.compare(la[idx].to_numpy(), lb[idx].to_numpy(), X.loc[idx, "game_id"].to_numpy())
    for r in rows:
        if r["model"] == best:
            r["verdict"] = "best"
        elif r["model"] == baseline:
            r["verdict"] = "baseline"
        elif r["model"] in incumbents:
            r["verdict"] = "incumbent"
        elif r["model"] == "market_close":
            r["verdict"] = "market_benchmark"
        else:
            vb = r.get("vs_best")
            r["verdict"] = "competitive" if (vb and not ev.significant(vb)) else "rejected"
    return rows, best, params, losses


# ----------------------------------------------------------------------------- game families
def game_frame(X: pd.DataFrame, cols: dict) -> pd.DataFrame:
    """Home/away pairs for every game: cols maps model name -> team-row prediction column/Series."""
    h = X[X["team_id"] == X["home_id"]].set_index("game_id")
    a = X[X["team_id"] != X["home_id"]].set_index("game_id")
    gids = h.index.intersection(a.index)
    h, a = h.loc[gids], a.loc[gids]
    Gf = pd.DataFrame({"game_id": gids, "season": h["season"].to_numpy(), "week": h["week"].to_numpy(),
                       "post": h["post"].to_numpy(), "fbs_game": h["fbs_game"].to_numpy(),
                       "home_pts": h["pts"].to_numpy(), "away_pts": a["pts"].to_numpy(),
                       "close_spread": h["close_spread"].to_numpy(), "close_total": h["close_total"].to_numpy(),
                       "home_id": h["team_id"].to_numpy(), "away_id": a["team_id"].to_numpy(),
                       "kickoff": h["kickoff"].to_numpy(), "site": h["site"].to_numpy()})
    for n, s in cols.items():
        Gf[f"{n}__h"] = s.reindex(h["_rowid"]).to_numpy() if isinstance(s, pd.Series) else h[s].to_numpy()
        Gf[f"{n}__a"] = s.reindex(a["_rowid"]).to_numpy() if isinstance(s, pd.Series) else a[s].to_numpy()
    Gf["total"] = Gf["home_pts"] + Gf["away_pts"]
    Gf["margin"] = Gf["home_pts"] - Gf["away_pts"]
    return Gf


def fit_joint(Gf: pd.DataFrame, n: str, kind: str = "empirical"):
    d = Gf["season"].between(*DEV_FIT) & ~Gf["post"] & Gf["fbs_game"] & Gf[f"{n}__h"].notna() & Gf[f"{n}__a"].notna()
    if d.sum() < 300:
        d = Gf["season"].between(2016, 2020) & ~Gf["post"] & Gf["fbs_game"] & Gf[f"{n}__h"].notna() & Gf[f"{n}__a"].notna()
    return joint.JointModel(kind).fit(Gf.loc[d, f"{n}__h"].to_numpy(), Gf.loc[d, f"{n}__a"].to_numpy(),
                                      Gf.loc[d, "home_pts"].to_numpy(), Gf.loc[d, "away_pts"].to_numpy())
