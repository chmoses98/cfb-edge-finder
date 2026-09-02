"""Preseason (season-level) features for team T entering season S.

Every value is dated: it must be derivable from information that existed
before season S's first game.

  talent_S              /talent year=S           settled in the S-1 cycle
  recruit_S..S-3        /recruiting/teams        class of year Y signs in Y-1 cycle
  ret_*                 /player/returning year=S published preseason S, describes S-1
  coach_change          /coaches S vs S-1        hires are public before S
  coach_tenure          consecutive prior seasons with the same head coach
  prev_*                S-1 season aggregates (record, margins) and S-1-only ridge strength
  prev2_margin_strength S-2-only ridge strength
  sp_prev_*             /ratings/sp year=S-1     END-OF-SEASON S-1 rating, published before S
  poll_pre_*            /rankings S week 1       preseason AP / Coaches poll points (0 = unranked)
  fbs_new               not in /teams/fbs for S-1
  conference            /teams/fbs year=S

`/rankings` week 1 = preseason poll is VERIFIED by `verify_poll_timing`
(week-1 poll must differ from week-2 poll and match the known preseason
ordering property that no game has yet been played).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from cfb_edge_finder.research.v2.cache import V2Cache
from cfb_edge_finder.research.v2.ratings import FCS_ID, RidgeMarginRatings
from cfb_edge_finder.research.v2.state import StateConfig

RET_FIELDS = ("percentPPA", "percentPassingPPA", "percentRushingPPA", "percentReceivingPPA", "usage",
              "passingUsage", "totalPPA")


def _poll_points(cache: V2Cache, season: int, poll_name: str, week: int = 1) -> dict[str, float]:
    out: dict[str, float] = {}
    for r in cache.load(season, "rankings"):
        if r.get("week") != week or r.get("seasonType") != "regular":
            continue
        for p in r.get("polls") or []:
            if p.get("poll") != poll_name:
                continue
            for rk in p.get("ranks") or []:
                out[rk.get("school")] = float(rk.get("points") or 0.0)
    return out


def verify_poll_timing(cache: V2Cache, season: int) -> dict:
    """Week-1 poll should be the preseason poll: report top-5 of week 1 and
    week 2 so the report can show they differ (games were played between)."""
    w1 = _poll_points(cache, season, "AP Top 25", 1)
    w2 = _poll_points(cache, season, "AP Top 25", 2)
    top = lambda d: [k for k, _ in sorted(d.items(), key=lambda kv: -kv[1])[:5]]  # noqa: E731
    return {"season": season, "week1_top5": top(w1), "week2_top5": top(w2), "differs": top(w1) != top(w2)}


def build_preseason_table(cache: V2Cache, *, games_all: pd.DataFrame, seasons: list[int],
                          long_hist: pd.DataFrame, state_cfg: StateConfig) -> pd.DataFrame:
    rows = []
    talent = {s: {r["team"]: r.get("talent") for r in cache.load(s, "talent") if r.get("team")} for s in seasons}
    recruit = {s: {r["team"]: r.get("points") for r in cache.load(s, "recruiting_teams") if r.get("team")}
               for s in seasons}
    returning = {s: {r["team"]: r for r in cache.load(s, "returning_production") if r.get("team")} for s in seasons}
    coaches = {s: {r["school"]: r.get("coach") for r in cache.load(s, "coaches") if r.get("school")} for s in seasons}
    sp = {s: {r["team"]: r for r in cache.load(s, "ratings_sp") if r.get("team")} for s in seasons}
    fbs = {s: {t["school"]: t for t in cache.load(s, "teams_fbs")} for s in seasons}
    ap = {s: _poll_points(cache, s, "AP Top 25") for s in seasons}
    coaches_poll = {s: _poll_points(cache, s, "Coaches Poll") for s in seasons}

    # prior-season-only strengths (S-1 games only, no decay) and S-2 only
    strength_by_season: dict[int, RidgeMarginRatings] = {}
    pts_by_season: dict[int, pd.DataFrame] = {}
    g = games_all[games_all.completed]
    for s in seasons:
        gs = g[g.season == s]
        if len(gs) == 0:
            continue
        strength_by_season[s] = RidgeMarginRatings(lam=state_cfg.lam, fcs_lam=state_cfg.fcs_lam).fit(gs)
        hs = pd.DataFrame({"team": gs.home, "pf": gs.home_points, "pa": gs.away_points, "w": (gs.margin > 0)})
        as_ = pd.DataFrame({"team": gs.away, "pf": gs.away_points, "pa": gs.home_points, "w": (gs.margin < 0)})
        both = pd.concat([hs, as_])
        pts_by_season[s] = both.groupby("team").agg(games=("pf", "size"), pf=("pf", "mean"), pa=("pa", "mean"),
                                                    win_pct=("w", "mean"))

    for s in seasons:
        teams = set(fbs.get(s, {}))
        if not teams:
            teams = set(games_all[games_all.season == s].home) | set(games_all[games_all.season == s].away)
        for t in sorted(teams):
            r: dict = {"season": s, "team": t}
            r["talent"] = talent.get(s, {}).get(t)
            rec = [recruit.get(s - k, {}).get(t) for k in range(4)]
            r["recruit_0"], r["recruit_1"], r["recruit_2"], r["recruit_3"] = rec
            vals = [v for v in rec if v is not None]
            r["recruit_avg4"] = float(np.mean(vals)) if vals else None
            ret = returning.get(s, {}).get(t, {})
            for f in RET_FIELDS:
                r[f"ret_{f}"] = ret.get(f)
            c_now, c_prev = coaches.get(s, {}).get(t), coaches.get(s - 1, {}).get(t)
            r["coach_change"] = None if (c_now is None or c_prev is None) else float(c_now != c_prev)
            tenure = 0
            k = 1
            while c_now is not None and coaches.get(s - k, {}).get(t) == c_now:
                tenure += 1
                k += 1
            r["coach_tenure"] = tenure
            prev = pts_by_season.get(s - 1)
            if prev is not None and t in prev.index:
                r["prev_games"] = float(prev.at[t, "games"])
                r["prev_pf"] = float(prev.at[t, "pf"])
                r["prev_pa"] = float(prev.at[t, "pa"])
                r["prev_win_pct"] = float(prev.at[t, "win_pct"])
            else:
                r["prev_games"] = 0.0
                r["prev_pf"] = r["prev_pa"] = r["prev_win_pct"] = None
            sm1 = strength_by_season.get(s - 1)
            sm2 = strength_by_season.get(s - 2)
            r["prev_margin_strength"] = sm1.strength.get(t) if sm1 else None
            r["prev2_margin_strength"] = sm2.strength.get(t) if sm2 else None
            spr = sp.get(s - 1, {}).get(t, {})
            r["sp_prev_rating"] = spr.get("rating")
            r["sp_prev_off"] = (spr.get("offense") or {}).get("rating")
            r["sp_prev_def"] = (spr.get("defense") or {}).get("rating")
            r["poll_pre_ap"] = ap.get(s, {}).get(t, 0.0)
            r["poll_pre_coaches"] = coaches_poll.get(s, {}).get(t, 0.0)
            r["fbs_new"] = float(t not in fbs.get(s - 1, {})) if fbs.get(s - 1) else None
            r["conference"] = fbs.get(s, {}).get(t, {}).get("conference")
            rows.append(r)
    df = pd.DataFrame(rows)
    # pooled FCS row so FCS sides have a defined (neutral) preseason vector
    for s in seasons:
        df.loc[len(df)] = {"season": s, "team": FCS_ID}
    return df
