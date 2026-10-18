"""Team-game, game and player-game frames for 2014-2026.

Sources (all read from the gitignored cache, see paths.py):
  * CFBD cache on the research-data branch, 2014-2025: games, team box (games_teams), advanced game
    stats with and without garbage time (EPA/play = CFBD "ppa", success rate, explosiveness, drives),
    closing lines (several providers), talent, recruiting, returning production, coaches, FBS list.
  * 2026: the production ESPN game log committed at data/football/2026/team_games.jsonl (box + drive
    aggregates; no EPA) and data/football/2026/schedule.json.
  * ESPN player box + ESPN closing lines from sportsdataverse-data releases, 2014-2026.

Every frame carries `src` so mixed-source seasons are visible. CFBD and ESPN game/team ids are the
same id space (ESPN ids); team names differ and are never used as join keys across sources.

Plays definition: box rush attempts + pass attempts (NCAA box: sacks are rushes). This is the
definition available in BOTH sources (the base-repair source-contract check found CFBD advanced
plays - box attempts = +0.74 per team-game), so a 2026 team-game is on the same scale as history.
"""
from __future__ import annotations

import gzip
import json
from functools import lru_cache

import numpy as np
import pandas as pd

from . import paths

REG_WEEK_POST = 30   # block offset for postseason games (after every regular-season week)


def _gz(season: int, name: str):
    p = paths.CFBD / str(season) / f"{name}.json.gz"
    if not p.exists():
        return []
    with gzip.open(p) as f:
        return json.load(f)


def _f(x):
    try:
        return float(x)
    except (TypeError, ValueError):
        return np.nan


def _split(x, i):
    try:
        return float(str(x).split("-")[i])
    except (TypeError, ValueError, IndexError):
        return np.nan


def _mmss(x):
    try:
        m, s = str(x).split(":")
        return 60 * float(m) + float(s)
    except (TypeError, ValueError):
        return np.nan


# --------------------------------------------------------------------------- CFBD seasons
def _cfbd_season(season: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    games = [g for g in _gz(season, "games") if g.get("completed") and g.get("homePoints") is not None
             and g.get("seasonType") in ("regular", "postseason")]
    grow = []
    for g in games:
        grow.append({
            "game_id": str(g["id"]), "season": season, "week": int(g["week"]),
            "season_type": g["seasonType"], "kickoff": g["startDate"],
            "home_id": str(g["homeId"]), "away_id": str(g["awayId"]),
            "home": g["homeTeam"], "away": g["awayTeam"],
            "home_div": g.get("homeClassification") or "unknown", "away_div": g.get("awayClassification") or "unknown",
            "home_conf": g.get("homeConference"), "away_conf": g.get("awayConference"),
            "neutral": bool(g.get("neutralSite")), "conf_game": bool(g.get("conferenceGame")),
            "home_pts": float(g["homePoints"]), "away_pts": float(g["awayPoints"]),
            "venue_id": g.get("venueId"), "src": "cfbd",
        })
    G = pd.DataFrame(grow)
    if G.empty:
        return G, pd.DataFrame()
    name2id = {}
    for g in games:
        name2id[(str(g["id"]), g["homeTeam"])] = str(g["homeId"])
        name2id[(str(g["id"]), g["awayTeam"])] = str(g["awayId"])
    # team box
    box = {}
    for g in _gz(season, "games_teams"):
        for t in g.get("teams") or []:
            st = {s["category"]: s["stat"] for s in t.get("stats") or []}
            tid = str(t.get("teamId") or name2id.get((str(g["id"]), t.get("team")), ""))
            if not tid:
                continue
            box[(str(g["id"]), tid)] = {
                "rush_att": _f(st.get("rushingAttempts")), "rush_yds": _f(st.get("rushingYards")),
                "pass_cmp": _split(st.get("completionAttempts"), 0), "pass_att": _split(st.get("completionAttempts"), 1),
                "pass_yds": _f(st.get("netPassingYards")), "total_yds": _f(st.get("totalYards")),
                "turnovers": _f(st.get("turnovers")), "poss_sec": _mmss(st.get("possessionTime")),
                "first_downs": _f(st.get("firstDowns")),
            }
    adv = {}
    for name, suf in (("advanced_regular", ""), ("advanced_postseason", ""), ("advanced_regular_nogarbage", "_ng")):
        for a in _gz(season, name):
            tid = name2id.get((str(a["gameId"]), a.get("team")))
            if not tid:
                continue
            o = a.get("offense") or {}
            d = adv.setdefault((str(a["gameId"]), tid), {})
            d["epa" + suf] = _f(o.get("ppa"))
            d["sr" + suf] = _f(o.get("successRate"))
            d["expl" + suf] = _f(o.get("explosiveness"))
            d["adv_plays" + suf] = _f(o.get("plays"))
            if not suf:
                d["drives"] = _f(o.get("drives"))
                d["pass_epa"] = _f((o.get("passingPlays") or {}).get("ppa"))
                d["rush_epa"] = _f((o.get("rushingPlays") or {}).get("ppa"))
    T = _team_rows(G)
    keys = list(zip(T["game_id"], T["team_id"]))
    B = pd.DataFrame([box.get(k, {}) for k in keys], index=T.index)
    A = pd.DataFrame([adv.get(k, {}) for k in keys], index=T.index)
    T = pd.concat([T, B, A], axis=1)
    return G, T


def _team_rows(G: pd.DataFrame) -> pd.DataFrame:
    """Two rows per game: the team's offence vs the opponent's defence."""
    out = []
    for side, opp in (("home", "away"), ("away", "home")):
        d = pd.DataFrame({
            "game_id": G["game_id"], "season": G["season"], "week": G["week"],
            "season_type": G["season_type"], "kickoff": G["kickoff"],
            "team_id": G[f"{side}_id"], "team": G[side], "opp_id": G[f"{opp}_id"], "opp": G[opp],
            "team_div": G[f"{side}_div"], "opp_div": G[f"{opp}_div"],
            "team_conf": G[f"{side}_conf"], "opp_conf": G[f"{opp}_conf"],
            "is_home_side": side == "home",
            "site": np.where(G["neutral"], 0, 1 if side == "home" else -1),
            "neutral": G["neutral"], "conf_game": G["conf_game"],
            "pts": G[f"{side}_pts"], "pts_allowed": G[f"{opp}_pts"], "src": G["src"],
        })
        out.append(d)
    return pd.concat(out, ignore_index=True)


# --------------------------------------------------------------------------- 2026 ESPN log
def _espn_2026() -> tuple[pd.DataFrame, pd.DataFrame]:
    rows = [json.loads(x) for x in open(paths.FOOTBALL_2026 / "team_games.jsonl")]
    sched = json.load(open(paths.FOOTBALL_2026 / "schedule.json"))
    ev = {e["id"]: e for e in sched["events"]}
    by_game: dict[str, dict] = {}
    for r in rows:
        if r.get("points_for") is None:
            continue
        by_game.setdefault(r["game_id"], {})[r["team_id"]] = r
    grow, trow = [], []
    for gid, pair in by_game.items():
        if len(pair) != 2:
            continue
        e = ev.get(gid, {})
        comp = {c["team_id"]: c for c in e.get("competitors", [])}
        ids = list(pair)
        home = next((t for t in ids if comp.get(t, {}).get("home_away") == "home"), None)
        if home is None:  # fall back on the log's own site field
            home = next((t for t in ids if pair[t].get("site") == "home"), ids[0])
        away = [t for t in ids if t != home][0]
        h, a = pair[home], pair[away]
        neutral = bool(e.get("neutral_site")) if e else h.get("site") == "neutral"
        grow.append({"game_id": gid, "season": 2026, "week": int(h["week"]), "season_type": "regular",
                     "kickoff": h["kickoff_utc"], "home_id": home, "away_id": away, "home": h["team"], "away": a["team"],
                     "home_div": h.get("team_division") or "unknown", "away_div": a.get("team_division") or "unknown",
                     "home_conf": comp.get(home, {}).get("conference_id"), "away_conf": comp.get(away, {}).get("conference_id"),
                     "neutral": neutral, "conf_game": bool(e.get("conference_game")),
                     "home_pts": float(h["points_for"]), "away_pts": float(a["points_for"]), "venue_id": None, "src": "espn"})
        for t in (home, away):
            r = pair[t]
            b = r.get("box") or {}
            p = r.get("pbp") or {}
            sp = p.get("scrim_plays")
            trow.append({"game_id": gid, "team_id": t,
                         "rush_att": _f(b.get("rush_att")), "rush_yds": _f(b.get("rush_yards")),
                         "pass_cmp": _f(b.get("pass_cmp")), "pass_att": _f(b.get("pass_att")),
                         "pass_yds": _f(b.get("pass_yards")), "total_yds": _f(b.get("total_yards")),
                         "turnovers": _f(b.get("turnovers")), "poss_sec": _f(b.get("possession_seconds")),
                         "first_downs": _f(b.get("first_downs")),
                         "sr": (_f(p.get("success_plays")) / sp) if sp else np.nan,
                         "drives": _f(p.get("drives")), "primary_passer": r.get("primary_passer")})
    G = pd.DataFrame(grow)
    T = _team_rows(G).merge(pd.DataFrame(trow), on=["game_id", "team_id"], how="left")
    return G, T


# --------------------------------------------------------------------------- lines
def _cfbd_lines(season: int) -> pd.DataFrame:
    prio = ["consensus", "ESPN Bet", "DraftKings", "Draft Kings", "Bovada", "Caesars", "William Hill (New Jersey)",
            "teamrankings", "numberfire"]
    out = []
    for name in ("lines_regular", "lines_postseason"):
        for g in _gz(season, name):
            ls = [x for x in g.get("lines") or [] if x.get("spread") is not None or x.get("overUnder") is not None]
            if not ls:
                continue
            ls.sort(key=lambda x: prio.index(x["provider"]) if x["provider"] in prio else 99)
            sp = [x["spread"] for x in ls if x.get("spread") is not None]
            tot = [x["overUnder"] for x in ls if x.get("overUnder") is not None]
            out.append({"game_id": str(g["id"]),
                        "close_spread": float(np.median(sp)) if sp else np.nan,
                        "close_total": float(np.median(tot)) if tot else np.nan,
                        "line_provider": ls[0]["provider"], "n_providers": len(ls)})
    return pd.DataFrame(out)


def _espn_lines(season: int) -> pd.DataFrame:
    p = paths.SDV / "betting" / f"betting_{season}.parquet"
    if not p.exists():
        return pd.DataFrame()
    d = pd.read_parquet(p)
    d = d[d["game_spread_available"].fillna(False)]
    return pd.DataFrame({"game_id": d["game_id"].astype(str), "close_spread": d["home_team_spread"].astype(float),
                         "close_total": d["over_under"].astype(float), "line_provider": "espn_core_odds",
                         "n_providers": 1})


# --------------------------------------------------------------------------- preseason inputs
def preseason_inputs() -> pd.DataFrame:
    """Per (season, team_id): talent composite, 4-yr recruiting points, returning production, new coach.
    Keyed by CFBD team name -> id via the season's FBS team list (and prior seasons for 2026)."""
    name2id = {}
    for s in paths.SEASONS:
        for t in _gz(s, "teams_fbs"):
            name2id[t["school"]] = str(t["id"])
        for g in _gz(s, "games"):
            name2id.setdefault(g["homeTeam"], str(g["homeId"]))
            name2id.setdefault(g["awayTeam"], str(g["awayId"]))
    rows = {}

    def put(season, team, **kw):
        tid = name2id.get(team)
        if tid:
            rows.setdefault((season, tid), {"season": season, "team_id": tid}).update(kw)

    rec = {}
    for s in paths.SEASONS:
        for r in _gz(s, "recruiting_teams"):
            rec[(s, r["team"])] = r.get("points")
    coach = {}
    for s in paths.SEASONS + [paths.CURRENT]:
        if s == paths.CURRENT:
            p = paths.PRESEASON / f"{s}.json"
            if not p.exists():
                continue
            pre = json.load(open(p))
            tal, ret, co = pre.get("talent", []), pre.get("returning_production", []), pre.get("coaches", [])
        else:
            tal, ret, co = _gz(s, "talent"), _gz(s, "returning_production"), _gz(s, "coaches")
        for t in tal:
            put(s, t["team"], talent=_f(t.get("talent")))
        for r in ret:
            put(s, r["team"], ret_ppa=_f(r.get("percentPPA")), ret_usage=_f(r.get("usage")),
                ret_pass=_f(r.get("percentPassingPPA")), ret_rush=_f(r.get("percentRushingPPA")),
                ret_recv=_f(r.get("percentReceivingPPA")))
        for c in co:
            coach[(s, c["school"])] = c.get("coach")
    for (s, team), c in coach.items():
        prev = coach.get((s - 1, team))
        if prev is not None:
            put(s, team, new_coach=float(prev != c))
    teams_rec = {t for (_, t) in rec}
    for s in paths.SEASONS + [paths.CURRENT]:
        for team in teams_rec:
            # 4 classes ending with season s; for 2026 (class not cached) the 3 classes 2023-2025 (documented)
            pts = [rec.get((y, team)) for y in range(s - 3, s + 1)]
            pts = [p for p in pts if p is not None]
            if pts:
                put(s, team, recruit4=float(np.mean(pts)))
    df = pd.DataFrame(list(rows.values()))
    return df


# --------------------------------------------------------------------------- assembly
@lru_cache(maxsize=1)
def load(include_2026: bool = True) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (games, team_games). Cached in .cache/built as parquet."""
    paths.BUILT.mkdir(parents=True, exist_ok=True)
    fg, ft = paths.BUILT / "games.parquet", paths.BUILT / "team_games.parquet"
    src_stamp = paths.BUILT / "stamp.json"
    stamp = {"log_mtime": (paths.FOOTBALL_2026 / "team_games.jsonl").stat().st_mtime, "v": 3}
    if fg.exists() and ft.exists() and src_stamp.exists() and json.load(open(src_stamp)) == stamp:
        G, T = pd.read_parquet(fg), pd.read_parquet(ft)
    else:
        Gs, Ts, Ls = [], [], []
        for s in paths.SEASONS:
            g, t = _cfbd_season(s)
            Gs.append(g)
            Ts.append(t)
            Ls.append(_cfbd_lines(s))
        g, t = _espn_2026()
        Gs.append(g)
        Ts.append(t)
        Ls.append(_espn_lines(2026))
        G = pd.concat(Gs, ignore_index=True)
        T = pd.concat(Ts, ignore_index=True)
        L = pd.concat(Ls, ignore_index=True).drop_duplicates("game_id")
        G = G.merge(L, on="game_id", how="left")
        G.to_parquet(fg)
        T.to_parquet(ft)
        json.dump(stamp, open(src_stamp, "w"))
    if not include_2026:
        G, T = G[G.season < 2026], T[T.season < 2026]
    return _finish(G.copy(), T.copy())


def _finish(G: pd.DataFrame, T: pd.DataFrame):
    for d in (G, T):
        d["kickoff"] = pd.to_datetime(d["kickoff"], utc=True, format="ISO8601")
        d["post"] = d["season_type"] == "postseason"
        d["block"] = d["season"] * 100 + np.where(d["post"], REG_WEEK_POST, d["week"])
    T["plays"] = T["rush_att"] + T["pass_att"]
    T.loc[~(T["plays"] > 20), "plays"] = np.nan          # malformed / partial box
    T["ppp"] = T["pts"] / T["plays"]
    T["ypp"] = T["total_yds"] / T["plays"]
    T["fbs_game"] = (T["team_div"] == "fbs") & (T["opp_div"] == "fbs")
    T["vs_fcs"] = (T["team_div"] == "fbs") & (T["opp_div"] != "fbs")
    T["is_fcs"] = T["team_div"] != "fbs"
    G["total"] = G["home_pts"] + G["away_pts"]
    G["margin"] = G["home_pts"] - G["away_pts"]
    G["fbs_game"] = (G["home_div"] == "fbs") & (G["away_div"] == "fbs")
    # market-implied team points from the closing spread/total (home spread: negative = home favoured)
    G["mkt_home_pts"] = G["close_total"] / 2 - G["close_spread"] / 2
    G["mkt_away_pts"] = G["close_total"] / 2 + G["close_spread"] / 2
    G["mkt_margin"] = -G["close_spread"]
    G = G.sort_values(["kickoff", "game_id"]).reset_index(drop=True)
    T = T.sort_values(["kickoff", "game_id", "is_home_side"], ascending=[True, True, False]).reset_index(drop=True)
    return G, T


# --------------------------------------------------------------------------- players
def load_players() -> pd.DataFrame:
    """Player-game rows (passing, rushing, receiving) from the ESPN player box release.

    Only players who recorded the stat appear; absent rows are NOT zero-filled here (see players.py)."""
    fp = paths.BUILT / "player_games.parquet"
    if fp.exists() and fp.stat().st_mtime > max(p.stat().st_mtime for p in (paths.SDV / "player_box").glob("*.parquet")):
        return pd.read_parquet(fp)
    frames = []
    for p in sorted((paths.SDV / "player_box").glob("player_box_*.parquet")):
        d = pd.read_parquet(p)
        d = d[d["category"].isin(["passing", "rushing", "receiving"])]
        keep = pd.DataFrame({"game_id": d["game_id"].astype(str), "team_id": d["team_id"].astype(str),
                             "player_id": d["athlete_id"].astype(str), "name": d["athlete_name"],
                             "category": d["category"], "season": d["season"].astype(int)})
        if "completions/passingAttempts" in d:
            ca = d["completions/passingAttempts"].astype(str)
        else:
            ca = d["stat_1"].astype(str)
        keep["pass_cmp"] = np.where(d["category"] == "passing", pd.to_numeric(ca.str.split("/").str[0], errors="coerce"), np.nan)
        keep["pass_att"] = np.where(d["category"] == "passing", pd.to_numeric(ca.str.split("/").str[1], errors="coerce"), np.nan)

        def col(name, idx):
            if name in d:
                v = pd.to_numeric(d[name], errors="coerce")
            else:
                v = pd.to_numeric(d[f"stat_{idx}"], errors="coerce")
            return v
        keep["pass_yds"] = np.where(d["category"] == "passing", col("passingYards", 2), np.nan)
        keep["carries"] = np.where(d["category"] == "rushing", col("rushingAttempts", 1), np.nan)
        keep["rush_yds"] = np.where(d["category"] == "rushing", col("rushingYards", 2), np.nan)
        keep["receptions"] = np.where(d["category"] == "receiving", col("receptions", 1), np.nan)
        keep["rec_yds"] = np.where(d["category"] == "receiving", col("receivingYards", 2), np.nan)
        frames.append(keep)
    P = pd.concat(frames, ignore_index=True)
    P = P[~P["player_id"].isin(["", "None", "nan"]) & ~P["name"].fillna("").str.upper().str.contains("TEAM")]
    agg = P.groupby(["game_id", "team_id", "player_id", "season"], as_index=False).agg(
        name=("name", "first"), pass_cmp=("pass_cmp", "sum"), pass_att=("pass_att", "sum"),
        pass_yds=("pass_yds", "sum"), carries=("carries", "sum"), rush_yds=("rush_yds", "sum"),
        receptions=("receptions", "sum"), rec_yds=("rec_yds", "sum"),
        has_pass=("pass_att", lambda s: s.notna().any()), has_rush=("carries", lambda s: s.notna().any()),
        has_rec=("receptions", lambda s: s.notna().any()))
    agg.to_parquet(fp)
    return agg


# --------------------------------------------------------------------------- upcoming (forward projections)
def week_end(now: pd.Timestamp) -> pd.Timestamp:
    """End of the current CFB week: the next Tuesday 08:00 UTC (weeks run Tuesday-Monday)."""
    days = (1 - now.weekday()) % 7 or 7
    return (now + pd.Timedelta(days=days)).normalize() + pd.Timedelta(hours=8)


def upcoming(now: pd.Timestamp, T: pd.DataFrame, horizon_end: pd.Timestamp | None = None,
             replay: bool = False) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Not-yet-played games from data/football/2026/schedule.json kicking off before the end of the current week.
    Outcome columns are NaN; division comes from the team's latest known division (unknown -> fcs);
    games without an FBS participant are dropped. week = last completed 2026 week + 1."""
    sched = json.load(open(paths.FOOTBALL_2026 / "schedule.json"))
    div = (T.sort_values("kickoff").drop_duplicates("team_id", keep="last").set_index("team_id")["team_div"].to_dict())
    last_week = int(T.loc[(T["season"] == 2026) & ~T["post"], "week"].max())
    rows = []
    for e in sched["events"]:
        if not replay and (e.get("completed") or e.get("state") == "post"):
            continue            # replay (tests/smoke runs with a past `now`): the schedule file is newer than `now`
        ko = pd.Timestamp(e["date"])
        ko = ko.tz_localize("UTC") if ko.tzinfo is None else ko.tz_convert("UTC")
        if not (now - pd.Timedelta(hours=1) <= ko <= (horizon_end or week_end(now))):
            continue
        comp = {c["home_away"]: c for c in e.get("competitors", [])}
        if "home" not in comp or "away" not in comp:
            continue
        h, a = comp["home"], comp["away"]
        hd, ad = div.get(h["team_id"], "fcs"), div.get(a["team_id"], "fcs")
        if hd != "fbs" and ad != "fbs":
            continue
        rows.append({"game_id": e["id"], "season": 2026, "week": last_week + 1, "season_type": "regular",
                     "kickoff": ko.strftime("%Y-%m-%dT%H:%M:%SZ"), "home_id": h["team_id"], "away_id": a["team_id"],
                     "home": h.get("location") or h["names"][0], "away": a.get("location") or a["names"][0],
                     "home_div": hd, "away_div": ad, "home_conf": h.get("conference_id"), "away_conf": a.get("conference_id"),
                     "neutral": bool(e.get("neutral_site")), "conf_game": bool(e.get("conference_game")),
                     "home_pts": np.nan, "away_pts": np.nan, "venue_id": None, "src": "espn_schedule"})
    Gu = pd.DataFrame(rows)
    if Gu.empty:
        return Gu, pd.DataFrame()
    Tu = _team_rows(Gu)
    for c in T.columns:
        if c not in Tu.columns:
            Tu[c] = np.nan
    Tu = Tu[[c for c in T.columns if c in Tu.columns]].copy()
    for c in ("close_spread", "close_total", "line_provider", "n_providers"):
        Gu[c] = np.nan
    Gu, Tu = _finish(Gu, Tu)
    return Gu, Tu
