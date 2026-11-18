#!/usr/bin/env python3
"""Reproducible pipeline for the CFB opponent-adjusted-efficiency challenger (research only).

    python research/cfb_challenger/run_challenger.py data      # parse vault ESPN summaries, P0 2026 extension
    python research/cfb_challenger/run_challenger.py tune      # dev 2016-2022 -> validation 2023-2024 grid
    python research/cfb_challenger/run_challenger.py freeze    # refit layer on 2016-2024, write frozen_params.json
    python research/cfb_challenger/run_challenger.py holdout   # score 2025 + 2026-to-date ONCE vs production

Inputs (never committed): WORK=/tmp/cfb_work holding the restored vault CFBD cache (2014-2026/), ESPN summaries
(espn/) and the research-data preseason cache (rd/). Betting-line files are never read.
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
import challenger  # noqa: E402,F401  (sets BLAS threads before numpy)
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from challenger import evaluate as E  # noqa: E402
from challenger import model as M  # noqa: E402
from challenger import production as P  # noqa: E402

WORK = Path(os.environ.get("CFB_WORK", "/tmp/cfb_work"))
CFBD, ESPN, RD = WORK / "2014-2026", WORK / "espn", WORK / "rd"
TG = WORK / "team_games.parquet"
FROZEN, RESULTS = HERE / "frozen_params.json", HERE / "results.json"
DEV = [2016, 2017, 2018, 2019, 2021, 2022]          # 2020 (COVID) excluded as a target
VAL = [2023, 2024]
HOLD = [2025, 2026]
GRID = {"lam": [1.0, 3.0, 8.0, 20.0], "w_prev": [0.1, 0.2, 0.4, 0.7]}
K_GRID, ALPHA_GRID = [1.0, 3.0, 6.0, 12.0], [1.0, 30.0]


def load():
    tg = pd.read_parquet(TG)
    return tg, M.games_table(tg), M.preseason_table(CFBD)


def production_table(games: pd.DataFrame) -> pd.DataFrame:
    ext = pd.read_parquet(WORK / "prod_2026_ext.parquet")
    ext = ext.merge(games[["game_id", "home_pts", "away_pts"]], on="game_id").rename(
        columns={"home_pts": "p_home_pts", "away_pts": "p_away_pts"})
    pr = pd.concat([P.load_hist(), P.load_archived_2026(), ext], ignore_index=True)
    pr = pr[["game_id", "prod_margin", "prod_total", "prod_sigma", "p_home_pts", "p_away_pts"]].drop_duplicates("game_id")
    g = games.merge(pr, on="game_id")
    same = (g.home_pts == g.p_home_pts) & (g.away_pts == g.p_away_pts)
    flip = (g.home_pts == g.p_away_pts) & (g.away_pts == g.p_home_pts) & ~same
    g = g[same | flip].copy()
    g.loc[flip, "prod_margin"] *= -1                     # production oriented to its CFBD home team
    g["prod_p"] = P.win_prob(g.prod_margin, g.prod_sigma)
    g["prod_home"], g["prod_away"] = (g.prod_total + g.prod_margin) / 2, (g.prod_total - g.prod_margin) / 2
    return g[["game_id", "prod_margin", "prod_total", "prod_p", "prod_home", "prod_away"]]


def features(params: M.Params, seasons) -> pd.DataFrame:
    tag = f"feat_{params.lam}_{params.w_prev}_{min(seasons)}_{max(seasons)}.parquet"
    f = WORK / tag
    if f.exists():
        return pd.read_parquet(f)
    tg, games, pre = load()
    F = M.walk_forward_features(tg, games, pre, params, seasons)
    F.to_parquet(f)
    return F


def _feat_job(lw):
    return features(M.Params(lam=lw[0], w_prev=lw[1]), list(range(2016, 2025)))


def fbs_eval_set(games, F, seasons):
    g = games[games.season.isin(seasons) & (games.home_div == "fbs") & (games.away_div == "fbs")]
    return g.merge(F, on="game_id")


def cmd_data():
    from challenger.data import build_team_games
    if not TG.exists():
        build_team_games(ESPN, CFBD, TG)
    tg, games, _ = load()
    ext = WORK / "prod_2026_ext.parquet"
    if not ext.exists():
        slug = P.espn_to_slug(games)
        a = P.load_archived_2026()
        g = games[(games.season == 2026) & (games.home_div == "fbs") & (games.away_div == "fbs")
                  & ~games.game_id.isin(a.game_id)].copy()
        g["home_slug"], g["away_slug"] = g.home.map(slug), g.away.map(slug)
        P.build_2026(g.dropna(subset=["home_slug", "away_slug"]), CFBD, RD).to_parquet(ext)
    print("team-games", len(tg), "games", len(games))


def cmd_tune():
    tg, games, _ = load()
    combos = list(itertools.product(GRID["lam"], GRID["w_prev"]))
    with ProcessPoolExecutor(4) as ex:
        feats = dict(zip(combos, ex.map(_feat_job, combos)))
    prod = production_table(games)
    rows = []
    for (lam, wp), F in feats.items():
        dev, val = fbs_eval_set(games, F, DEV), fbs_eval_set(games, F, VAL)
        for k, al in itertools.product(K_GRID, ALPHA_GRID):
            coef = M.fit_layer(dev, (dev.home_pts - dev.away_pts).to_numpy(), (dev.home_pts + dev.away_pts).to_numpy(), al, k)
            pr = val.merge(M.predict(coef, val), on="game_id")
            l = E.per_game_losses(pr, "ch").mean()
            rows.append({"lam": lam, "w_prev": wp, "k": k, "alpha": al, **{m: float(l[m]) for m in l.index}})
    tab = pd.DataFrame(rows).sort_values("margin_ae")
    print(tab.head(10).to_string())
    best = tab.iloc[0]
    F = feats[(best.lam, best.w_prev)]
    val = fbs_eval_set(games, F, VAL).merge(prod, on="game_id")
    dev = fbs_eval_set(games, F, DEV)
    coef = M.fit_layer(dev, (dev.home_pts - dev.away_pts).to_numpy(), (dev.home_pts + dev.away_pts).to_numpy(), best.alpha, best.k)
    val = val.merge(M.predict(coef, val), on="game_id")
    cmp_ = E.compare(val)
    print(json.dumps(cmp_, indent=1))
    (WORK / "tune.json").write_text(json.dumps({"grid": rows, "best": best.to_dict(), "validation_vs_prod": cmp_}, indent=1))


def cmd_freeze():
    tune = json.loads((WORK / "tune.json").read_text())
    b = tune["best"]
    p = M.Params(lam=b["lam"], w_prev=b["w_prev"], decay_k=b["k"], layer_alpha=b["alpha"])
    _, games, _ = load()
    F = features(p, list(range(2016, 2025)))
    tr = fbs_eval_set(games, F, DEV + VAL)
    coef = M.fit_layer(tr, (tr.home_pts - tr.away_pts).to_numpy(), (tr.home_pts + tr.away_pts).to_numpy(), p.layer_alpha, p.decay_k)
    code = hashlib.sha256(b"".join((HERE / "challenger" / f).read_bytes() for f in ("model.py", "data.py"))).hexdigest()
    FROZEN.write_text(json.dumps({"model": "cfb_oae_challenger_v1", "params": p.__dict__, "layer": coef,
                                  "trained_on": DEV + VAL, "selected_on": VAL, "code_sha256": code,
                                  "validation_vs_prod": tune["validation_vs_prod"]}, indent=1))
    print("frozen", FROZEN)


def cmd_holdout():
    fz = json.loads(FROZEN.read_text())
    p = M.Params(**fz["params"])
    tg, games, pre = load()
    F = M.walk_forward_features(tg, games, pre, p, HOLD)
    ev = fbs_eval_set(games, F, HOLD).merge(production_table(games), on="game_id")
    ev = ev.merge(M.predict(fz["layer"], ev), on="game_id")
    res = {"holdout": "FBS-vs-FBS games, 2025 season (incl. postseason) + 2026 to date, identical games for both",
           "frozen_params_sha256": hashlib.sha256(FROZEN.read_bytes()).hexdigest(),
           "n_fbs_games_without_production_forecast": int(len(fbs_eval_set(games, F, HOLD)) - len(ev)),
           "all": E.compare(ev)}
    for s in HOLD:
        res[str(s)] = E.compare(ev[ev.season == s])
    b1 = P.load_b1_2025().merge(ev[ev.season == 2025], on="game_id")
    b1 = b1[(b1.home_pts == b1.b1_home_pts) | (b1.home_pts == b1.b1_away_pts)].copy()
    b1.loc[(b1.home_pts == b1.b1_away_pts) & (b1.home_pts != b1.b1_home_pts), "b1_margin"] *= -1
    b1["b1_p"] = P.win_prob(b1.b1_margin, b1.b1_sigma)
    b1["b1_home"], b1["b1_away"] = (b1.b1_total + b1.b1_margin) / 2, (b1.b1_total - b1.b1_margin) / 2
    res["reference_2025_vs_B1_inseason_production_stack"] = E.compare(b1, "ch", "b1")
    early = ev[(ev.n_h + ev.n_a) / 2 < 3]
    res["early_season_lt3_games"] = E.compare(early)
    RESULTS.write_text(json.dumps(res, indent=1))
    print(json.dumps(res["all"], indent=1))


if __name__ == "__main__":
    {"data": cmd_data, "tune": cmd_tune, "freeze": cmd_freeze, "holdout": cmd_holdout}[sys.argv[1]]()
