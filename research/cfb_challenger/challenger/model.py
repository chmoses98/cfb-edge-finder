"""Challenger: opponent-adjusted efficiency ratings (ridge, offence/defence separately) refit before every game
date from strictly-earlier games, with prior seasons carried at decaying weight, plus preseason talent /
returning production whose weight decays with games played; a game-level linear layer maps the adjusted
efficiency gaps to margin, total and team totals, and a probit layer gives the winner probability.
"""
from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

METRICS = ("pts", "ppd", "sr", "ypp", "plays", "drives")
FCS = "FCS"


@dataclass
class Params:
    lam: float = 6.0            # ridge penalty on team effects (in game-equivalents)
    w_prev: float = 0.35        # weight of last season's games
    w_prev2: float = 0.10       # weight of two seasons back
    decay_k: float = 4.0        # preseason-prior features scaled by k / (k + games played)
    layer_alpha: float = 1.0    # ridge on the game-level layer


def team_game_metrics(tg: pd.DataFrame) -> pd.DataFrame:
    t = tg.copy()
    t["pts"] = t["points_for"].astype(float)
    t["plays"] = t["box_plays"].astype(float)
    t["ypp"] = t["box_total_yards"] / t["box_plays"].where(t["box_plays"] > 0)
    t["sr"] = t["pbp_success_plays"] / t["pbp_scrim_plays"].where(t["pbp_scrim_plays"] > 0)
    t["drives"] = t["pbp_drives"].astype(float)
    t["ppd"] = t["pbp_drive_points"] / t["pbp_drives"].where(t["pbp_drives"] > 0)
    t["off"] = np.where(t["team_division"] == "fbs", t["team_id"], FCS)
    t["dfn"] = np.where(t["opponent_division"] == "fbs", t["opponent_id"], FCS)
    t["hflag"] = np.where(t["site"] == "home", 1.0, np.where(t["site"] == "away", -1.0, 0.0))
    return t


def fit_ratings(rows: pd.DataFrame, weights: np.ndarray, lam: float) -> dict[str, dict]:
    """Weighted ridge y = mu + hfa*hflag + O[off] - D[dfn] per metric. Returns {metric: {mu, hfa, O, D}}."""
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
    pen = np.diag(np.r_[0.0, 0.0, np.full(2 * T, lam)])
    out = {}
    for m in METRICS:
        y = rows[m].to_numpy(float)
        ok = np.isfinite(y)
        w = weights * ok
        Xw = X * w[:, None]
        beta = np.linalg.solve(X.T @ Xw + pen + 1e-9 * np.eye(2 * T + 2), Xw.T @ np.where(ok, y, 0.0))
        out[m] = {"mu": beta[0], "hfa": beta[1], "O": dict(zip(teams, beta[2:2 + T])),
                  "D": dict(zip(teams, beta[2 + T:]))}
    return out


def _name_to_id(cfbd_root: Path) -> dict[tuple[int, str], str]:
    out = {}
    for s in range(2014, 2027):
        f = cfbd_root / str(s) / "games.json.gz"
        if f.exists():
            for g in json.load(gzip.open(f)):
                out[g["homeTeam"]] = str(g["homeId"])
                out[g["awayTeam"]] = str(g["awayId"])
    return out


def preseason_table(cfbd_root: Path) -> pd.DataFrame:
    """Season-S preseason inputs: 247 talent composite (z within season) and offensive returning production."""
    ids = _name_to_id(cfbd_root)
    rows = []
    for s in range(2014, 2027):
        tal, ret = {}, {}
        f = cfbd_root / str(s) / "talent.json.gz"
        if f.exists():
            tal = {ids.get(x["team"]): float(x["talent"]) for x in json.load(gzip.open(f)) if x.get("talent")}
        f = cfbd_root / str(s) / "returning_production.json.gz"
        if f.exists():
            ret = {ids.get(x["team"]): x.get("percentPPA") for x in json.load(gzip.open(f))}
        for t in set(tal) | set(ret):
            if t:
                rows.append({"season": s, "team": t, "talent": tal.get(t), "ret": ret.get(t)})
    df = pd.DataFrame(rows)
    for c in ("talent", "ret"):
        g = df.groupby("season")[c]
        df[c + "_z"] = ((df[c] - g.transform("mean")) / g.transform("std")).fillna(0.0)
    return df[["season", "team", "talent_z", "ret_z"]]


def games_table(tg: pd.DataFrame) -> pd.DataFrame:
    """One row per game, home-oriented (for neutral sites ESPN's listed home team)."""
    h = tg[tg["site"].isin(["home", "neutral"])].copy()
    h = h.sort_values(["game_id", "site"]).drop_duplicates("game_id")  # neutral: keep one orientation
    # ensure the paired opponent row exists
    keys = set(zip(tg.game_id, tg.team_id))
    h = h[[(g, o) in keys for g, o in zip(h.game_id, h.opponent_id)]]
    return pd.DataFrame({"game_id": h.game_id, "season": h.season, "week": h.week, "date": h.date,
                         "postseason": h.postseason.astype(bool), "neutral": h.site == "neutral",
                         "home": h.team_id, "away": h.opponent_id,
                         "home_div": h.team_division, "away_div": h.opponent_division,
                         "home_pts": h.points_for.astype(float), "away_pts": h.points_against.astype(float)}
                        ).reset_index(drop=True)


def walk_forward_features(tg: pd.DataFrame, games: pd.DataFrame, pre: pd.DataFrame, p: Params,
                          seasons) -> pd.DataFrame:
    """For every game in `seasons`, ratings fit on team-games with date strictly before the game's date."""
    t = team_game_metrics(tg)
    pre_ix = pre.set_index(["season", "team"])
    feats = []
    for s in seasons:
        base = t[t.season.isin([s - 2, s - 1])]
        bw = np.where(base.season == s - 1, p.w_prev, p.w_prev2)
        cur = t[t.season == s]
        gs = games[games.season == s]
        for d in sorted(gs.date.unique()):
            c = cur[cur.date < d]
            rows = pd.concat([base, c])
            w = np.r_[bw, np.ones(len(c))]
            R = fit_ratings(rows, w, p.lam)
            gp = c.groupby("team_id").size()
            for g in gs[gs.date == d].itertuples():
                f = {"game_id": g.game_id}
                hk = g.home if g.home_div == "fbs" else FCS
                ak = g.away if g.away_div == "fbs" else FCS
                hf = 0.0 if g.neutral else 1.0
                for m in METRICS:
                    r = R[m]
                    eh = r["mu"] + r["hfa"] * hf + r["O"].get(hk, 0.0) - r["D"].get(ak, 0.0)
                    ea = r["mu"] - r["hfa"] * hf + r["O"].get(ak, 0.0) - r["D"].get(hk, 0.0)
                    f[f"{m}_h"], f[f"{m}_a"] = eh, ea
                nh, na = gp.get(g.home, 0), gp.get(g.away, 0)
                for side, team, n in (("h", g.home, nh), ("a", g.away, na)):
                    k = (s, team)
                    tz = pre_ix.loc[k, "talent_z"] if k in pre_ix.index else 0.0
                    rz = pre_ix.loc[k, "ret_z"] if k in pre_ix.index else 0.0
                    f[f"tal_{side}"], f[f"ret_{side}"] = float(tz), float(rz)
                    f[f"n_{side}"] = n
                f["hf"] = hf
                feats.append(f)
    return pd.DataFrame(feats)


def design(F: pd.DataFrame, k: float) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Margin and total design matrices (home perspective); k = preseason-prior decay constant."""
    dec = (k / (k + F.n_h) + k / (k + F.n_a)) / 2
    eff = {}
    for m in ("pts", "ppd", "sr", "ypp"):
        eff[f"m_{m}"] = F[f"{m}_h"] - F[f"{m}_a"]
    eff["m_ppd_x_dr"] = F.ppd_h * F.drives_h - F.ppd_a * F.drives_a
    marg = pd.DataFrame({**eff, "m_tal": F.tal_h - F.tal_a, "m_tal_dec": (F.tal_h - F.tal_a) * dec,
                         "m_ret_dec": (F.ret_h - F.ret_a) * dec, "hf": F.hf})
    tot = pd.DataFrame({"t_pts": F.pts_h + F.pts_a, "t_ppd_x_dr": F.ppd_h * F.drives_h + F.ppd_a * F.drives_a,
                        "t_plays": F.plays_h + F.plays_a, "t_sr": F.sr_h + F.sr_a, "t_ypp": F.ypp_h + F.ypp_a,
                        "t_tal_dec": (F.tal_h + F.tal_a) * dec})
    return marg.to_numpy(float), tot.to_numpy(float), list(marg.columns) + list(tot.columns)


def _ridge(X, y, alpha):
    mu, sd = X.mean(0), X.std(0) + 1e-9
    Z = np.c_[np.ones(len(X)), (X - mu) / sd]
    pen = np.r_[0.0, np.full(X.shape[1], alpha)]
    b = np.linalg.solve(Z.T @ Z + np.diag(pen), Z.T @ y)
    return {"mu": mu.tolist(), "sd": sd.tolist(), "b": b.tolist()}


def _apply(c, X):
    return np.c_[np.ones(len(X)), (X - np.array(c["mu"])) / np.array(c["sd"])] @ np.array(c["b"])


def fit_layer(F: pd.DataFrame, y_margin, y_total, alpha: float, k: float) -> dict:
    Xm, Xt, names = design(F, k)
    cm, ct = _ridge(Xm, y_margin, alpha), _ridge(Xt, y_total, alpha)
    resid = y_margin - _apply(cm, Xm)
    return {"margin": cm, "total": ct, "sigma": float(np.sqrt(np.mean(resid**2))), "k": k, "features": names}


def predict(coef: dict, F: pd.DataFrame) -> pd.DataFrame:
    Xm, Xt, _ = design(F, coef["k"])
    m, tot = _apply(coef["margin"], Xm), _apply(coef["total"], Xt)
    return pd.DataFrame({"game_id": F.game_id.to_numpy(), "ch_margin": m, "ch_total": tot,
                         "ch_home": (tot + m) / 2, "ch_away": (tot - m) / 2,
                         "ch_p": norm.sf((0.5 - m) / coef["sigma"])})
