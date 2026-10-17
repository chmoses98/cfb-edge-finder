"""Root-cause analysis of the script engine's defensive rankings (the owner's 'unrealistic defensive
rankings' observation).

Where the rankings come from (read-only use of production code):
  src/cfb_edge_finder/scripting/adjust.py  fit_metric / rank_in_universe
  src/cfb_edge_finder/scripting/matchup.py unit_metric -> "rank" published for every defence metric
The model is a season-to-date ridge  y = mu + h*x + o_team + d_opp (+ shared non-FBS offsets), lambda = 4
games, ranked over FBS teams, published whether or not the fit is 'stable'.

This module replays that exact code (adjust.fit_metric) on 2015-2025 CFBD team-games, week by week, and
compares its defensive points rating with alternatives on independent future data.
"""
from __future__ import annotations

import sys
from datetime import timedelta

import numpy as np
import pandas as pd
from scipy import stats

from . import paths, ratings


def _se_modules():
    src = str(paths.REPO_ROOT / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from cfb_edge_finder.scripting import adjust, gamelog  # noqa: E402
    return adjust, gamelog


def team_game_objects(T: pd.DataFrame):
    _, gamelog = _se_modules()
    out = []
    site = {1: "home", -1: "away", 0: "neutral"}
    for r in T.itertuples(index=False):
        out.append(gamelog.TeamGame(
            game_id=str(r.game_id), season=int(r.season), week=int(r.week),
            kickoff_utc=r.kickoff.strftime("%Y-%m-%dT%H:%M:%SZ"), team_id=str(r.team_id), team=str(r.team),
            opponent_id=str(r.opp_id), opponent=str(r.opp),
            team_division=r.team_div if r.team_div in ("fbs", "fcs") else "other",
            opponent_division=r.opp_div if r.opp_div in ("fbs", "fcs") else "other",
            site=site[int(r.site)], points_for=float(r.pts), points_against=float(r.pts_allowed),
            box={}, pbp=None))
    return out


def replay_script_engine(T: pd.DataFrame, seasons, metric: str = "points_per_game") -> pd.DataFrame:
    """Week-by-week replay of the production fit. Returns one row per (season, week, team) with the
    published adjusted defence/offence values, ranks, games and the fit's stability flag."""
    adjust, _ = _se_modules()
    rows = []
    for s in seasons:
        Ts = T[(T["season"] == s) & ~T["post"]]
        objs = team_game_objects(Ts)
        for w in sorted(Ts["week"].unique()):
            if w < 2:
                continue
            first = Ts.loc[Ts["week"] == w, "kickoff"].min()
            cutoff = (first - timedelta(minutes=1)).strftime("%Y-%m-%dT%H:%M:%SZ")
            lf = adjust.fit_league(objs, cutoff, metric_ids=(metric,))
            fit = lf.fits[metric]
            if not fit.adjusted:
                continue
            for unit in ("offense", "defense"):
                higher = unit == "offense"
                for t in fit.fbs_teams:
                    e = fit.get(t, unit)
                    if e is None or e.adjusted is None:
                        continue
                    rk, n = adjust.rank_in_universe(fit, t, unit, higher)
                    rows.append({"season": s, "week": int(w), "team_id": t, "unit": unit, "adjusted": e.adjusted,
                                 "raw": e.raw, "games": e.games, "rank": rk, "universe": n, "mu": fit.mu,
                                 "h": fit.home_effect, "stable": lf.stable, "components": lf.fbs_components})
            # non-FBS units (needed to predict FBS-vs-FCS games)
            for (t, unit), e in fit.estimates.items():
                if t in fit.fbs_teams or e.adjusted is None:
                    continue
                rows.append({"season": s, "week": int(w), "team_id": t, "unit": unit, "adjusted": e.adjusted,
                             "raw": e.raw, "games": e.games, "rank": None, "universe": None, "mu": fit.mu,
                             "h": fit.home_effect, "stable": lf.stable, "components": lf.fbs_components})
    return pd.DataFrame(rows)


def se_team_points(T: pd.DataFrame, SE: pd.DataFrame) -> pd.Series:
    """The production 'scoring baseline' per team side: adj_off(team) + adj_def(opp) - mu + h*x
    (x = +1 home, -1 away, 0 neutral; floored at 0 as production does)."""
    o = SE[SE["unit"] == "offense"].set_index(["season", "week", "team_id"])
    d = SE[SE["unit"] == "defense"].set_index(["season", "week", "team_id"])
    io = pd.MultiIndex.from_arrays([T["season"], T["week"], T["team_id"]])
    idf = pd.MultiIndex.from_arrays([T["season"], T["week"], T["opp_id"]])
    oo, dd = o.reindex(io), d.reindex(idf)
    pred = oo["adjusted"].to_numpy() + dd["adjusted"].to_numpy() - oo["mu"].to_numpy() + oo["h"].to_numpy() * T["site"].to_numpy()
    pred = np.where(T["post"].to_numpy(), np.nan, pred)
    return pd.Series(np.maximum(pred, 0.0), index=T.index)


def future_truth(T: pd.DataFrame, season: int, week: int, y: str = "pts") -> pd.DataFrame:
    """Defence ratings from games in weeks >= `week` only (independent of everything a week-`week`
    ranking could have seen), pooled FCS, light ridge."""
    sub = T[(T["season"] == season) & ~T["post"] & (T["week"] >= week) & T[y].notna()].copy()
    if len(sub) < 200:
        return pd.DataFrame()
    sub["block"] = 1
    sub = pd.concat([sub, sub.head(1).assign(block=2)], ignore_index=True)
    R = ratings.rolling_ridge_ratings(sub.assign(_o=np.where(sub.team_div == "fbs", sub.team_id, "FCS"),
                                                 _d=np.where(sub.opp_div == "fbs", sub.opp_id, "FCS")),
                                      "block", "_o", "_d", y, home_col="site", alpha=2.0, min_rows=20)
    R = R[R["block"] == 2]
    return R[["team", "def_rating", "off_rating", "n_def"]].rename(columns={"team": "team_id"})


def rank_reliability(T: pd.DataFrame, SE: pd.DataFrame, Dcarry: pd.DataFrame, seasons, weeks=(2, 3, 4, 5, 6, 8, 10)):
    """Spearman correlation of each method's week-w defensive rating with the rest-of-season truth."""
    out = []
    for s in seasons:
        for w in weeks:
            truth = future_truth(T, s, w)
            if truth.empty:
                continue
            fbs = truth[truth["team_id"] != "FCS"]
            se = SE[(SE.season == s) & (SE.week == w) & (SE.unit == "defense") & SE["rank"].notna()]
            # raw points allowed per game to date
            hist = T[(T.season == s) & (T.week < w) & ~T.post]
            raw = hist.groupby("opp_id")["pts"].mean().rename("raw").reset_index().rename(columns={"opp_id": "team_id"})
            blk = s * 100 + w
            dc = Dcarry[(Dcarry["block"] == blk)][["team", "def_rating"]].rename(columns={"team": "team_id", "def_rating": "dcarry"})
            m = fbs.merge(se[["team_id", "adjusted"]], on="team_id", how="left").merge(raw, on="team_id", how="left").merge(dc, on="team_id", how="left")
            row = {"season": s, "week": w, "n": int(len(m)), "stable": bool(se["stable"].iloc[0]) if len(se) else None,
                   "components": int(se["components"].iloc[0]) if len(se) else None}
            for c in ("adjusted", "raw", "dcarry"):
                mm = m.dropna(subset=[c])
                row[f"rho_{c}"] = float(stats.spearmanr(mm[c], mm["def_rating"]).statistic) if len(mm) > 20 else np.nan
                # top-10 overlap with truth
                if len(mm) > 20:
                    top_m = set(mm.nsmallest(10, c)["team_id"])
                    top_t = set(mm.nsmallest(10, "def_rating")["team_id"])
                    row[f"top10_{c}"] = len(top_m & top_t)
            out.append(row)
    return pd.DataFrame(out)


def fcs_credit(T: pd.DataFrame, SE: pd.DataFrame, Xind: pd.DataFrame, Xpool: pd.DataFrame, seasons):
    """Does having played an FCS team flatter a defence's rating (weeks 2-4)? Measured as the bias of the
    NEXT FBS-vs-FBS game's points allowed: defence ratings that were flattered under-predict."""
    rows = []
    for name, pred in (("script_engine", "se_pred"), ("ridge_fcs_individual", "Di_pts_pred"), ("ridge_fcs_pooled", "D_pts_pred")):
        X = Xpool if name != "ridge_fcs_individual" else Xind
        d = X[X["season"].isin(seasons) & ~X["post"] & X["fbs_game"] & X["week"].between(2, 4) & X[pred].notna()].copy()
        played = T[(T["season"].isin(seasons)) & T["vs_fcs"]][["season", "team_id", "week"]].rename(
            columns={"team_id": "opp_id", "week": "fcs_week"})
        d = d.merge(played, on=["season", "opp_id"], how="left")
        d["opp_played_fcs_before"] = d["fcs_week"].notna() & (d["fcs_week"] < d["week"])
        d = d.drop_duplicates(["game_id", "team_id"])
        for flag, g in d.groupby("opp_played_fcs_before"):
            e = g[pred] - g["pts"]
            rows.append({"model": name, "defence_played_fcs_earlier": bool(flag), "n": int(len(g)),
                         "bias_pts_allowed": float(e.mean()), "mae": float(e.abs().mean())})
    return pd.DataFrame(rows)


def pace_confound(T: pd.DataFrame, SE: pd.DataFrame, seasons, week: int = 8):
    """Points-per-game defence vs points-per-drive defence: is the gap explained by the team's own
    offensive tempo (fewer plays -> fewer opponent possessions -> fewer points allowed per game)?"""
    rows = []
    for s in seasons:
        hist = T[(T.season == s) & (T.week < week) & ~T.post & T.fbs_game].copy()
        if hist.empty:
            continue
        opp_drives = hist.set_index(["game_id", "team_id"])["drives"]
        g = hist.groupby("opp_id").agg(ppg=("pts", "mean"), pts=("pts", "sum"), drives=("drives", "sum"))
        g["ppd"] = g["pts"] / g["drives"]
        own = hist.groupby("team_id").agg(own_plays=("plays", "mean"), own_poss=("poss_sec", "mean"))
        m = g.join(own, how="inner")
        m["rank_ppg"] = m["ppg"].rank()
        m["rank_ppd"] = m["ppd"].rank()
        m["rank_gap"] = m["rank_ppg"] - m["rank_ppd"]   # negative = looks better per game than per drive
        rows.append({"season": s, "n": int(len(m)),
                     "corr_rankgap_ownplays": float(stats.spearmanr(m["rank_gap"], m["own_plays"]).statistic),
                     "corr_rankgap_ownposs": float(stats.spearmanr(m["rank_gap"], m["own_poss"]).statistic),
                     "mean_abs_rank_gap": float(m["rank_gap"].abs().mean())})
    return pd.DataFrame(rows)
