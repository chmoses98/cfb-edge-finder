"""Downstream validation of the schedule-adjusted defensive rating against the script engine's rating.

    python -m cfb.defense_validate [--frame PATH] [--se PATH] [--stages repro,downstream,conference,reliability]

Writes results/defense_ratings_validation.json. Rules (same as the study): hyper-parameters and the conference-offset
decision use DEVELOPMENT (2016-2019) only; validation (2021-22), holdout (2023-24) and 2025 are reported for the frozen
choice; comparisons are paired and game-clustered (pf_core.metrics.cluster_bootstrap_diff, negative = first is better).

The headline test is DOWNSTREAM, not rank correlation: how well does each rating predict a team's NEXT game points (equiv.
the opposing defence's points allowed)? The script engine's rating is replayed with its own production code
(`adjust.fit_league`, season-to-date ridge, lambda = 4 games, points per game) in cfb/defense_diag.py.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time

import numpy as np
import pandas as pd
from scipy import stats

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pf_core import metrics as pmx  # noqa: E402

from cfb import build, data, defense_diag, paths  # noqa: E402
from cfb import defense_ratings as dr  # noqa: E402

SPLITS = {"development": (2016, 2019), "validation": (2021, 2022), "holdout": (2023, 2024), "holdout_2025": (2025, 2025)}
OUT = paths.RESULTS / "defense_ratings_validation.json"
WEEK_BUCKETS = {"all": (1, 99), "wk1": (1, 1), "wk2-4": (2, 4), "wk5-8": (5, 8), "wk9+": (9, 99)}


def log(*a):
    print(f"[{time.time() - T0:6.0f}s]", *a, flush=True)


T0 = time.time()


def _cmp(a_err, b_err, games):
    r = pmx.cluster_bootstrap_diff(a_err, b_err, games, n_boot=2000, seed=0)
    return {"mean_diff": round(r["mean_diff"], 4), "ci95": [round(x, 4) for x in r["ci95"]], "n": r["n_obs"], "n_games": r["n_clusters"]}


def _split_mask(X, name):
    a, b = SPLITS[name]
    return X["season"].between(a, b)


# --------------------------------------------------------------------------- B. downstream proof
def attach_se_components(X: pd.DataFrame, SE: pd.DataFrame) -> pd.DataFrame:
    """Script-engine adjusted off(team), def(opp), mu and h for each row of the walk-forward frame."""
    o = SE[SE["unit"] == "offense"].set_index(["season", "week", "team_id"])
    d = SE[SE["unit"] == "defense"].set_index(["season", "week", "team_id"])
    io = pd.MultiIndex.from_arrays([X["season"], X["week"], X["team_id"]])
    idf = pd.MultiIndex.from_arrays([X["season"], X["week"], X["opp_id"]])
    oo, dd = o.reindex(io), d.reindex(idf)
    X = X.copy()
    X["se_off"], X["se_def"] = oo["adjusted"].to_numpy(), dd["adjusted"].to_numpy()
    X["se_mu"], X["se_h"] = oo["mu"].to_numpy(), oo["h"].to_numpy()
    return X


def downstream(X: pd.DataFrame, SE: pd.DataFrame, new_col: str = "D_pts_pred") -> dict:
    """`new_col` = the new rating's prediction column; the defence-only swaps use the frozen D defence term (they are
    informative for the frozen rating only and are skipped for other columns)."""
    X = attach_se_components(X, SE)
    X = X[~X["post"] & X["pts"].notna() & X[new_col].notna() & X["se_pred"].notna()].copy()
    # new rating = frozen D. Defence-only swaps isolate the defensive term:
    #   new_with_se_def : D prediction with the opponent's D defence term replaced by the engine's (def - mu)
    #   se_with_new_def : engine prediction with the opponent's engine defence term replaced by D's (mu + def)
    X["new_with_se_def"] = np.maximum(X["D_pts_pred"] - X["D_pts_def"] + (X["se_def"] - X["se_mu"]), 0)
    X["se_with_new_def"] = np.maximum(X["se_off"] + (X["D_pts_mu"] + X["D_pts_def"]) - X["se_mu"] + X["se_h"] * X["site"], 0)
    X["new_pred"] = X[new_col]
    out = {}
    pops = {"FBS_vs_FBS": X["fbs_game"], "any_FBS_involved": pd.Series(True, index=X.index)}
    for split in SPLITS:
        for pop, pm_ in pops.items():
            for wb, (w0, w1) in WEEK_BUCKETS.items():
                m = _split_mask(X, split) & pm_ & X["week"].between(w0, w1)
                g = X[m]
                if len(g) < 50:
                    continue
                e = lambda c: (g[c] - g["pts"]).to_numpy()  # noqa: E731
                row = {"n_team_games": int(len(g)), "n_games": int(g["game_id"].nunique())}
                for name, col in (("new", "new_pred"), ("script_engine", "se_pred")):
                    row[f"{name}_mae"] = round(float(np.abs(e(col)).mean()), 3)
                    row[f"{name}_rmse"] = round(float(np.sqrt((e(col) ** 2).mean())), 3)
                    row[f"{name}_bias"] = round(float(e(col).mean()), 3)
                row["new_minus_se_mae"] = _cmp(np.abs(e("new_pred")), np.abs(e("se_pred")), g["game_id"])
                row["new_minus_se_sqerr"] = _cmp(e("new_pred") ** 2, e("se_pred") ** 2, g["game_id"])
                if new_col == "D_pts_pred":
                    row["defence_only"] = {
                        "new_def_vs_se_def_inside_new_model": _cmp(np.abs(e("new_pred")), np.abs(e("new_with_se_def")), g["game_id"]),
                        "new_def_vs_se_def_inside_se_model": _cmp(np.abs(e("se_with_new_def")), np.abs(e("se_pred")), g["game_id"])}
                out[f"{split}|{pop}|{wb}"] = row
    return out


# --------------------------------------------------------------------------- C. conference offsets (development decision)
def _conf_map(T: pd.DataFrame, season: int) -> dict:
    d = T[(T["season"] == season) & (T["team_div"] == "fbs")].drop_duplicates("team_id", keep="last")
    return dict(zip(d["team_id"].astype(str), d["team_conf"].astype(str)))


def walkforward_pred(T: pd.DataFrame, seasons, cfg: dr.DefenseConfig, use_conf: bool) -> pd.Series:
    """Per-row prediction from the module's fit at each row's block (PIT). Index = T.index."""
    pred = pd.Series(np.nan, index=T.index)
    for s in seasons:
        conf_of = _conf_map(T, s) if use_conf else None
        sub = T[(T["season"] == s) & ~T["post"] & T["pts"].notna()]
        for b in sorted(sub["block"].unique()):
            f = dr.fit(T, s, int(b), cfg, conf_of=conf_of, light=True)
            rows = sub[sub["block"] == b]
            pred.loc[rows.index] = f.predict(rows)
    return pred


def conference_experiment(T: pd.DataFrame, X: pd.DataFrame, base_cfg: dr.DefenseConfig) -> dict:
    grid = [(a_t, a_c) for a_t in (0.5, 2.0, 5.0) for a_c in (0.25, 1.0, 4.0)]
    dev_seasons = list(range(2016, 2020))
    Tn = T.copy()
    Tn["_fbs"] = Tn["team_div"].eq("fbs") & Tn["opp_div"].eq("fbs")
    log("conference experiment: baseline (module) on development")
    base = walkforward_pred(Tn, dev_seasons, base_cfg, False)
    dev = Tn["season"].isin(dev_seasons) & ~Tn["post"] & Tn["pts"].notna() & base.notna()
    pop = dev & Tn["_fbs"]
    mae = lambda p, m: float((p[m] - Tn.loc[m, "pts"]).abs().mean())  # noqa: E731
    res = {"baseline_dev_mae_fbs_vs_fbs": round(mae(base, pop), 4), "grid": []}
    best = None
    for a_t, a_c in grid:
        cfg = dr.DefenseConfig(**{**base_cfg.__dict__, "alpha": a_t, "conf_alpha": a_c})
        p = walkforward_pred(Tn, dev_seasons, cfg, True)
        m = pop & p.notna()
        cmp_all = _cmp((p[m] - Tn.loc[m, "pts"]).abs(), (base[m] - Tn.loc[m, "pts"]).abs(), Tn.loc[m, "game_id"])
        early = m & Tn["week"].between(1, 4)
        cmp_early = _cmp((p[early] - Tn.loc[early, "pts"]).abs(), (base[early] - Tn.loc[early, "pts"]).abs(), Tn.loc[early, "game_id"])
        row = {"alpha_team": a_t, "alpha_conf": a_c, "mae": round(mae(p, m), 4), "vs_baseline_all_weeks": cmp_all,
               "vs_baseline_weeks_1_4": cmp_early}
        res["grid"].append(row)
        log(f"  alpha_team={a_t} alpha_conf={a_c}: dev MAE {row['mae']:.4f} vs base {res['baseline_dev_mae_fbs_vs_fbs']:.4f}  diff {cmp_all['mean_diff']:+.4f} {cmp_all['ci95']}")
        if best is None or row["mae"] < best["mae"]:
            best = row
    res["best_on_development"] = best
    lo, hi = best["vs_baseline_all_weeks"]["ci95"]
    res["decision_rule"] = "adopt only if the best development variant beats the module baseline with a game-clustered 95% CI below 0 (all weeks, FBS-vs-FBS)"
    adopted = bool(hi < 0)
    res["adopted"] = adopted
    # confirmation on validation/holdout/2025 (reported either way; a worsening there vetoes adoption)
    conf_cfg = dr.DefenseConfig(**{**base_cfg.__dict__, "alpha": best["alpha_team"], "conf_alpha": best["alpha_conf"]})
    later = [2021, 2022, 2023, 2024, 2025]
    log("conference experiment: confirmation seasons (baseline vs best variant)")
    b2 = walkforward_pred(Tn, later, base_cfg, False)
    p2 = walkforward_pred(Tn, later, conf_cfg, True)
    conf = {}
    for split in ("validation", "holdout", "holdout_2025"):
        a, b = SPLITS[split]
        m = Tn["season"].between(a, b) & ~Tn["post"] & Tn["pts"].notna() & b2.notna() & p2.notna() & Tn["_fbs"]
        conf[split] = _cmp((p2[m] - Tn.loc[m, "pts"]).abs(), (b2[m] - Tn.loc[m, "pts"]).abs(), Tn.loc[m, "game_id"])
    res["confirmation_best_variant_vs_baseline"] = conf
    if adopted and any(v["ci95"][0] > 0 for v in conf.values()):
        res["adopted"], res["veto"] = False, "significantly worse on a later split"
    res["conf_alpha"] = best["alpha_conf"]
    res["alpha_team_with_conf"] = best["alpha_team"]
    return res


# --------------------------------------------------------------------------- D. reliability + band calibration
def reliability(T: pd.DataFrame, SE: pd.DataFrame, cfg: dr.DefenseConfig, weeks=(2, 3, 4, 6, 8), inflations=(1.0, 1.5, 2.0, 3.0, 4.0),
                use_conf: bool = False):
    rows, cover = [], []
    for s in range(2016, 2026):
        Ts = T[(T["season"] == s) & ~T["post"]]
        for w in weeks:
            truth = defense_diag.future_truth(T, s, w)
            if truth.empty:
                continue
            truth = truth[truth["team_id"] != "FCS"].set_index("team_id")
            f = dr.fit(T, s, s * 100 + w, cfg, conf_of=_conf_map(T, s) if use_conf else None)
            fr = f.frame().set_index("team_id")
            fr = fr[fr.index != dr.POOL]
            ids = [t for t in fr.index if t in truth.index]
            if len(ids) < 40:
                continue
            se_r = SE[(SE.season == s) & (SE.week == w) & (SE.unit == "defense")].set_index("team_id")
            row = {"season": s, "week": w, "n": len(ids),
                   "rho_new": float(stats.spearmanr(fr.loc[ids, "def_rating"], truth.loc[ids, "def_rating"]).statistic),
                   "components": int(f.components["n_components"]), "median_games": f.median_fbs_games}
            sid = [t for t in ids if t in se_r.index and pd.notna(se_r.loc[t, "adjusted"])]
            row["rho_se"] = float(stats.spearmanr(se_r.loc[sid, "adjusted"], truth.loc[sid, "def_rating"]).statistic) if len(sid) > 40 else np.nan
            top_t = set(truth.loc[ids].nsmallest(10, "def_rating").index)
            row["top10_new"] = len(top_t & set(fr.loc[ids].nsmallest(10, "def_rating").index))
            row["top10_se"] = len(top_t & set(se_r.loc[sid].nsmallest(10, "adjusted").index)) if len(sid) > 40 else np.nan
            rows.append(row)
            # calibration of the rating SE against an independent rest-of-season fit (which has its own SE)
            sub = T[(T["season"] == s) & ~T["post"] & (T["week"] >= w) & T["pts"].notna()]
            tcfg = dr.DefenseConfig(alpha=2.0, decay=0.0, history=0)
            ft = dr.fit(sub, s, s * 100 + 99, tcfg, light=True)
            tf = ft.frame().set_index("team_id")
            tid = [t for t in ids if t in tf.index]
            ca = dr.centered(f, tid).set_index("team_id")
            cb = dr.centered(ft, tid).set_index("team_id")
            for infl in inflations:
                z = (ca["rating_c"] - cb["rating_c"]) / np.sqrt(infl * ca["se_c"] ** 2 + cb["se_c"] ** 2)
                cover.append({"season": s, "week": w, "inflation": infl, "cover80": float((z.abs() < 1.2816).mean()), "n": len(tid)})
            # rank band vs the rest-of-season point rank (descriptive: the truth rank is itself noisy)
            band = dr.rank_band(f, ids)
            tr = truth.loc[ids, "def_rating"].rank(method="first")
            band = band.set_index("team_id")
            cover.append({"season": s, "week": w, "inflation": "rank_band", "cover80":
                          float(((tr.loc[band.index] >= band["rank_lo"]) & (tr.loc[band.index] <= band["rank_hi"])).mean()), "n": len(ids)})
    R = pd.DataFrame(rows)
    C = pd.DataFrame(cover)
    out = {"rank_reliability_by_week": {}}
    for split, (a, b) in {**SPLITS}.items():
        r = R[R["season"].between(a, b)]
        if r.empty:
            continue
        g = r.groupby("week")[["rho_new", "rho_se", "top10_new", "top10_se", "components", "median_games"]].mean().round(3)
        out["rank_reliability_by_week"][split] = g.reset_index().to_dict("records")
    cal = {}
    for split, (a, b) in SPLITS.items():
        c = C[C["season"].between(a, b)]
        if c.empty:
            continue
        cal[split] = {str(k): round(float(v), 3) for k, v in c.groupby("inflation", sort=False)["cover80"].mean().items()}
    out["rating_interval_coverage_vs_rest_of_season"] = cal
    dev = pd.Series(cal["development"])
    cand = [float(k) for k in dev.index if k != "rank_band" and dev[k] >= 0.80]
    out["var_inflation"] = min(cand) if cand else 4.0
    out["_note"] = ("coverage = share of teams whose week-w rating lies within the 80% interval of the independent "
                    "rest-of-season rating (z = diff / sqrt(inflation*se_w^2 + se_truth^2)); the inflation is the smallest "
                    "factor reaching 80% on DEVELOPMENT; validation/holdout show the same factor out of sample. 'rank_band' is "
                    "the share of teams whose rest-of-season rank falls inside the published rank band (inflation 1): "
                    "descriptive, because the truth rank is itself noisy.")
    return out, R


# --------------------------------------------------------------------------- A. reproduction
def reproduce(T: pd.DataFrame, X: pd.DataFrame, cfg: dr.DefenseConfig) -> dict:
    diffs = []
    for s, w in ((2017, 1), (2019, 5), (2022, 3), (2023, 9), (2024, 6), (2025, 12)):
        blk = s * 100 + w
        rows = X[(X["block"] == blk) & X["D_pts_pred"].notna() & ~X["post"]]
        if rows.empty:
            continue
        f = dr.fit(T, s, blk, cfg, light=True)
        p = f.predict(rows)
        diffs.append({"season": s, "week": w, "n": int(len(rows)), "max_abs_diff_points": float(np.abs(p - rows["D_pts_pred"].to_numpy()).max())})
    return {"blocks": diffs, "max_abs_diff_points": max(d["max_abs_diff_points"] for d in diffs) if diffs else None}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--frame", default=str(paths.BUILT / "team_frame.parquet"))
    ap.add_argument("--se", default=str(paths.BUILT / "se_replay.parquet"))
    ap.add_argument("--stages", default="repro,downstream,conference,reliability")
    ap.add_argument("--out", default=str(OUT))
    a = ap.parse_args()
    stages = a.stages.split(",")
    summ = json.load(open(paths.RESULTS / "summary.json"))["frozen_model"]
    cfg = dr.DefenseConfig(alpha=summ["alphas"]["pts"], decay=summ["decay"], history=summ["history"])
    G, T = data.load()
    T = build.add_derived(T)
    X = pd.read_parquet(a.frame)
    SE = pd.read_parquet(a.se)
    prev = json.load(open(a.out)) if os.path.exists(a.out) else {}
    out = {"schema": "sift.defense_ratings_validation.v1", "authority": "research_only",
           "config": {"alpha": cfg.alpha, "decay": cfg.decay, "history": cfg.history, "fcs_mode": cfg.fcs_mode},
           "target": "team points in the NEXT game (= points allowed by the opposing defence), one row per team-game, "
                     "regular season, game-clustered paired bootstrap; negative diff = new rating better",
           **{k: v for k, v in prev.items() if k not in ("schema", "authority", "config", "target")}}
    if "repro" in stages:
        out["reproduction"] = reproduce(T, X, cfg)
        log("reproduction", out["reproduction"]["max_abs_diff_points"])
    conf = out.get("conference_offsets")
    if "conference" in stages:
        conf = out["conference_offsets"] = conference_experiment(T, X, cfg)
    pub_cfg, use_conf = cfg, False
    if conf and conf.get("adopted"):
        pub_cfg = dr.DefenseConfig(**{**cfg.__dict__, "alpha": conf["alpha_team_with_conf"], "conf_alpha": conf["conf_alpha"]})
        use_conf = True
    out["published_rating"] = {"alpha": pub_cfg.alpha, "conf_alpha": pub_cfg.conf_alpha, "decay": pub_cfg.decay, "history": pub_cfg.history,
                               "fcs_mode": pub_cfg.fcs_mode, "conference_offsets": use_conf}
    if "downstream" in stages:
        out["downstream_frozen_D"] = downstream(X, SE)
        if use_conf:
            log("walk-forward predictions of the published (conference-offset) rating, 2016-2025")
            pred = walkforward_pred(T, list(range(2016, 2026)), pub_cfg, True)
            key = pd.Series(pred.to_numpy(), index=pd.MultiIndex.from_arrays([T["game_id"].astype(str), T["team_id"].astype(str)]))
            key = key[~key.index.duplicated()]
            X = X.copy()
            X["pub_pred"] = key.reindex(pd.MultiIndex.from_arrays([X["game_id"].astype(str), X["team_id"].astype(str)])).to_numpy()
            out["downstream"] = downstream(X, SE, "pub_pred")
        else:
            out["downstream"] = out["downstream_frozen_D"]
        for k in ("validation|FBS_vs_FBS|all", "holdout|FBS_vs_FBS|all", "holdout_2025|FBS_vs_FBS|all"):
            r = out["downstream"][k]
            log(k, "new MAE", r["new_mae"], "SE MAE", r["script_engine_mae"], r["new_minus_se_mae"])
    if "reliability" in stages:
        rel, R = reliability(T, SE, pub_cfg, use_conf=use_conf)
        out["rank_reliability"] = rel["rank_reliability_by_week"]
        out["rank_band"] = {"var_inflation": rel["var_inflation"], "coverage": rel["rating_interval_coverage_vs_rest_of_season"],
                            "note": rel["_note"]}
        if use_conf:
            rel0, _ = reliability(T, SE, cfg, use_conf=False)
            out["rank_reliability_frozen_D"] = rel0["rank_reliability_by_week"]
    os.makedirs(os.path.dirname(a.out), exist_ok=True)
    json.dump(out, open(a.out, "w"), indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o))
    log("wrote", a.out)


if __name__ == "__main__":
    main()
