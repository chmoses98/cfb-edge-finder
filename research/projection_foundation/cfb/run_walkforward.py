"""End-to-end CFB walk-forward backtest.

    python -m cfb.run_walkforward [--stages all|team,game,diag,gate,players,report] [--fetch]

(run from research/projection_foundation). Writes results/summary.json, results/experiments/*.json and
RESULTS_CFB.md. Development 2015/16-2019 (2020 reported separately as COVID-anomalous), validation 2021-2022,
holdout 2023-2024 (+2025 second holdout), live 2026 sanity (weeks played so far). Development fits, validation
selects, holdout is scored once for the frozen winner + baseline + incumbents + market.
"""
from __future__ import annotations

import argparse
import json
import os
import pickle
import sys
import time

import numpy as np
import pandas as pd

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from pf_core import metrics as pmx  # noqa: E402
from pf_core import registry  # noqa: E402

from cfb import build, data, defense_diag, inventory, joint, paths, players  # noqa: E402
from cfb import evaluate as ev  # noqa: E402
from cfb import gate_cfb  # noqa: E402
from cfb import team_eval as te  # noqa: E402
from cfb import team_models as tm  # noqa: E402

STATE = paths.BUILT / "stages.pkl"
EXP_DIR = paths.RESULTS / "experiments"
T0 = time.time()


def log(*a):
    print(f"[{time.time() - T0:7.0f}s]", *a, flush=True)


def load_state():
    return pickle.load(open(STATE, "rb")) if STATE.exists() else {}


def save_state(S):
    pickle.dump(S, open(STATE, "wb"))


def _clean(o):
    if isinstance(o, dict):
        return {str(k): _clean(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [_clean(v) for v in o]
    if isinstance(o, (np.floating, float)):
        return None if not np.isfinite(o) else round(float(o), 5)
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, np.bool_):
        return bool(o)
    return o


def record(S, exp_id, family, model, question, hypothesis, split, result: dict, verdict, method="", limitations=()):
    rec = registry.ExperimentRecord(
        experiment_id=exp_id, sport="CFB", family=family, model=model, model_version="pf-cfb-1.0.0",
        question=question, hypothesis=hypothesis, split=split, train_period="walk-forward (all games before each block)",
        eval_period=split, features=[], hyperparameters={}, data_provenance=["CFBD cache (research-data)", "ESPN 2026 log",
                                                                               "sportsdataverse player box/lines"],
        n_obs=int(result.get("n_obs", 0) or 0), n_independent=int(result.get("n_obs", 0) or 0),
        n_clusters=int(result.get("n_clusters", 0) or 0), point=_clean(result), verdict=verdict,
        limitations=list(limitations))
    registry.write_record(rec, str(EXP_DIR))


# ============================================================================ stage: team
def stage_team(S):
    G, T = data.load()
    T = build.add_derived(T)
    fp = paths.BUILT / "alphas.json"
    if fp.exists():
        alphas = json.load(open(fp))
    else:
        log("tuning per-target ridge alpha on development (2018-2019)")
        tuned = build.tune_alphas(T, build.RIDGE_TARGETS, alphas=(0.5, 1.0, 2.5, 5.0))
        alphas = {k: v["alpha"] for k, v in tuned.items()}
        json.dump({"alphas": alphas, "grid": tuned}, open(paths.BUILT / "alpha_grid.json", "w"), indent=1)
        json.dump(alphas, open(fp, "w"))
    S["alphas"] = alphas
    X = build.build(alphas=alphas, log=log)
    X["_rowid"] = X.index
    S["inventory"] = inventory.matrix(G, T, data.load_players())
    fams = {}
    preds_all = {}
    for fam, (y, *_r) in ev.FAMILIES.items():
        log(f"family {fam}")
        preds = te.ladder(X, y, log=log)
        preds["F_stack"], w = te.stack(X, y, preds)
        kind, kinds = te.choose_dist_kind(X, y, preds["D_ridge_sos"], fam)
        inc = {}
        if fam == "team_points":
            inc = {"incumbent_retired_engine": X["inc_prod_pred"], "incumbent_script_engine_baseline": X["se_pred"]}
        market = "mkt_pred" if fam == "team_points" else None
        rows, best, params, losses = te.score_family(X, fam, preds, kind, inc, market)
        fams[fam] = {"rows": rows, "best": best, "dist_kind": kind, "dist_kind_dev_crps": kinds, "stack_weights": w,
                     "target": y}
        preds_all[fam] = {**preds, **inc}
        if market:
            preds_all[fam]["market_close"] = X[market]
        log(f"  best={best} dist={kind} {kinds}")
    S["team_families"] = fams
    pickle.dump({"X": X, "preds": preds_all}, open(paths.BUILT / "team_preds.pkl", "wb"))
    save_state(S)


# ============================================================================ stage: experiments on team points
def _mae(X, m, col):
    e = (X.loc[m, col] - X.loc[m, "pts"]).abs()
    return float(e.mean()), int(m.sum())


def stage_experiments(S):
    TP = pickle.load(open(paths.BUILT / "team_preds.pkl", "rb"))
    X, P = TP["X"], TP["preds"]["team_points"]
    G, T = data.load()
    T = build.add_derived(T)
    X = X.copy()
    for n, s in P.items():
        X[f"m_{n}"] = s
    out = {}
    base = X["pts"].notna() & ~X["post"] & X["season"].between(2016, 2026)
    splits = {k: base & X["season"].between(*v) for k, v in ev.SPLITS.items() if k != "covid_2020"}
    splits["development"] = base & X["season"].between(2016, 2019)

    def paired(a, b, m):
        mm = m & X[a].notna() & X[b].notna()
        if mm.sum() < 30:
            return {"n_obs": int(mm.sum()), "mean_diff": None, "ci95": None, "note": "too few rows"}
        la, lb = (X.loc[mm, a] - X.loc[mm, "pts"]).abs(), (X.loc[mm, b] - X.loc[mm, "pts"]).abs()
        r = ev.compare(la.to_numpy(), lb.to_numpy(), X.loc[mm, "game_id"].to_numpy())
        r.update({"mae_a": float(la.mean()), "mae_b": float(lb.mean()), "n_obs": int(mm.sum())})
        return r

    # --- SOS: raw opponent-allowed (B) vs schedule-adjusted ridge (D); FCS pooled vs individual; carry vs none
    sos = []
    for split in ("development", "validation"):
        for pop, pm_ in (("FBS-vs-FBS", X["fbs_game"]), ("FBS-vs-FCS (FBS offence)", X["vs_fcs"]),
                         ("FCS offence vs FBS", X["is_fcs"] & (X["opp_div"] == "fbs"))):
            m = splits[split] & pm_
            for a, b, q in (("m_D_ridge_sos", "m_B_ewma_rawopp", "schedule-adjusted ridge vs raw opponent-allowed EWMA"),
                            ("D_pts_pred", "Di_pts_pred", "FCS pooled into one entity vs FCS teams rated individually"),
                            ("D_pts_pred", "Dcs_pts_pred", "prior seasons carried (decay) vs current season only"),
                            ("m_D_ridge_sos", "m_A_season_avg", "ridge vs own-offence average (no opponent)")):
                r = paired(a, b, m)
                r.update({"split": split, "population": pop, "comparison": q, "a": a, "b": b})
                sos.append(r)
    out["sos"] = sos
    # by week block (early-season uncertainty)
    wk = []
    for split in ("development", "validation"):
        for lo, hi in ((1, 1), (2, 4), (5, 8), (9, 20)):
            m = splits[split] & X["fbs_game"] & X["week"].between(lo, hi)
            for a, b in (("D_pts_pred", "Dcs_pts_pred"), ("m_D_ridge_sos", "m_B_ewma_rawopp"), ("m_D_ridge_sos", "mkt_pred"),
                         ("m_D_ridge_sos", "inc_prod_pred"), ("m_D_ridge_sos", "se_pred")):
                r = paired(a, b, m)
                r.update({"split": split, "weeks": f"{lo}-{hi}", "a": a, "b": b})
                wk.append(r)
    out["by_week"] = wk
    # --- preseason prior (talent / returning production) vs carry
    try:
        pre = data.preseason_inputs()
        from cfb import ratings
        Rc = build.ratings_split(T, "pts", alpha=S["alphas"].get("pts", build.DEFAULT_ALPHA), decay=build.DECAY,
                                 history=build.HISTORY, fcs_mode="pool")
        Pp = tm.preseason_prior(T, Rc, pre, "pts", list(range(2016, 2027)), alpha_cur=2.5)
        off_c = tm.preseason_offset(T, Rc, None)
        off_p = tm.preseason_offset(T, Rc, Pp)
        res = []
        for nm, off in (("carry_prior", off_c), ("talent_returning_prior", off_p)):
            for a in (2.5, 5.0, 10.0):
                Xa = tm.ridge_with_prior(T, "pts", off, list(range(2016, 2027)), alpha=a, prefix="Dp")
                key = f"Dp_{nm}_a{a}"
                X[key] = Xa["Dp_pred"].to_numpy()
                for split in ("development", "validation"):
                    for lo, hi in ((1, 1), (2, 4), (5, 20)):
                        m = splits[split] & X["fbs_game"] & X["week"].between(lo, hi)
                        r = paired(key, "D_pts_pred", m)
                        r.update({"model": key, "split": split, "weeks": f"{lo}-{hi}"})
                        res.append(r)
        out["preseason_prior"] = res
        X["prior_week1_talent"] = off_p.to_numpy()
        X["prior_week1_carry"] = off_c.to_numpy()
    except Exception as e:  # noqa: BLE001
        log("preseason prior experiment failed:", repr(e))
        out["preseason_prior_error"] = repr(e)
    # --- garbage time: EPA ratings with vs without garbage-time plays, predicting next games' EPA and points
    gt = []
    for split in ("development", "validation"):
        m = splits[split] & X["fbs_game"] & X["epa"].notna()
        for target in ("epa", "pts"):
            for a, b in (("D_epa_ng_pred", "D_epa_pred"),):
                mm = m & X[a].notna() & X[b].notna()
                # map EPA-scale predictions onto the target by a development-fitted linear map
                dev = splits["development"] & X["fbs_game"] & X[a].notna() & X[b].notna() & X[target].notna()
                ca = np.polyfit(X.loc[dev, a], X.loc[dev, target], 1)
                cb = np.polyfit(X.loc[dev, b], X.loc[dev, target], 1)
                pa, pb = np.polyval(ca, X.loc[mm, a]), np.polyval(cb, X.loc[mm, b])
                la, lb = np.abs(pa - X.loc[mm, target]), np.abs(pb - X.loc[mm, target])
                r = ev.compare(la.to_numpy(), lb.to_numpy(), X.loc[mm, "game_id"].to_numpy())
                r.update({"split": split, "target": target, "mae_no_garbage": float(la.mean()),
                          "mae_all_plays": float(lb.mean()), "n_obs": int(mm.sum())})
                gt.append(r)
    out["garbage_time"] = gt
    # --- FBS vs FCS, QB change, returning production (error investigations), on D
    inv = []
    m = splits["validation"] | splits["development"]
    for nm, mm in (("FBS-vs-FBS", m & X["fbs_game"]), ("FBS offence vs FCS defence", m & X["vs_fcs"]),
                   ("FCS offence vs FBS defence", m & X["is_fcs"] & (X["opp_div"] == "fbs"))):
        e = X.loc[mm, "m_D_ridge_sos"] - X.loc[mm, "pts"]
        inv.append({"segment": nm, "n": int(mm.sum()), "mae": float(e.abs().mean()), "bias": float(e.mean()),
                    "sd": float(e.std())})
    out["fcs_segments"] = inv
    # QB change (post-hoc label from player box: this game's leading passer != previous game's)
    try:
        Pl = data.load_players()
        lead = Pl[Pl["pass_att"] >= 8].sort_values("pass_att", ascending=False).drop_duplicates(["game_id", "team_id"])
        L = X[["game_id", "team_id", "season", "kickoff"]].merge(lead[["game_id", "team_id", "player_id"]], on=["game_id", "team_id"], how="left")
        L = L.sort_values(["team_id", "kickoff"])
        L["prev_qb"] = L.groupby(["team_id", "season"])["player_id"].shift(1)
        L["qb_change"] = L["player_id"].notna() & L["prev_qb"].notna() & (L["player_id"] != L["prev_qb"])
        X = X.merge(L[["game_id", "team_id", "qb_change"]], on=["game_id", "team_id"], how="left")
        X["qb_change"] = X["qb_change"].fillna(False)
        q = []
        for split in ("development", "validation"):
            mm = (base & X["season"].between(*({"development": (2016, 2019)}.get(split, ev.SPLITS[split])))) & X["fbs_game"] & (X["week"] > 1)
            for flag, g in X[mm].groupby("qb_change"):
                e = g["m_D_ridge_sos"] - g["pts"]
                em = g["mkt_pred"] - g["pts"]
                q.append({"split": split, "qb_change": bool(flag), "n": int(len(g)), "mae": float(e.abs().mean()),
                          "bias": float(e.mean()), "market_mae": float(em.abs().mean()), "market_bias": float(em.mean())})
        out["qb_change"] = q
    except Exception as e:  # noqa: BLE001
        out["qb_change_error"] = repr(e)
    # returning production tercile, weeks 1-4
    try:
        pre = data.preseason_inputs()
        Z = X.merge(pre[["season", "team_id", "ret_ppa"]], on=["season", "team_id"], how="left")
        mm = (splits["development"] | splits["validation"]) & Z["fbs_game"].to_numpy() & Z["week"].between(1, 4).to_numpy() & Z["ret_ppa"].notna().to_numpy()
        Zm = Z[mm.to_numpy()].copy()
        Zm["ret_tercile"] = pd.qcut(Zm["ret_ppa"], 3, labels=["low", "mid", "high"])
        rr = []
        for t_, g in Zm.groupby("ret_tercile", observed=True):
            e = g["m_D_ridge_sos"] - g["pts"]
            rr.append({"returning_production": str(t_), "n": int(len(g)), "mae": float(e.abs().mean()), "bias": float(e.mean())})
        out["returning_production"] = rr
    except Exception as e:  # noqa: BLE001
        out["returning_production_error"] = repr(e)
    S["experiments"] = out
    pickle.dump({"X": X, "preds": TP["preds"]}, open(paths.BUILT / "team_preds.pkl", "wb"))
    save_state(S)
    log("experiments done")


# ============================================================================ stage: game-level model G
def stage_gmodel(S):
    from cfb import game_model as gm
    TP = pickle.load(open(paths.BUILT / "team_preds.pkl", "rb"))
    X = TP["X"]
    F = gm.game_features(X)
    fbs = (F["home_fcs"] == 0) & (F["away_fcs"] == 0) & ~F["post"]
    grid = {}
    chosen = {}
    preds = {}
    for tgt in ("margin", "total"):
        best = None
        for a in (10.0, 100.0, 1000.0, 3000.0):
            p = gm.walkforward(F, tgt, a)
            m = fbs & F["season"].between(*te.DEV_FIT) & p.notna()
            mae = float((p[m] - F.loc[m, tgt]).abs().mean())
            grid[f"{tgt}_alpha_{a}"] = mae
            if best is None or mae < best[1]:
                best = (a, mae, p)
        chosen[tgt] = best[0]
        preds[tgt] = best[2]
    log(f"G alphas {chosen} grid {grid}")
    g = gm.team_means(F, preds["margin"], preds["total"], len(X))
    g.index = X.index
    P = TP["preds"]["team_points"]
    P["G_game_ridge"] = g
    fam = S["team_families"]["team_points"]
    inc = {"incumbent_retired_engine": X["inc_prod_pred"], "incumbent_script_engine_baseline": X["se_pred"]}
    ladder = {k: v for k, v in P.items() if k not in inc and k != "market_close"}
    rows, best, params, losses = te.score_family(X, "team_points", ladder, fam["dist_kind"], inc, "mkt_pred")
    fam.update({"rows": rows, "best": best, "g_model": {"alphas": chosen, "dev_grid": grid,
                                                          "note": "added after first scoring pass; selected on validation"}})
    S["g_alphas"] = chosen
    pickle.dump(TP, open(paths.BUILT / "team_preds.pkl", "wb"))
    save_state(S)
    log(f"team_points re-scored with G: best={best}")


# ============================================================================ stage: game families (joint)
def stage_game(S):
    TP = pickle.load(open(paths.BUILT / "team_preds.pkl", "rb"))
    X, P = TP["X"], TP["preds"]["team_points"]
    best = S["team_families"]["team_points"]["best"]
    cand = {n: P[n] for n in ("A_season_avg", "B_ewma_rawopp", "C_opp_x_eff", "D_ridge_sos", "D_C_ridge_opp_x_eff",
                              "E_lgbm", "F_stack", "G_game_ridge") if n in P}
    cand["incumbent_retired_engine"] = X["inc_prod_pred"]
    cand["incumbent_script_engine_baseline"] = X["se_pred"]
    cand["market_close"] = X["mkt_pred"]
    for n in list(cand):
        cand[n] = pd.Series(cand[n].to_numpy(), index=X["_rowid"].to_numpy())
    Gf = te.game_frame(X, cand)
    v2 = build.v2_research_preds()
    Gf = Gf.merge(v2, on="game_id", how="left")
    pop = ~Gf["post"] & Gf["fbs_game"] & Gf["season"].between(2016, 2026)
    res = {"rows": [], "kinds": {}, "threshold": [], "best": None}
    # 1) joint kind on development for the team-points best model
    kinds = {}
    dev = pop & Gf["season"].between(*te.DEV_FIT) & Gf[f"{best}__h"].notna()
    for kd in ("empirical", "bivariate_normal", "independent"):
        J = te.fit_joint(Gf, best, kd)
        D = J.distributions(Gf.loc[dev, f"{best}__h"].to_numpy(), Gf.loc[dev, f"{best}__a"].to_numpy())
        kinds[kd] = {"rho": J.rho, "crps_total": float(D["total"].crps(Gf.loc[dev, "total"]).mean()),
                     "crps_margin": float(D["margin"].crps(Gf.loc[dev, "margin"]).mean()),
                     "crps_home": float(D["home"].crps(Gf.loc[dev, "home_pts"]).mean())}
    res["kinds"] = kinds
    kind = min(kinds, key=lambda k: kinds[k]["crps_total"] + kinds[k]["crps_margin"])
    res["kind"] = kind
    log(f"joint kinds {kinds} -> {kind}")
    losses = {}

    def run(n, split, mask, direct=None):
        if direct is None:
            m = mask & Gf[f"{n}__h"].notna() & Gf[f"{n}__a"].notna()
        else:
            m = mask & Gf[direct].notna()
        if m.sum() < 30:
            return []
        out = []
        if direct is None:
            J = te.fit_joint(Gf, n, kind)
            D = J.distributions(Gf.loc[m, f"{n}__h"].to_numpy(), Gf.loc[m, f"{n}__a"].to_numpy())
            means = {"game_total": Gf.loc[m, f"{n}__h"] + Gf.loc[m, f"{n}__a"], "margin": Gf.loc[m, f"{n}__h"] - Gf.loc[m, f"{n}__a"]}
            dd = {"game_total": D["total"], "margin": D["margin"]}
        else:
            fam = "margin" if "margin" in direct else "game_total"
            yv = "margin" if fam == "margin" else "total"
            dmask = pop & Gf["season"].between(2016, 2020) & Gf[direct].notna()
            lo, K = (joint.MAR_LO, joint.MAR_K) if fam == "margin" else (0, joint.TOT_K)
            prm = ev.fit_dist("normal", Gf.loc[dmask, yv], Gf.loc[dmask, direct], lo, K)
            means = {fam: Gf.loc[m, direct]}
            dd = {fam: ev.make(prm, Gf.loc[m, direct].to_numpy())}
        for fam, dist in dd.items():
            yv = "margin" if fam == "margin" else "total"
            r = ev.score(Gf.loc[m, yv], means[fam], dist, Gf.loc[m, "game_id"])
            r.update({"model": n if direct is None else direct, "split": split, "family": fam})
            losses[(fam, r["model"], split)] = pd.Series(dist.crps(Gf.loc[m, yv].to_numpy()), index=Gf.index[m])
            # threshold calibration vs the closing line (line used only as the threshold)
            if fam == "margin":
                s = Gf.loc[m, "close_spread"]
                ok = s.notna() & (Gf.loc[m, "margin"] != -s)
                thr = np.floor(-s.fillna(0)).to_numpy() + 1     # home covers iff margin >= floor(-spread)+1
                pc = dist.prob_ge(thr)
                o = (Gf.loc[m, "margin"] > -s).astype(float)
                pc, o = pc[ok.to_numpy()], o[ok].to_numpy()
            else:
                ln = Gf.loc[m, "close_total"]
                ok = ln.notna() & (Gf.loc[m, "total"] != ln)
                pc = dist.prob_ge(np.floor(ln.fillna(0)).to_numpy() + 1)
                o = (Gf.loc[m, "total"] > ln).astype(float)
                pc, o = pc[ok.to_numpy()], o[ok].to_numpy()
            if len(o) > 30:
                r["line_brier"] = float(np.mean((pc - o) ** 2))
                r["line_ece"] = pmx.ece(pc, o)
                r["line_hit_rate_when_p_gt_0.6"] = float(o[pc > 0.6].mean()) if (pc > 0.6).sum() > 20 else None
                r["line_n_p_gt_0.6"] = int((pc > 0.6).sum())
                if n == best and direct is None:
                    res["threshold"].append({"family": fam, "split": split, "calibration": pmx.calibration_table(pc, o, 10)})
            out.append(r)
        return out

    names = list(cand)
    for split in ("development", "validation", "holdout", "holdout_2025", "live_2026"):
        a, b = ev.SPLITS[split]
        mask = pop & Gf["season"].between(max(a, 2016), b)
        hold = split not in ("development", "validation")
        for n in names:
            if hold and n not in (best, "F_stack", "A_season_avg", "incumbent_retired_engine", "incumbent_script_engine_baseline", "market_close"):
                continue
            res["rows"] += run(n, split, mask)
        for direct in ("v2_margin", "v2_total"):
            res["rows"] += run(direct, split, mask, direct=direct)
    # paired comparisons
    for r in res["rows"]:
        la = losses[(r["family"], r["model"], r["split"])]
        for tag, other in (("vs_baseline", "A_season_avg"), ("vs_incumbent", "incumbent_retired_engine"),
                           ("vs_market", "market_close"), ("vs_best", best),
                           ("vs_v2_research", "v2_margin" if r["family"] == "margin" else "v2_total")):
            key = (r["family"], other, r["split"])
            if key not in losses or other == r["model"]:
                r[tag] = None
                continue
            lb = losses[key]
            idx = la.index.intersection(lb.index)
            r[tag] = ev.compare(la[idx].to_numpy(), lb[idx].to_numpy(), Gf.loc[idx, "game_id"].to_numpy())
            # MAE difference too (means)
    res["best"] = best
    S["game_families"] = res
    Gf.to_parquet(paths.BUILT / "game_frame.parquet")
    save_state(S)
    log("game families done")


# ============================================================================ stage: defensive-ranking diagnosis
def stage_diag(S):
    G, T = data.load()
    T = build.add_derived(T)
    SE = pd.read_parquet(paths.BUILT / "se_replay.parquet")
    from cfb import ratings
    Rc = build.ratings_split(T, "pts", alpha=S["alphas"].get("pts", build.DEFAULT_ALPHA), decay=build.DECAY,
                             history=build.HISTORY, fcs_mode="pool")
    seasons = list(range(2016, 2026))
    rel = defense_diag.rank_reliability(T, SE, Rc, seasons)
    TP = pickle.load(open(paths.BUILT / "team_preds.pkl", "rb"))
    X = TP["X"]
    Xi = X.copy()
    fc = defense_diag.fcs_credit(T, SE, Xi, X, seasons)
    pc = defense_diag.pace_confound(T, SE, seasons)
    # the 2026 snapshot through the production code at three cutoffs vs the ridge with carry
    snaps = []
    SE26 = SE[SE.season == 2026]
    names = T.drop_duplicates("team_id").set_index("team_id")["team"].to_dict()
    for w in sorted(SE26["week"].unique()):
        se = SE26[(SE26.week == w) & (SE26.unit == "defense") & SE26["rank"].notna()].sort_values("rank")
        dc = Rc[Rc["block"] == 2026 * 100 + w]
        dc = dc[dc["team"] != "FCS"].sort_values("def_rating")
        snaps.append({"week": int(w), "stable": bool(se["stable"].iloc[0]) if len(se) else None,
                      "components": int(se["components"].iloc[0]) if len(se) else None,
                      "script_engine_top10": [f"{names.get(t, t)} ({g}g, raw {r:.1f})" for t, g, r in
                                              se.head(10)[["team_id", "games", "raw"]].itertuples(index=False)],
                      "ridge_carry_top10": [names.get(t, t) for t in dc.head(10)["team"]]})
    S["diag"] = {"reliability": rel.to_dict("records"), "fcs_credit": fc.to_dict("records"),
                 "pace": pc.to_dict("records"), "snapshots_2026": snaps}
    save_state(S)
    log("diag done")


# ============================================================================ stage: gate
def stage_gate(S):
    TP = pickle.load(open(paths.BUILT / "team_preds.pkl", "rb"))
    X = TP["X"]
    best = S["team_families"]["team_points"]["best"]
    Gf = pd.read_parquet(paths.BUILT / "game_frame.parquet")
    kind = S["game_families"]["kind"]
    J = te.fit_joint(Gf, best, kind)
    pop = ~Gf["post"] & Gf["season"].between(2021, 2025) & Gf[f"{best}__h"].notna() & Gf[f"{best}__a"].notna()
    g = Gf[pop].reset_index(drop=True)
    D = J.distributions(g[f"{best}__h"].to_numpy(), g[f"{best}__a"].to_numpy())
    Xi = X.set_index(["game_id", "team_id"])
    cur_off = X.groupby(["season", "team_id"]).cumcount()
    X = X.assign(cur_games=cur_off.to_numpy())
    cg = X.set_index(["game_id", "team_id"])["cur_games"]
    sd_def = X[X["fbs_game"]].groupby("season")["D_pts_def"].std().to_dict()
    Xs = X.sort_values("kickoff")
    histmap = {}
    for tid, grp in Xs.groupby("team_id"):
        vals = grp["pts"].tolist()
        for k, gid in enumerate(grp["game_id"].tolist()):
            histmap[(gid, tid)] = vals[max(0, k - 12):k]
    out = []
    for i, r in g.iterrows():
        ko = pd.Timestamp(r["kickoff"])
        asof = (ko - pd.Timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
        now = (ko - pd.Timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
        for side, oth, dkey in (("home_id", "away_id", "home"), ("away_id", "home_id", "away")):
            t, o = r[side], r[oth]
            row = Xi.loc[(r["game_id"], t)]
            mu = float((g.loc[i, f"{best}__h"] if dkey == "home" else g.loc[i, f"{best}__a"]))
            dist = D[dkey]
            res = gate_cfb.team_gate(
                team_id=t, team=row["team"], game_id=r["game_id"], start_utc=ko.strftime("%Y-%m-%dT%H:%M:%SZ"),
                mean=float(dist.mean[i]), dist_row_lo=0, dist_row_probs=dist.P[i], thresholds=None,
                sd=float(np.sqrt(dist.var[i])), week=int(r["week"]), cur_games_team=int(cg.get((r["game_id"], t), 0)),
                cur_games_opp=int(cg.get((r["game_id"], o), 0)), opp_fcs=row["opp_div"] != "fbs",
                team_fcs=row["team_div"] != "fbs", opp_def_rating=row.get("D_pts_def"), league_mu=row.get("D_pts_mu"),
                stat_history=[v for v in histmap.get((r["game_id"], t), []) if v == v], data_asof_utc=asof, now_utc=now,
                game_total_mean=float(D["total"].mean[i]), team_sum_mean=float(D["home"].mean[i] + D["away"].mean[i]),
                opp_def_sd=sd_def.get(int(r["season"])))
            y = r["home_pts"] if dkey == "home" else r["away_pts"]
            out.append({"season": int(r["season"]), "week": int(r["week"]), "status": res["status"], "abs_err": abs(mu - y),
                        "checks": [f["check"] + ":" + f["severity"] for f in res["findings"]]})
    Z = pd.DataFrame(out)
    summ = {"n": int(len(Z)), "status_counts": Z["status"].value_counts().to_dict()}
    from collections import Counter
    summ["check_counts"] = dict(Counter(c for cs in Z["checks"] for c in cs).most_common())
    ebs = {}
    for sp, (a, b) in (("validation", (2021, 2022)), ("holdout", (2023, 2025))):
        zz = Z[Z.season.between(a, b)]
        for ck in ("early_opponent:REVIEW", "fcs:REVIEW", "confidence:REVIEW"):
            has = zz["checks"].apply(lambda c: ck in c)
            if has.any() and (~has).any():
                ebs.setdefault("by_check", {}).setdefault(sp, {})[ck] = {
                    "n_flagged": int(has.sum()), "mae_flagged": float(zz.loc[has, "abs_err"].mean()),
                    "mae_unflagged": float(zz.loc[~has, "abs_err"].mean())}
        ebs[sp] = {st: {"n": int(len(gg)), "mae": float(gg["abs_err"].mean()),
                        "rate": float(len(gg) / len(zz))} for st, gg in zz.groupby("status")}
        if {"PASS", "REVIEW"} <= set(zz["status"]):
            ebs[sp]["review_minus_pass_mae"] = float(zz[zz.status == "REVIEW"]["abs_err"].mean() - zz[zz.status == "PASS"]["abs_err"].mean())
    S["gate"] = {"summary": summ, "error_by_status": ebs}
    save_state(S)
    log("gate done", summ["status_counts"])


# ============================================================================ stage: players
def stage_players(S):
    TP = pickle.load(open(paths.BUILT / "team_preds.pkl", "rb"))
    X = TP["X"]
    G, T = data.load()
    T = build.add_derived(T)
    Pl = data.load_players()
    covered = set(Pl["game_id"])
    U = players.build_panel(Pl, T)
    U = players.team_shares(U, T)
    U = U[U["team_div"] == "fbs"]
    log("player features ...")
    Df = players.player_features(U)
    Df = players.attach_team(Df, X)
    Df["covered"] = Df["game_id"].isin(covered)
    res = {}
    keep = {}
    for fam, (role, opp, stat, *_r) in players.FAMILIES.items():
        log(f"player family {fam}")
        preds = players.ladder(Df, fam, log=log)
        cand = Df[role]
        pop = cand & Df["covered"] & ~Df["post"] & Df["season"].between(2016, 2026)
        if role == "passer":
            pop &= Df["has_pass"]
        names = list(preds)
        Pm = np.column_stack([preds[n].to_numpy(float) for n in names if not n.startswith("A_")])
        sm = (pop & Df["season"].between(2017, 2019)).to_numpy() & np.isfinite(Pm).all(1)
        from pf_core import models as pmod
        w = pmod.fit_stack_weights(Df.loc[sm, stat].to_numpy(float), Pm[sm])
        preds["F_stack"] = pd.Series(np.where(np.isfinite(Pm).all(1), Pm @ w, np.nan), index=Df.index)
        count = stat in ("pass_att", "carries", "receptions")
        lo, K = (0, 81) if count else (-30, 631)
        kinds = {}
        d0 = pop & Df["season"].between(2016, 2019) & preds["D_ridge_x_share_x_eff_x_opp" if not count else "D_ridge_team_x_share"].notna()
        refm = preds["D_ridge_x_share_x_eff_x_opp" if not count else "D_ridge_team_x_share"]
        for kd in (["nb", "normal", "empirical"] if count else ["normal", "empirical"]):
            prm = ev.fit_dist(kd, Df.loc[d0, stat], refm[d0], lo, K)
            kinds[kd] = float(ev.make(prm, refm[d0].to_numpy()).crps(Df.loc[d0, stat].to_numpy()).mean())
        kind = min(kinds, key=kinds.get)
        rows, losses = [], {}
        params = {}
        for n, p in preds.items():
            d = pop & Df["season"].between(2016, 2019) & p.notna()
            params[n] = ev.fit_dist(kind, Df.loc[d, stat], p[d], lo, K)

        def run(n, split, mask):
            m = mask & preds[n].notna()
            if m.sum() < 50:
                return None
            dist = ev.make(params[n], preds[n][m].to_numpy())
            r = ev.score(Df.loc[m, stat], preds[n][m], dist, Df.loc[m, "game_id"])
            losses[(n, split)] = pd.Series(dist.crps(Df.loc[m, stat].to_numpy()), index=Df.index[m])
            r.update({"model": n, "split": split})
            return r

        for split in ("development", "validation"):
            a, b = ev.SPLITS[split]
            for n in preds:
                r = run(n, split, pop & Df["season"].between(max(a, 2016), b))
                if r:
                    rows.append(r)
        val = {r["model"]: r["crps"] for r in rows if r["split"] == "validation"}
        best = min(val, key=val.get)
        for split in ("holdout", "holdout_2025", "live_2026"):
            a, b = ev.SPLITS[split]
            for n in dict.fromkeys([best, "A_season_avg"]):
                r = run(n, split, pop & Df["season"].between(a, b))
                if r:
                    rows.append(r)
        for r in rows:
            la = losses[(r["model"], r["split"])]
            for tag, other in (("vs_baseline", "A_season_avg"), ("vs_best", best)):
                if (other, r["split"]) not in losses or other == r["model"]:
                    r[tag] = None
                    continue
                lb = losses[(other, r["split"])]
                idx = la.index.intersection(lb.index)
                r[tag] = ev.compare(la[idx].to_numpy(), lb[idx].to_numpy(), Df.loc[idx, "game_id"].to_numpy())
            r["vs_incumbent"] = None
            if r["model"] == best:
                r["verdict"] = "best"
            elif r["model"] == "A_season_avg":
                r["verdict"] = "baseline"
            else:
                r["verdict"] = "competitive" if (r.get("vs_best") and not ev.significant(r["vs_best"])) else "rejected"
        extra = {}
        if role == "passer":
            allc = cand & Df["covered"] & ~Df["post"] & Df["season"].between(2016, 2025)
            extra["non_appearance_rate"] = float(1 - Df.loc[allc, "has_pass"].mean())
        res[fam] = {"rows": rows, "best": best, "dist_kind": kind, "dist_kind_dev_crps": kinds,
                    "stack_weights": dict(zip([n for n in names if not n.startswith("A_")], map(float, w))), **extra}
        keep[fam] = preds[best]
        log(f"  best={best} kind={kind}")
    S["players"] = res
    Df[["game_id", "team_id", "player_id", "name", "season", "week", "passer", "rusher", "receiver"]].assign(
        **{f"pred_{k}": v for k, v in keep.items()}).to_parquet(paths.BUILT / "player_preds.parquet")
    save_state(S)


# ============================================================================ stage: script-engine v2 ranges
def stage_script_engine(S):
    import glob
    import gzip
    G, T = data.load()
    res = {}
    rows = []
    for f in sorted(glob.glob(str(paths.REPO_ROOT / "data/scripting/live/frozen_v2/*.json.gz"))):
        c = json.load(gzip.open(f))["content"]
        ctl = (c.get("claims") or {}).get("control")
        if not ctl:
            continue
        hr = ctl["historical_range"]
        rows.append({"game_id": str(c["event_id"]), "side": ctl["side"], "tier": hr["tier"], "c50": hr["central_50"],
                     "c80": hr["central_80"], "win_rate": hr["win_rate"]["rate"], "median": hr["median"]})
    R = pd.DataFrame(rows)
    if len(R):
        R = R.merge(G[["game_id", "margin"]], on="game_id", how="inner")
        R["ctl_margin"] = np.where(R["side"] == "home", R["margin"], -R["margin"])
        R["in50"] = [a[0] <= m <= a[1] for a, m in zip(R["c50"], R["ctl_margin"])]
        R["in80"] = [a[0] <= m <= a[1] for a, m in zip(R["c80"], R["ctl_margin"])]
        R["won"] = R["ctl_margin"] > 0
        res = {"n_games": int(len(R)), "coverage_50": float(R["in50"].mean()), "coverage_80": float(R["in80"].mean()),
               "mean_stated_win_rate": float(R["win_rate"].mean()), "realised_win_rate": float(R["won"].mean()),
               "by_tier": R.groupby("tier").agg(n=("won", "size"), stated=("win_rate", "mean"), realised=("won", "mean"),
                                                cov50=("in50", "mean"), cov80=("in80", "mean")).reset_index().to_dict("records")}
        # our joint model on the same games
        try:
            Gf = pd.read_parquet(paths.BUILT / "game_frame.parquet")
            best = S["team_families"]["team_points"]["best"]
            J = te.fit_joint(Gf, best, S["game_families"]["kind"])
            g = Gf.merge(R[["game_id", "side"]], on="game_id")
            g = g[g[f"{best}__h"].notna()]
            D = J.distributions(g[f"{best}__h"].to_numpy(), g[f"{best}__a"].to_numpy())
            lo, hi = D["margin"].interval(0.8)
            p_home = D["margin"].prob_ge(np.ones(len(g)))
            res["pf_model_same_games"] = {"n": int(len(g)), "cover_80_range": float(np.mean((g["margin"] >= lo) & (g["margin"] <= hi))),
                                          "winner_brier": float(np.mean((p_home - (g["margin"] > 0)) ** 2))}
        except Exception as e:  # noqa: BLE001
            res["pf_model_error"] = repr(e)
    S["script_engine_v2"] = res
    save_state(S)
    log("script engine v2 ranges", {k: v for k, v in res.items() if k != "by_tier"})


STAGES = {"team": stage_team, "experiments": stage_experiments, "gmodel": stage_gmodel, "game": stage_game, "diag": stage_diag,
          "gate": stage_gate, "players": stage_players, "script_engine": stage_script_engine}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--stages", default="all")
    ap.add_argument("--fetch", action="store_true")
    a = ap.parse_args()
    if a.fetch:
        paths.fetch_research_data()
        paths.fetch_sdv()
    S = load_state()
    todo = list(STAGES) if a.stages == "all" else a.stages.split(",")
    for st in todo:
        if st == "report":
            continue
        log(f"=== stage {st}")
        STAGES[st](S)
    if a.stages == "all" or "report" in todo:
        from cfb import report
        report.write(S, log=log)


if __name__ == "__main__":
    main()
