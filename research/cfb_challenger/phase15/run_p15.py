#!/usr/bin/env python3
"""Phase 15 CFB pipeline on top of #307 (research only).

    python run_p15.py feats      # walk-forward features 2016-2026 for the pre-declared grid (pregame only)
    python run_p15.py leak       # truncation test on real data, sampled dates (2025 + 2026)
    python run_p15.py tune       # FG: pick centre (lam_c, rho) on validation 2023-24; OOF sigma curves
    python run_p15.py freeze     # FG layer + sigma curves + period layers/shapes fitted on <= 2024, frozen
    python run_p15.py holdout    # score 2025 + 2026-to-date ONCE: FG vs production / #307, periods vs naive split
    python run_p15.py talent     # sensitivity: prior-season talent, no returning production (layer refit <= 2024)

WORK = $CFB_WORK (run_challenger.py layout + team_games.parquet); PERIODS = derived linescore parquet.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import os
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
sys.path.insert(0, str(HERE.parent))
import challenger  # noqa: E402,F401  (BLAS threads)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

import p15model as X  # noqa: E402
from challenger import model as M  # noqa: E402

sys.path.insert(0, os.environ.get("P15EVAL_DIR", "/home/user/sift-alpha/research/phase15"))
import p15eval as PE  # noqa: E402

WORK = Path(os.environ.get("CFB_WORK", "/home/user/p15work/cfb/work"))
PERIODS = Path(os.environ.get("CFB_PERIODS", "/home/user/p15work/cfb/derived/cfb_period_scores_2014_2026.parquet"))
KALSHI = Path(os.environ.get("CFB_KALSHI", "/home/user/p15work/kalshi/CFB/CFB"))
CFBD = WORK / "2014-2026"
FROZEN, RESULTS = HERE / "frozen_p15.json", HERE / "results_p15.json"
DEV, VAL, HOLD = [2016, 2017, 2018, 2019, 2021, 2022], [2023, 2024], [2025, 2026]
TRAIN = DEV + VAL
ALL = list(range(2016, 2027))
FZ307 = json.loads((HERE.parent / "frozen_params.json").read_text())
K307, ALPHA307 = FZ307["params"]["decay_k"], FZ307["params"]["layer_alpha"]
# pre-declared grid (lam_c = 0 is #307 exactly)
GRID = [(0.0, 0.0)] + list(itertools.product([3.0, 8.0], [0.5, 0.8]))
SEGS = {"q1": ("q1",), "q2": ("q2",), "q3": ("q3",), "q4": ("q4",), "h1": ("q1", "q2"), "h2": ("q3", "q4")}
SERIES = {  # family -> (Kalshi series, half-width of strike window around the model mean; None = full listed ladder)
    ("FG", "spread"): ("KXNCAAFSPREAD", 14), ("FG", "total"): ("KXNCAAFTOTAL", 14),
    ("FG", "team_total"): ("KXNCAAFTEAMTOTAL", 10),
    ("h1", "spread"): ("KXNCAAF1HSPREAD", 10), ("h1", "total"): ("KXNCAAF1HTOTAL", 10),
    ("h1", "team_total"): ("KXNCAAF1HTEAMTOTAL", 7),
    ("h2", "spread"): ("KXNCAAF2HSPREAD", 10), ("h2", "total"): ("KXNCAAF2HTOTAL", 10),
    **{(q, "spread"): (f"KXNCAAF{q[1]}QSPREAD", None) for q in ("q1", "q2", "q3", "q4")},
    **{(q, "total"): (f"KXNCAAF{q[1]}QTOTAL", None) for q in ("q1", "q2", "q3", "q4")},
}


def cfg_tag(lc, rho):
    return f"p15feat_{lc}_{rho}.parquet"


def load():
    tg = pd.read_parquet(WORK / "team_games.parquet")
    per = pd.read_parquet(PERIODS)
    return X.add_period_points(tg, per), M.games_table(tg), M.preseason_table(CFBD), per


def _feat_job(cfg):
    f = WORK / cfg_tag(*cfg)
    if not f.exists():
        tg, games, pre, _ = load()
        X.walk_forward(tg, games, pre, X.Params15(lam_c=cfg[0], rho=cfg[1], periods=True), ALL).to_parquet(f)
    return str(f)


def cmd_feats():
    with ProcessPoolExecutor(4) as ex:
        print(list(ex.map(_feat_job, GRID)))


def cmd_leak():
    """Rebuild features with every team-game on/after date D removed; features for D's games must be identical."""
    tg, games, pre, _ = load()
    rng = np.random.default_rng(15)
    out = {}
    for cfg in (GRID[0], GRID[-1]):          # #307 path (selected) and the centred path with period ratings
        full = pd.read_parquet(WORK / cfg_tag(*cfg)).set_index("game_id")
        p = X.Params15(lam_c=cfg[0], rho=cfg[1], periods=True)
        dates = []
        for s in HOLD:
            ds = sorted(games[games.season == s].date.unique())
            dates += [ds[0], ds[1]] + list(rng.choice(ds[2:], 3, replace=False))
        maxdiff, n = 0.0, 0
        for d in dates:
            s = int(games[games.date == d].season.iloc[0])
            tr = X.walk_forward(tg[tg.date < d], games, pre, p, [s], dates={d}).set_index("game_id")
            a, b = full.loc[tr.index, tr.columns].to_numpy(float), tr.to_numpy(float)
            maxdiff = max(maxdiff, float(np.nanmax(np.abs(a - b))) if a.size else 0.0)
            n += len(tr)
        out[str(cfg)] = {"dates": [str(d) for d in dates], "games": n, "max_abs_diff": maxdiff}
    print(json.dumps(out, indent=1))
    (WORK / "leak.json").write_text(json.dumps(out, indent=1))


# ---------------------------------------------------------------- FG
def fbs(games, F, seasons):
    g = games[games.season.isin(seasons) & (games.home_div == "fbs") & (games.away_div == "fbs")]
    return g.merge(F, on="game_id")


def ym(df):
    return (df.home_pts - df.away_pts).to_numpy(), (df.home_pts + df.away_pts).to_numpy()


def fit_layer(df):
    m, t = ym(df)
    return M.fit_layer(df, m, t, ALPHA307, K307)


def oof(df, seasons):
    """Leave-one-season-out layer predictions (honest residuals / inputs for the period layer)."""
    parts = []
    for s in seasons:
        c = fit_layer(df[df.season.isin([x for x in seasons if x != s])])
        te = df[df.season == s]
        parts.append(te[["game_id"]].merge(M.predict(c, te), on="game_id"))
    return pd.concat(parts)


def navg(df):
    return ((df.n_h + df.n_a) / 2).to_numpy(float)


def sigma_pack(df, pred):
    d = df.merge(pred, on="game_id")
    m, t = ym(d)
    rm, rt = m - d.ch_margin.to_numpy(), t - d.ch_total.to_numpy()
    n = navg(d)
    return {"margin": X.fit_sigma_curve(rm, n, K307), "total": X.fit_sigma_curve(rt, n, K307),
            "sd_margin_const": float(np.sqrt(np.mean(rm ** 2))), "sd_total_const": float(np.sqrt(np.mean(rt ** 2))),
            "rho_mt": float(np.corrcoef(rm, rt)[0, 1])}


def cmd_tune():
    _, games, _, _ = load()
    rows = []
    for cfg in GRID:
        F = pd.read_parquet(WORK / cfg_tag(*cfg))
        dev, val = fbs(games, F, DEV), fbs(games, F, VAL)
        pr = val.merge(M.predict(fit_layer(dev), val), on="game_id")
        m, t = ym(pr)
        early = navg(pr) < 3
        ae = np.abs(pr.ch_margin - m)
        rows.append({"lam_c": cfg[0], "rho": cfg[1], "val_margin_mae": float(ae.mean()),
                     "val_total_mae": float(np.abs(pr.ch_total - t).mean()),
                     "val_margin_mae_early": float(ae[early].mean()), "n_val": len(pr), "n_early": int(early.sum())})
    tab = pd.DataFrame(rows).sort_values("val_margin_mae")
    print(tab.to_string())
    best = tab.iloc[0]
    (WORK / "tune_p15.json").write_text(json.dumps({"grid": rows, "best": [best.lam_c, best.rho]}, indent=1))


# ---------------------------------------------------------------- periods
def kalshi_strikes(series, min_count=20):
    import collections
    c = collections.Counter()
    for tier in ("historical", "live"):
        f = KALSHI / f"{series}.{tier}.jsonl"
        if f.exists():
            for line in open(f):
                k = json.loads(line).get("floor_strike")
                if k is not None:
                    c[float(k)] += 1
    return np.array(sorted(k for k, v in c.items() if v >= min_count))


def team_rows(df, per_df, fg_pred, seg):
    """Stack home/away team-period rows: design + target (NaN if linescore invalid)."""
    d = df.merge(fg_pred, on="game_id").merge(per_df, on="game_id", how="left", suffixes=("", "_per"))
    out = []
    for side, o in (("h", "a"), ("a", "h")):
        ff = d.ch_home if side == "h" else d.ch_away
        fa = d.ch_away if side == "h" else d.ch_home
        y = sum(d[f"{side}_{q}"] for q in SEGS[seg])
        Xd = X.period_design(d, side, seg, ff.to_numpy(), fa.to_numpy())
        out.append(pd.DataFrame({"game_id": d.game_id, "side": side, "fg": ff.to_numpy(), "y": y.to_numpy(float),
                                 "season": d.season.to_numpy(), "valid": d.valid.fillna(False).to_numpy(bool)}).join(
            pd.DataFrame(Xd, columns=[f"x{i}" for i in range(Xd.shape[1])])))
    return pd.concat(out, ignore_index=True)


def fit_period(tr: pd.DataFrame) -> dict:
    tr = tr[tr.valid]
    Xc = [c for c in tr.columns if c.startswith("x")]
    coef = X.ridge_fit(tr[Xc].to_numpy(float), tr.y.to_numpy(float), 1.0)
    share = float(tr.y.sum() / tr.fg.sum())
    mu = X.ridge_apply(coef, tr[Xc].to_numpy(float))
    res = tr.y.to_numpy() - mu
    # residual sd as a linear function of the mean (football period points are over-dispersed and grow with mean)
    A = np.c_[np.ones(len(mu)), mu]
    sd_lin = np.linalg.lstsq(A, np.abs(res) * np.sqrt(np.pi / 2), rcond=None)[0]
    piv = tr.assign(mu=mu, r=res).pivot_table(index="game_id", columns="side", values="r")
    rho = float(np.corrcoef(piv.h, piv.a)[0, 1])
    return {"coef": coef, "share": share, "sd_lin": sd_lin.tolist(), "rho": rho,
            "mu_train": mu.tolist(), "y_train": tr.y.astype(int).tolist()}


def period_predict(fp, rows):
    Xc = [c for c in rows.columns if c.startswith("x")]
    return X.ridge_apply(fp["coef"], rows[Xc].to_numpy(float))


def tie_scale(fp, rows, mu, k=1500):
    """Observed ties / ties implied by independent neighbour PMFs, on the fitting rows."""
    nb = X.NeighbourPMF(np.array(fp["mu_train"]), np.array(fp["y_train"]), k)
    r = rows.assign(mu=mu)
    w = r.pivot_table(index="game_id", columns="side", values=["mu", "y"]).dropna()
    pt = np.mean([float(np.sum(nb.pmf(a) * nb.pmf(b))) for a, b in zip(w["mu"].h, w["mu"].a)])
    return float(np.mean(w["y"].h == w["y"].a) / pt)


def seg_pmfs(kind, fp, mh, ma, fg_ctx, nmax=70):
    """Joint PMFs for one game segment. kind: 'normal' | 'empirical' | 'naive' (proportional FG split)."""
    if kind == "naive":
        sh = fp["share"]
        mu_m, sd_m, mu_t, sd_t, rho_mt = fg_ctx
        return PE.pmf_from_margin_total(sh * mu_m, np.sqrt(sh) * sd_m, sh * mu_t, np.sqrt(sh) * sd_t, rho_mt, nmax)
    if kind == "normal":
        a, b = fp["sd_lin"]
        return PE.pmf_from_normal_pair(mh, ma, max(a + b * mh, 1.0), max(a + b * ma, 1.0), fp["rho"], nmax)
    nb = fp["_nb"]
    return PE.ScorePMF(X.joint_independent(nb.pmf(mh)[:nmax], nb.pmf(ma)[:nmax], fp["tie_scale"]))


# ---------------------------------------------------------------- ladders / scoring
def ladder(pm: PE.ScorePMF, h, a, fam, strikes, centre, half):
    """Rows (p, y, strike, side) at Kalshi strikes within `half` of the model centre (None = all listed)."""
    out = []
    if fam == "winner":
        out.append((pm.p_margin_gt(0.5), float(h > a), 0.0, "home"))
        out.append((float(pm.p[(pm._h - pm._a) < -0.5].sum()), float(a > h), 0.0, "away"))
        return out
    if fam == "tie":
        return [(pm.p_tie(), float(h == a), 0.0, "tie")]
    for k in strikes:
        if fam == "spread":
            if half is None or abs(k - centre[0]) <= half:
                out.append((pm.p_margin_gt(k), float(h - a > k), k, "home"))
            if half is None or abs(k + centre[0]) <= half:
                out.append((float(pm.p[(pm._h - pm._a) < -k].sum()), float(a - h > k), k, "away"))
        elif fam == "total":
            if half is None or abs(k - centre[1]) <= half:
                out.append((pm.p_total_gt(k), float(h + a > k), k, "total"))
        elif fam == "team_total":
            if half is None or abs(k - centre[2]) <= half:
                out.append((pm.p_home_gt(k), float(h > k), k, "home"))
            if half is None or abs(k - centre[3]) <= half:
                out.append((pm.p_away_gt(k), float(a > k), k, "away"))
    return out


def score_family(rows_new, rows_old):
    """rows_*: DataFrame(game_id, p, y) aligned row-for-row (same strikes). Paired, game-clustered."""
    yn, pn, po = rows_new.y.to_numpy(), np.clip(rows_new.p.to_numpy(), 1e-6, 1 - 1e-6), np.clip(rows_old.p.to_numpy(), 1e-6, 1 - 1e-6)
    g = rows_new.game_id.to_numpy()
    bn, bo = (pn - yn) ** 2, (po - yn) ** 2
    ln, lo = -(yn * np.log(pn) + (1 - yn) * np.log(1 - pn)), -(yn * np.log(po) + (1 - yn) * np.log(1 - po))
    out = {"n_rows": int(len(yn)), "n_games": int(len(set(g))),
           "brier": PE.cluster_ci(bn, g), "brier_base": PE.cluster_ci(bo, g), "d_brier": PE.paired_delta(bn, bo, g),
           "logloss": PE.cluster_ci(ln, g), "logloss_base": PE.cluster_ci(lo, g), "d_logloss": PE.paired_delta(ln, lo, g),
           "p_logloss": PE.boot_p(ln - lo, g, n_boot=2000),
           "slope": round(PE.calibration_slope(pn, yn), 3), "slope_base": round(PE.calibration_slope(po, yn), 3),
           "bias": round(float(np.mean(pn - yn)), 4), "bias_base": round(float(np.mean(po - yn)), 4),
           "reliability": [[round(x, 3) if isinstance(x, float) else x for x in r] for r in PE.reliability(pn, yn)]}
    return out


def orient_periods(games, per):
    """Period scores in the games-table orientation (neutral-site games may be listed either way)."""
    p = per.drop(columns=["season"]).merge(games[["game_id", "home", "away"]], on="game_id", suffixes=("_p", ""))
    flip = (p.home_p == p.away) & (p.away_p == p.home)
    keep = flip | ((p.home_p == p.home) & (p.away_p == p.away))
    p = p[keep].copy()
    for c in [c for c in p.columns if c.startswith("h_")]:
        a = "a_" + c[2:]
        p.loc[flip, [c, a]] = p.loc[flip, [a, c]].to_numpy()
    return p[["game_id", "valid"] + [c for c in p.columns if c.startswith(("h_", "a_"))]]


def production_rows(games):
    sys.path.insert(0, str(HERE.parent))
    from challenger import production as P
    ext = pd.read_parquet(WORK / "prod_2026_ext.parquet").merge(
        games[["game_id", "home_pts", "away_pts"]], on="game_id").rename(columns={"home_pts": "p_home_pts", "away_pts": "p_away_pts"})
    pr = pd.concat([P.load_hist(), P.load_archived_2026(), ext], ignore_index=True)
    pr = pr[["game_id", "prod_margin", "prod_total", "prod_sigma", "p_home_pts", "p_away_pts"]].drop_duplicates("game_id")
    g = games.merge(pr, on="game_id")
    same = (g.home_pts == g.p_home_pts) & (g.away_pts == g.p_away_pts)
    flip = (g.home_pts == g.p_away_pts) & (g.away_pts == g.p_home_pts) & ~same
    g = g[same | flip].copy()
    g.loc[flip, "prod_margin"] *= -1
    return g[["game_id", "prod_margin", "prod_total", "prod_sigma"]]


def fg_dist(df, pred, pack, curve=True):
    """Per-game (mu_m, sd_m, mu_t, sd_t, rho) from layer predictions and a sigma pack."""
    d = df[["game_id", "n_h", "n_a"]].merge(pred, on="game_id")
    n = navg(d)
    if curve:
        sm, st = X.sigma_of(pack["margin"], n), X.sigma_of(pack["total"], n)
    else:
        sm, st = np.full(len(d), pack["sd_margin_const"]), np.full(len(d), pack["sd_total_const"])
    return d.assign(sd_m=sm, sd_t=st, rho=pack["rho_mt"])


def cmd_freeze():
    tune = json.loads((WORK / "tune_p15.json").read_text())
    cfg = tuple(tune["best"])
    _, games, _, per = load()
    F, F0 = pd.read_parquet(WORK / cfg_tag(*cfg)), pd.read_parquet(WORK / cfg_tag(*GRID[0]))
    tr, tr0 = fbs(games, F, TRAIN), fbs(games, F0, TRAIN)
    layer = fit_layer(tr)
    o, o0 = oof(tr, TRAIN), oof(tr0, TRAIN)
    pack, pack0 = sigma_pack(tr, o), sigma_pack(tr0, o0)
    prod = tr0.merge(production_rows(games), on="game_id")
    pm, pt = ym(prod)
    rpm, rpt = pm - prod.prod_margin.to_numpy(), pt - prod.prod_total.to_numpy()
    prod_pack = {"sd_total_const": float(np.sqrt(np.mean(rpt ** 2))), "rho_mt": float(np.corrcoef(rpm, rpt)[0, 1])}
    # ---- periods: shape chosen on validation (fit DEV -> score VAL), then refit on TRAIN
    pero = orient_periods(games, per)
    allg = fbs(games, F, TRAIN)
    fgd = fg_dist(allg, o, pack)
    val_tab, period_frozen, nb_rows = {}, {}, []
    for seg in SEGS:
        rows = team_rows(allg, pero, o, seg)
        dev_rows, val_rows = rows[rows.season.isin(DEV)], rows[rows.season.isin(VAL)]
        fp = fit_period(dev_rows)
        fp["_nb"] = X.NeighbourPMF(np.array(fp["mu_train"]), np.array(fp["y_train"]))
        fp["tie_scale"] = tie_scale(fp, dev_rows[dev_rows.valid], period_predict(fp, dev_rows[dev_rows.valid]))
        vr = val_rows[val_rows.valid].assign(mu=period_predict(fp, val_rows[val_rows.valid]))
        val_tab[seg] = _score_shapes(vr, fp, fgd, seg, ("normal", "empirical", "naive"))
        best = min(("normal", "empirical"), key=lambda k: val_tab[seg][k]["logloss"])
        fpt = fit_period(rows)
        fpt["_nb"] = X.NeighbourPMF(np.array(fpt["mu_train"]), np.array(fpt["y_train"]))
        fpt["tie_scale"] = tie_scale(fpt, rows[rows.valid], period_predict(fpt, rows[rows.valid]))
        nb_rows.append(pd.DataFrame({"seg": seg, "mu": fpt["mu_train"], "y": fpt["y_train"]}))
        period_frozen[seg] = {k: v for k, v in fpt.items() if k not in ("_nb", "mu_train", "y_train")} | {"shape": best}
        print(seg, best, json.dumps(val_tab[seg]))
    nbf = HERE / "frozen_p15_period_nb.parquet"
    pd.concat(nb_rows).to_parquet(nbf)
    code = hashlib.sha256(b"".join((HERE / f).read_bytes() for f in ("p15model.py", "run_p15.py", "periods_data.py"))).hexdigest()
    FROZEN.write_text(json.dumps({
        "model": "cfb_oae_challenger_v2_p15", "fg": {"cfg": list(cfg), "layer": layer, "sigma": pack},
        "v307_sigma_pack": pack0, "prod_pack": prod_pack, "periods": period_frozen,
        "period_nb_sha256": hashlib.sha256(nbf.read_bytes()).hexdigest(), "period_validation": val_tab,
        "fg_tune": tune, "trained_on": TRAIN, "selected_on": VAL, "code_sha256": code}, indent=1, default=float))
    print("frozen", FROZEN)


def _ladder_frame(pmfs, outs, ctrs, fam, strikes, half, gids):
    rows = []
    for pm, (h, a), c, g in zip(pmfs, outs, ctrs, gids):
        for p, y, k, side in ladder(pm, h, a, fam, strikes, c, half):
            rows.append((g, p, y, k, side, c))
    return pd.DataFrame(rows, columns=["game_id", "p", "y", "k", "side", "ctr"])


def _period_pmfs(kind, fp, vr, fgd):
    w = vr.pivot_table(index="game_id", columns="side", values=["mu", "y"])
    w = w.dropna()
    ctx = fgd.set_index("game_id")
    pm, outs, ctrs, gids = [], [], [], []
    for gid, r in w.iterrows():
        mh, ma = float(r[("mu", "h")]), float(r[("mu", "a")])
        c = ctx.loc[gid]
        pm.append(seg_pmfs(kind, fp, mh, ma, (c.ch_margin, c.sd_m, c.ch_total, c.sd_t, c.rho)))
        outs.append((int(r[("y", "h")]), int(r[("y", "a")])))
        ctrs.append((mh - ma, mh + ma, mh, ma))
        gids.append(gid)
    return pm, outs, ctrs, gids


def _score_shapes(vr, fp, fgd, seg, kinds):
    out = {}
    pm0 = _period_pmfs("empirical", fp, vr, fgd)          # strike windows centred on the period-model mean for all
    for kind in kinds:
        pm, outs, ctrs, gids = _period_pmfs(kind, fp, vr, fgd)
        ctrs = pm0[2]
        fr = pd.concat([_ladder_frame(pm, outs, ctrs, fam, kalshi_strikes(SERIES[(seg, fam)][0]), SERIES[(seg, fam)][1], gids)
                        for fam in ("spread", "total")])
        p = np.clip(fr.p, 1e-6, 1 - 1e-6)
        out[kind] = {"logloss": float(np.mean(-(fr.y * np.log(p) + (1 - fr.y) * np.log(1 - p)))),
                     "brier": float(np.mean((p - fr.y) ** 2)), "n_rows": len(fr)}
    return out


def load_period_frozen(fz):
    nb = pd.read_parquet(HERE / "frozen_p15_period_nb.parquet")
    assert hashlib.sha256((HERE / "frozen_p15_period_nb.parquet").read_bytes()).hexdigest() == fz["period_nb_sha256"]
    out = {}
    for seg, fp in fz["periods"].items():
        q = nb[nb.seg == seg]
        out[seg] = dict(fp, _nb=X.NeighbourPMF(q.mu.to_numpy(), q.y.to_numpy()))
    return out


def fg_metrics(ev, dists: dict, base: str, ref: str):
    """Point/CRPS/winner metrics for each FG model, paired deltas of `base` vs `ref` models (game-clustered)."""
    from scipy.stats import norm
    m, t = ym(ev)
    g = ev.game_id.to_numpy()
    yw = (m > 0).astype(float)
    L = {}
    for k, d in dists.items():
        d = ev[["game_id"]].merge(d, on="game_id")
        p = np.clip(np.asarray(norm.sf((0.5 - d.mu_m) / d.sd_m), float), 1e-6, 1 - 1e-6)
        L[k] = {"margin_ae": np.abs(d.mu_m - m).to_numpy(), "total_ae": np.abs(d.mu_t - t).to_numpy(),
                "margin_crps": PE.crps_normal(d.mu_m.to_numpy(), d.sd_m.to_numpy(), m),
                "total_crps": PE.crps_normal(d.mu_t.to_numpy(), d.sd_t.to_numpy(), t),
                "win_brier": (p - yw) ** 2, "win_logloss": -(yw * np.log(p) + (1 - yw) * np.log(1 - p)), "_p": p}
    out = {"n_games": int(len(ev))}
    for k in L:
        out[k] = {mm: round(float(v.mean()), 4) for mm, v in L[k].items() if mm != "_p"}
        out[k]["win_slope"] = round(PE.calibration_slope(L[k]["_p"], yw), 3)
        out[k]["win_bias"] = round(float(np.mean(L[k]["_p"] - yw)), 4)
    for r in ref:
        out[f"{base}-{r}"] = {mm: PE.paired_delta(L[base][mm], L[r][mm], g) for mm in L[base] if mm != "_p"}
    return out


def fg_ladders(ev, dists, centre_key):
    """Kalshi-strike ladders (spread both sides, total, team totals, winner) for every FG model on identical rows."""
    ctr = dists[centre_key].set_index("game_id")
    res = {}
    for k, d in dists.items():
        d = d.set_index("game_id")
        pms, outs, ctrs, gids = [], [], [], []
        for r in ev.itertuples():
            q, c = d.loc[r.game_id], ctr.loc[r.game_id]
            pms.append(PE.pmf_from_margin_total(q.mu_m, q.sd_m, q.mu_t, q.sd_t, q.rho, 200))
            outs.append((int(r.home_pts), int(r.away_pts)))
            ctrs.append((c.mu_m, c.mu_t, (c.mu_t + c.mu_m) / 2, (c.mu_t - c.mu_m) / 2))
            gids.append(r.game_id)
        for fam in ("spread", "total", "team_total", "winner"):
            ser, half = SERIES.get(("FG", fam), (None, None))
            res[(k, fam)] = _ladder_frame(pms, outs, ctrs, fam, kalshi_strikes(ser) if ser else [], half, gids)
    return res


def verdict(sc, gain_key="d_logloss"):
    calibrated = 0.85 <= sc["slope"] <= 1.15 and abs(sc["bias"]) <= 0.02
    gain = sc[gain_key][1][1] < 0
    mild = 0.75 <= sc["slope"] <= 1.25 and abs(sc["bias"]) <= 0.03
    if sc["n_games"] < 150:
        return "ABSTAIN"
    if calibrated and gain:
        return "RELIABLE"
    if calibrated or (gain and mild):
        return "USABLE"
    return "ABSTAIN"


def abstain_bands(fr, ev_n):
    """Calibration by distance of strike from the model centre and by early/late season (bias = mean(p - y))."""
    out = []
    fr = fr.merge(ev_n, on="game_id")
    dist = np.where(fr.side == "away", np.abs(fr.k + fr.c0), np.abs(fr.k - fr.c0))
    fr = fr.assign(dist=dist, early=fr.navg < 3)
    for name, mask in (("strike within 3 of mean", fr.dist < 3), ("3-7 from mean", (fr.dist >= 3) & (fr.dist < 7)),
                       (">= 7 from mean", fr.dist >= 7), ("early season (<3 games)", fr.early), ("later", ~fr.early),
                       ("p < 0.1 or p > 0.9", (fr.p < 0.1) | (fr.p > 0.9))):
        q = fr[mask]
        if len(q) < 50:
            continue
        b = PE.cluster_ci((q.p - q.y).to_numpy(), q.game_id.to_numpy(), n_boot=1000)
        out.append({"band": name, "n_rows": int(len(q)), "bias": b, "slope": round(PE.calibration_slope(q.p, q.y), 3)})
    return out


def cmd_holdout():
    fz = json.loads(FROZEN.read_text())
    cfg = tuple(fz["fg"]["cfg"])
    _, games, _, per = load()
    F, F0 = pd.read_parquet(WORK / cfg_tag(*cfg)), pd.read_parquet(WORK / cfg_tag(*GRID[0]))
    prod = production_rows(games)
    ev = fbs(games, F, HOLD)
    ev = ev[ev.game_id.isin(prod.game_id)].reset_index(drop=True)
    ev0 = fbs(games, F0, HOLD).set_index("game_id").loc[ev.game_id].reset_index()
    p2, p0 = M.predict(fz["fg"]["layer"], ev), M.predict(FZ307["layer"], ev0)
    pk0 = fz["v307_sigma_pack"]
    D = {"v2": fg_dist(ev, p2, fz["fg"]["sigma"]), "v1": fg_dist(ev0, p0, pk0),
         "v307": fg_dist(ev0, p0, pk0, curve=False).assign(sd_m=FZ307["layer"]["sigma"])}
    pr = ev[["game_id", "n_h", "n_a"]].merge(prod, on="game_id")
    D["prod"] = pr.assign(ch_margin=pr.prod_margin, ch_total=pr.prod_total, sd_m=pr.prod_sigma,
                          sd_t=fz["prod_pack"]["sd_total_const"], rho=fz["prod_pack"]["rho_mt"])
    D = {k: d.rename(columns={"ch_margin": "mu_m", "ch_total": "mu_t"})[["game_id", "mu_m", "mu_t", "sd_m", "sd_t", "rho"]]
         for k, d in D.items()}
    n = ev[["game_id"]].assign(navg=navg(ev), season=ev.season)
    res = {"cfg": cfg, "frozen_sha256": hashlib.sha256(FROZEN.read_bytes()).hexdigest(),
           "fg_point": {"all": fg_metrics(ev, D, "v2", ("prod", "v307", "v1"))}}
    for s in HOLD:
        res["fg_point"][str(s)] = fg_metrics(ev[ev.season == s], D, "v2", ("prod", "v307"))
    early = navg(ev) < 3
    res["fg_point"]["early_lt3"] = fg_metrics(ev[early], D, "v2", ("prod", "v307", "v1"))
    res["fg_point"]["v1_early_lt3"] = fg_metrics(ev[early], D, "v1", ("v307",))
    # ---- FG ladders
    lad = fg_ladders(ev, D, "v2")
    res["fg_ladders"], bands = {}, {}
    for fam in ("winner", "spread", "total", "team_total"):
        a = lad[("v2", fam)]
        for base in ("prod", "v307"):
            b = lad[(base, fam)]
            assert (a.k.to_numpy() == b.k.to_numpy()).all()
            sc = score_family(a, b)
            sc["by_season"] = {str(s): score_family(a[a.game_id.isin(ev.game_id[ev.season == s])].reset_index(drop=True),
                                                    b[b.game_id.isin(ev.game_id[ev.season == s])].reset_index(drop=True))["d_logloss"]
                               for s in HOLD}
            res["fg_ladders"][f"{fam}_vs_{base}"] = sc
        bands[f"FG_{fam}"] = abstain_bands(a.assign(c0=[c[0] if fam == "spread" else (c[1] if fam == "total" else np.nan) for c in a.ctr]), n)
    # ---- periods
    pf = load_period_frozen(fz)
    pero = orient_periods(games, per)
    res["periods"] = {}
    for seg, fp in pf.items():
        rows = team_rows(ev, pero, p2, seg)
        vr = rows[rows.valid].assign(mu=lambda r: period_predict(fp, r))
        out = {}
        frames = {}
        for kind in (fp["shape"], "normal" if fp["shape"] == "empirical" else "empirical", "naive"):
            pm, outs, ctrs, gids = _period_pmfs(kind, fp, vr, D["v2"].rename(columns={"mu_m": "ch_margin", "mu_t": "ch_total"}))
            if kind == fp["shape"]:
                ctr0 = ctrs
            fams = ["spread", "total", "winner", "tie"] + (["team_total"] if seg == "h1" else [])
            for fam in fams:
                ser, half = SERIES.get((seg, fam), (None, None))
                if fam == "team_total":
                    ser, half = "KXNCAAF1HTEAMTOTAL", 7
                frames[(kind, fam)] = _ladder_frame(pm, outs, ctr0, fam, kalshi_strikes(ser) if ser else [], half, gids)
            # CRPS of period margin / total from the joint PMF
            cm = [PE.crps_pmf(*q.margin_pmf(), h - a) for q, (h, a) in zip(pm, outs)]
            ct = [PE.crps_pmf(*q.total_pmf(), h + a) for q, (h, a) in zip(pm, outs)]
            out[f"crps_{kind}"] = {"margin": PE.cluster_ci(np.array(cm), np.array(gids)),
                                   "total": PE.cluster_ci(np.array(ct), np.array(gids))}
            out[f"_crps_{kind}"] = (np.array(cm), np.array(ct), np.array(gids))
        for fam in sorted({f for (_, f) in frames}):
            a, b = frames[(fp["shape"], fam)], frames[("naive", fam)]
            sc = score_family(a, b)
            sc["vs_alt_shape_d_logloss"] = score_family(a, frames[("normal" if fp["shape"] == "empirical" else "empirical", fam)])["d_logloss"]
            sc["by_season"] = {str(s): score_family(a[a.game_id.isin(ev.game_id[ev.season == s])].reset_index(drop=True),
                                                    b[b.game_id.isin(ev.game_id[ev.season == s])].reset_index(drop=True))["d_logloss"]
                               for s in HOLD}
            out[fam] = sc
            if fam in ("spread", "total"):
                bands[f"{seg}_{fam}"] = abstain_bands(a.assign(c0=[c[0] if fam == "spread" else c[1] for c in a.ctr]), n)
        for key in ("margin", "total"):
            i = 0 if key == "margin" else 1
            new, old = out[f"_crps_{fp['shape']}"], out["_crps_naive"]
            out[f"crps_{key}_delta_vs_naive"] = PE.paired_delta(new[i], old[i], new[2])
        res["periods"][seg] = {k: v for k, v in out.items() if not k.startswith("_")} | {"shape": fp["shape"]}
        print(seg, {f: (out[f]["d_logloss"], out[f]["slope"]) for f in out if not f.startswith(("crps", "_"))})
    res["abstain_bands"] = bands
    RESULTS.write_text(json.dumps(res, indent=1, default=float))
    print(json.dumps(res["fg_point"]["all"], indent=1, default=float))


def cmd_talent():
    """Sensitivity of #307 and v2 to the talent/returning-production timing: prior-season talent, no returning
    production; layers refit on <= 2024 with the frozen hyperparameters; holdout margin MAE / winner log loss."""
    fz = json.loads(FROZEN.read_text())
    _, games, pre, _ = load()
    prev = pre.assign(season=pre.season + 1).set_index(["season", "team"]).talent_z
    prod = production_rows(games)
    out = {}
    for name, cfg in (("v307", GRID[0]), ("v2", tuple(fz["fg"]["cfg"]))):
        F = pd.read_parquet(WORK / cfg_tag(*cfg)).merge(games[["game_id", "season", "home", "away"]], on="game_id")
        G = F.copy()
        G["tal_h"] = [float(prev.get((s, t), 0.0)) for s, t in zip(G.season, G.home)]
        G["tal_a"] = [float(prev.get((s, t), 0.0)) for s, t in zip(G.season, G.away)]
        G["ret_h"], G["ret_a"] = 0.0, 0.0
        G = G.drop(columns=["season", "home", "away"])
        F = F.drop(columns=["season", "home", "away"])
        r = {}
        for tag, FF in (("as_published", F), ("prior_season_talent_no_ret", G)):
            tr = fbs(games, FF, TRAIN)
            ho = fbs(games, FF, HOLD)
            ho = ho[ho.game_id.isin(prod.game_id)]
            c = fit_layer(tr)
            p = ho.merge(M.predict(c, ho), on="game_id")
            m, t = ym(p)
            e = navg(p) < 3
            r[tag] = {"margin_mae": float(np.abs(p.ch_margin - m).mean()), "total_mae": float(np.abs(p.ch_total - t).mean()),
                      "margin_mae_early": float(np.abs(p.ch_margin - m)[e].mean()), "_ae": np.abs(p.ch_margin - m).to_numpy(),
                      "_g": p.game_id.to_numpy()}
        a, b = r["prior_season_talent_no_ret"], r["as_published"]
        out[name] = {k: {kk: vv for kk, vv in v.items() if not kk.startswith("_")} for k, v in r.items()}
        out[name]["delta_margin_mae"] = PE.paired_delta(a["_ae"], b["_ae"], a["_g"])
    print(json.dumps(out, indent=1))
    (WORK / "talent_sensitivity.json").write_text(json.dumps(out, indent=1))


def cmd_prior_variants():
    """Round 2 (validation only, after the centre grid failed): decayed end-of-prior-season rating gaps, or the
    production P0 forecast, as extra early-season layer features. Never scored on the holdout unless it wins."""
    tg, games, _, _ = load()
    t = M.team_game_metrics(tg)
    rows = []
    for s in ALL:
        c = X.season_centre(t[t.season < s], s, X.Params15(lam_c=1.0, rho=1.0))
        for g in games[games.season == s].itertuples():
            hk, ak = (g.home if g.home_div == "fbs" else M.FCS), (g.away if g.away_div == "fbs" else M.FCS)
            d = {"game_id": g.game_id}
            for m in ("pts", "ppd"):
                O, D = c[m]["O"], c[m]["D"]
                d[f"prev_{m}_h"], d[f"prev_{m}_a"] = O.get(hk, 0) - D.get(ak, 0), O.get(ak, 0) - D.get(hk, 0)
            rows.append(d)
    FF = pd.read_parquet(WORK / cfg_tag(*GRID[0])).merge(pd.DataFrame(rows), on="game_id").merge(
        production_rows(games), on="game_id", how="left")

    def design2(Fr, variant):
        Xm, Xt, _ = M.design(Fr, K307)
        dec = ((K307 / (K307 + Fr.n_h) + K307 / (K307 + Fr.n_a)) / 2).to_numpy()
        if variant == "prev_end_gap":
            Xm = np.c_[Xm, dec * (Fr.prev_pts_h - Fr.prev_pts_a), dec * (Fr.prev_ppd_h - Fr.prev_ppd_a)]
            Xt = np.c_[Xt, dec * (Fr.prev_pts_h + Fr.prev_pts_a)]
        if variant == "production_p0":
            has = Fr.prod_margin.notna().to_numpy()
            Xm = np.c_[Xm, dec * np.nan_to_num(Fr.prod_margin.to_numpy()), dec * has]
            Xt = np.c_[Xt, dec * np.nan_to_num(Fr.prod_total.to_numpy()), dec * has]
        return Xm, Xt
    dev, val = fbs(games, FF, DEV), fbs(games, FF, VAL)
    out = {}
    for v in ("pr307", "prev_end_gap", "production_p0"):
        (Xm, Xt), (Vm, Vt) = design2(dev, v), design2(val, v)
        (m, tt), (vm, vt) = ym(dev), ym(val)
        pm, pt = M._apply(M._ridge(Xm, m, ALPHA307), Vm), M._apply(M._ridge(Xt, tt, ALPHA307), Vt)
        e = navg(val) < 3
        out[v] = {"val_margin_mae": float(np.abs(pm - vm).mean()), "val_margin_mae_early": float(np.abs(pm - vm)[e].mean()),
                  "val_total_mae": float(np.abs(pt - vt).mean()), "val_total_mae_early": float(np.abs(pt - vt)[e].mean())}
    print(json.dumps(out, indent=1))
    (WORK / "prior_variants.json").write_text(json.dumps(out, indent=1))


KSER = {"FG": ("KXNCAAFGAME", "KXNCAAFSPREAD", "KXNCAAFTOTAL", "KXNCAAFTEAMTOTAL"),
        "h1": ("KXNCAAF1H", "KXNCAAF1HSPREAD", "KXNCAAF1HTOTAL", "KXNCAAF1HTEAMTOTAL"),
        "h2": ("KXNCAAF2H", "KXNCAAF2HSPREAD", "KXNCAAF2HTOTAL"),
        **{f"q{i}": (f"KXNCAAF{i}Q", f"KXNCAAF{i}QSPREAD", f"KXNCAAF{i}QTOTAL") for i in range(1, 5)}}
MON = {m: i for i, m in enumerate(("JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"), 1)}


def _family(series):
    for f in ("TEAMTOTAL", "SPREAD", "TOTAL"):
        if series.endswith(f):
            return {"TEAMTOTAL": "team_total", "SPREAD": "spread", "TOTAL": "total"}[f]
    return "winner"


def kalshi_markets(per):
    """Every finalized Kalshi CFB game/period market matched to an ESPN game (abbreviations, else final score)."""
    import re
    rows = []
    for seg, sers in KSER.items():
        for ser in sers:
            for tier in ("historical", "live"):
                f = KALSHI / f"{ser}.{tier}.jsonl"
                if not f.exists():
                    continue
                for line in open(f):
                    r = json.loads(line)
                    if r.get("result") not in ("yes", "no"):
                        continue
                    ev, last = r["event_ticker"], r["ticker"].split("-")[-1]
                    m = re.match(r"(\d\d)([A-Z]{3})(\d\d)(.+)$", ev.split("-", 1)[1])
                    if not m:
                        continue
                    rows.append({"seg": seg, "series": ser, "family": _family(ser), "ticker": r["ticker"],
                                 "date": f"20{m[1]}-{MON[m[2]]:02d}-{m[3]}", "S": m[4], "code": re.sub(r"\d+$", "", last),
                                 "strike": r.get("floor_strike"), "yes": r["result"] == "yes", "exp": r.get("expiration_value")})
    K = pd.DataFrame(rows).drop_duplicates("ticker")
    p = per[per.valid].copy()
    p["ldate"] = (pd.to_datetime(p.kickoff_utc, utc=True) - pd.Timedelta(hours=6)).dt.date.astype(str)
    key = {}
    for r in p.itertuples():
        key[(r.ldate, f"{r.away_abbr}{r.home_abbr}")] = (r.game_id, True)     # first code = ESPN away
        key[(r.ldate, f"{r.home_abbr}{r.away_abbr}")] = (r.game_id, False)
    sc = {}
    for r in p.itertuples():
        # Kalshi event codes list the Kalshi away team first; "Home: x, Away: y" fixes Kalshi home vs ESPN home
        sc.setdefault((r.ldate, int(r.h_final), int(r.a_final)), []).append((r.game_id, True))
        sc.setdefault((r.ldate, int(r.a_final), int(r.h_final)), []).append((r.game_id, False))
    ev_map = {}
    for (d, S), g in K.groupby(["date", "S"]):
        if (d, S) in key:
            ev_map[(d, S)] = key[(d, S)]
            continue
        tt = g[g.series == "KXNCAAFTEAMTOTAL"].exp.dropna()
        for e in tt:
            mm = re.match(r"Home: (\d+), Away: (\d+)", str(e))
            if mm and len(sc.get((d, int(mm[1]), int(mm[2])), [])) == 1:
                ev_map[(d, S)] = sc[(d, int(mm[1]), int(mm[2]))][0]
                break
    K["game_id"] = [ev_map.get((d, S), (None, None))[0] for d, S in zip(K.date, K.S)]
    K["first_is_away"] = [ev_map.get((d, S), (None, None))[1] for d, S in zip(K.date, K.S)]
    K = K.dropna(subset=["game_id"]).merge(p[["game_id", "home_abbr", "away_abbr"]], on="game_id")
    # which ESPN side is the contract's team: exact abbreviation, else prefix/suffix of the event code
    side = []
    for r in K.itertuples():
        if r.family in ("total",) or r.code == "TIE" or (r.family == "winner" and r.code == ""):
            side.append("tie" if r.code == "TIE" else "none")
        elif r.code == r.home_abbr:
            side.append("home")
        elif r.code == r.away_abbr:
            side.append("away")
        elif r.S.startswith(r.code) != r.S.endswith(r.code):
            side.append("away" if r.S.startswith(r.code) == bool(r.first_is_away) else "home")
        else:
            side.append(None)
    K["side"] = side
    return K.dropna(subset=["side"])


def realised(K, per):
    p = per.set_index("game_id")
    out = []
    for r in K.itertuples():
        q = p.loc[r.game_id]
        if r.seg == "FG":
            h, a = q.h_final, q.a_final
        else:
            h, a = (sum(q[f"{s}_{x}"] for x in SEGS[r.seg]) for s in ("h", "a"))
        out.append(contract_outcome(r.family, r.side, r.strike, h, a))
    return np.array(out)


def contract_outcome(fam, side, k, h, a):
    if fam == "total":
        return float(h + a > k)
    if fam == "team_total":
        return float((h if side == "home" else a) > k)
    if fam == "spread":
        return float(((h - a) if side == "home" else (a - h)) > k)
    if side == "tie":
        return float(h == a)
    return float(((h - a) if side == "home" else (a - h)) > 0)


def contract_prob(pm, fam, side, k):
    if fam == "total":
        return pm.p_total_gt(k)
    if fam == "team_total":
        return pm.p_home_gt(k) if side == "home" else pm.p_away_gt(k)
    if fam == "spread":
        return pm.p_margin_gt(k) if side == "home" else float(pm.p[(pm._h - pm._a) < -k].sum())
    if side == "tie":
        return pm.p_tie()
    return pm.p_margin_gt(0.5) if side == "home" else float(pm.p[(pm._h - pm._a) < -0.5].sum())


def cmd_kalshi():
    """Settlement check (linescores vs Kalshi results) and scoring at the strikes Kalshi actually listed per game."""
    fz = json.loads(FROZEN.read_text())
    _, games, _, per = load()
    K = kalshi_markets(per)
    K["y_linescore"] = realised(K, per)
    agree = K.assign(ok=K.y_linescore == K.yes.astype(float)).groupby("series").ok.agg(["size", "mean"])
    print(agree.to_string())
    F = pd.read_parquet(WORK / cfg_tag(*tuple(fz["fg"]["cfg"])))
    prod = production_rows(games)
    ev = fbs(games, F, HOLD)
    ev = ev[ev.game_id.isin(prod.game_id) & ev.game_id.isin(K.game_id)].reset_index(drop=True)
    p2 = M.predict(fz["fg"]["layer"], ev)
    D = fg_dist(ev, p2, fz["fg"]["sigma"])
    pr = ev[["game_id"]].merge(prod, on="game_id")
    pf = load_period_frozen(fz)
    pero = orient_periods(games, per)
    # ESPN orientation of the games table may differ from the linescore file for neutral sites
    flip = set(per.merge(games[["game_id", "home"]], on="game_id").query("home_x != home_y").game_id)
    pm = {}
    for r in D.itertuples():
        pm[("FG", "model", r.game_id)] = PE.pmf_from_margin_total(r.ch_margin, r.sd_m, r.ch_total, r.sd_t, r.rho, 200)
    for r in pr.itertuples():
        pm[("FG", "base", r.game_id)] = PE.pmf_from_margin_total(r.prod_margin, r.prod_sigma, r.prod_total,
                                                                 fz["prod_pack"]["sd_total_const"], fz["prod_pack"]["rho_mt"], 200)
    for seg, fp in pf.items():
        rows = team_rows(ev, pero, p2, seg)
        vr = rows[rows.valid].assign(mu=lambda r: period_predict(fp, r))
        for kind, tag in ((fp["shape"], "model"), ("naive", "base")):
            pms, _, _, gids = _period_pmfs(kind, fp, vr, D)
            for q, g in zip(pms, gids):
                pm[(seg, tag, g)] = q
    out, mrows = {}, []
    K = K[K.game_id.isin(ev.game_id)]
    for r in K.itertuples():
        side = r.side
        if r.game_id in flip and side in ("home", "away"):
            side = "away" if side == "home" else "home"
        if (r.seg, "model", r.game_id) not in pm or (r.seg, "base", r.game_id) not in pm:
            continue
        pn = contract_prob(pm[(r.seg, "model", r.game_id)], r.family, side, r.strike)
        pb = contract_prob(pm[(r.seg, "base", r.game_id)], r.family, side, r.strike)
        mrows.append({"series": r.series, "seg": r.seg, "family": r.family, "game_id": r.game_id, "p": pn, "pb": pb,
                      "y": float(r.yes), "y_linescore": r.y_linescore})
    Mr = pd.DataFrame(mrows)
    for ser, g in Mr.groupby("series"):
        sc = score_family(g[["game_id", "p", "y"]].reset_index(drop=True), g[["game_id", "pb", "y"]].rename(columns={"pb": "p"}).reset_index(drop=True))
        out[ser] = {k: sc[k] for k in ("n_rows", "n_games", "brier", "brier_base", "d_brier", "logloss", "logloss_base",
                                       "d_logloss", "p_logloss", "slope", "slope_base", "bias", "bias_base", "reliability")}
        mid = g[(g.p > 0.1) & (g.p < 0.9)]
        if len(mid) > 50 and len(mid) < len(g):
            out[ser]["p_0.1_0.9_only"] = {"n_rows": int(len(mid)), "slope": round(PE.calibration_slope(mid.p, mid.y), 3),
                                          "bias": round(float((mid.p - mid.y).mean()), 4)}
        print(ser, out[ser]["n_rows"], out[ser]["n_games"], out[ser]["d_logloss"], out[ser]["slope"], out[ser]["bias"])
    res = json.loads(RESULTS.read_text())
    res["kalshi_actual_markets"] = {"note": "2025-26 FBS-vs-FBS holdout games; every finalized market Kalshi listed; outcome = "
                                            "Kalshi result; baseline = production 0.5.0 (FG) / naive proportional split (periods)",
                                    "settlement_agreement": {k: [int(v["size"]), round(float(v["mean"]), 4)]
                                                             for k, v in agree.iterrows()},
                                    "n_markets_matched": int(len(K)), "by_series": out}
    RESULTS.write_text(json.dumps(res, indent=1, default=float))


if __name__ == "__main__":
    globals()["cmd_" + sys.argv[1]]()
