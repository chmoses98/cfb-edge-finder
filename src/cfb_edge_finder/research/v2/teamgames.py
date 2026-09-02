"""Team-game statistics table: one row per (game_id, team) built from
/stats/game/advanced (+ garbage-time-excluded variant), /games/teams box
scores and /drives.

EVERY column here is POSTGAME for its own game. The table is only ever
consumed by `state.py`, which restricts to games strictly before an
as-of point. Nothing in this module knows about as-of points on purpose:
it is a pure extraction step.

Column naming: `o_*` = this team's offense in this game, `d_*` = this
team's defense (i.e. the opponent's offense against it). Box-score
categories are parsed from CFBD's string "stat" values.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

ADV_METRICS = {
    "ppa": ("ppa",),
    "sr": ("successRate",),
    "expl": ("explosiveness",),
    "plays": ("plays",),
    "drives": ("drives",),
    "line_yds": ("lineYards",),
    "sec_lvl": ("secondLevelYards",),
    "open_fld": ("openFieldYards",),
    "power_sr": ("powerSuccess",),
    "stuff": ("stuffRate",),
    "pass_ppa": ("passingPlays", "ppa"),
    "pass_sr": ("passingPlays", "successRate"),
    "pass_expl": ("passingPlays", "explosiveness"),
    "rush_ppa": ("rushingPlays", "ppa"),
    "rush_sr": ("rushingPlays", "successRate"),
    "rush_expl": ("rushingPlays", "explosiveness"),
    "sd_ppa": ("standardDowns", "ppa"),
    "sd_sr": ("standardDowns", "successRate"),
    "pd_ppa": ("passingDowns", "ppa"),
    "pd_sr": ("passingDowns", "successRate"),
    "total_ppa": ("totalPPA",),
}


def _dig(d: dict, path: tuple[str, ...]):
    cur = d
    for k in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(k)
    return cur


def advanced_rows(raw: list[dict], *, suffix: str = "") -> pd.DataFrame:
    out = []
    for r in raw:
        if not isinstance(r, dict):
            continue
        row = {"game_id": str(r.get("gameId")), "team": r.get("team"), "opponent": r.get("opponent")}
        off = r.get("offense") or {}
        de = r.get("defense") or {}
        for name, path in ADV_METRICS.items():
            row[f"o_{name}{suffix}"] = _dig(off, path)
            row[f"d_{name}{suffix}"] = _dig(de, path)
        out.append(row)
    df = pd.DataFrame(out)
    if len(df):
        df = df.drop_duplicates(["game_id", "team"])
    return df


def _ratio(s: str | None) -> tuple[float | None, float | None]:
    if not s or "-" not in str(s):
        return None, None
    a, b = str(s).split("-", 1)
    try:
        return float(a), float(b)
    except ValueError:
        return None, None


def _mmss(s: str | None) -> float | None:
    if not s or ":" not in str(s):
        return None
    m, sec = str(s).split(":", 1)
    try:
        return float(m) * 60 + float(sec)
    except ValueError:
        return None


def _num(s) -> float | None:
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def box_rows(raw: list[dict]) -> pd.DataFrame:
    out = []
    for g in raw:
        if not isinstance(g, dict):
            continue
        gid = str(g.get("id"))
        for t in g.get("teams") or []:
            stats = {s.get("category"): s.get("stat") for s in (t.get("stats") or [])}
            row = {
                "game_id": gid, "team": t.get("team"), "home_away": t.get("homeAway"),
                "team_id_cfbd": t.get("teamId"),
            }
            row["b_total_yds"] = _num(stats.get("totalYards"))
            row["b_pass_yds"] = _num(stats.get("netPassingYards"))
            row["b_rush_yds"] = _num(stats.get("rushingYards"))
            row["b_rush_att"] = _num(stats.get("rushingAttempts"))
            comp, att = _ratio(stats.get("completionAttempts"))
            row["b_pass_comp"], row["b_pass_att"] = comp, att
            row["b_turnovers"] = _num(stats.get("turnovers"))
            row["b_fumbles_lost"] = _num(stats.get("fumblesLost"))
            row["b_int_thrown"] = _num(stats.get("interceptions"))
            row["b_sacks_def"] = _num(stats.get("sacks"))
            row["b_tfl_def"] = _num(stats.get("tacklesForLoss"))
            row["b_pd_def"] = _num(stats.get("passesDeflected"))
            row["b_qbh_def"] = _num(stats.get("qbHurries"))
            row["b_int_def"] = _num(stats.get("passesIntercepted"))
            row["b_first_downs"] = _num(stats.get("firstDowns"))
            c3, a3 = _ratio(stats.get("thirdDownEff"))
            row["b_3d_conv"], row["b_3d_att"] = c3, a3
            c4, a4 = _ratio(stats.get("fourthDownEff"))
            row["b_4d_conv"], row["b_4d_att"] = c4, a4
            pn, py = _ratio(stats.get("totalPenaltiesYards"))
            row["b_pen_n"], row["b_pen_yds"] = pn, py
            row["b_poss_sec"] = _mmss(stats.get("possessionTime"))
            row["b_kick_pts"] = _num(stats.get("kickingPoints"))
            row["b_points"] = _num(t.get("points"))
            out.append(row)
    df = pd.DataFrame(out)
    if len(df):
        df = df.drop_duplicates(["game_id", "team"])
    return df


TURNOVER_RESULTS = {"INT", "FUMBLE", "INT TD", "FUMBLE RETURN TD", "FUMBLE TD"}
SCORE_TD = {"TD", "PUNT TD", "DOWNS TD", "MISSED FG TD"}  # PUNT TD etc. are defensive scores; handled below
OFFENSE_TD = {"TD"}
OFFENSE_FG = {"FG"}


def drive_rows(raw: list[dict]) -> pd.DataFrame:
    """Per (game, offense team) drive aggregates."""
    if not raw:
        return pd.DataFrame()
    d = pd.DataFrame(raw)
    d["game_id"] = d["gameId"].astype(str)
    res = d["driveResult"].fillna("")
    d["is_td"] = res.isin(OFFENSE_TD)
    d["is_fg"] = res.isin(OFFENSE_FG)
    d["is_to"] = res.isin(TURNOVER_RESULTS)
    d["is_punt"] = res.str.startswith("PUNT")
    plays = pd.to_numeric(d["plays"], errors="coerce")
    d["three_out"] = d["is_punt"] & (plays <= 3)
    start_ytg = pd.to_numeric(d["startYardsToGoal"], errors="coerce")
    end_ytg = pd.to_numeric(d["endYardsToGoal"], errors="coerce")
    d["opportunity"] = (start_ytg <= 40) | (end_ytg <= 40) | d["is_td"]
    d["opp_points"] = np.where(d["is_td"], 7.0, np.where(d["is_fg"], 3.0, 0.0)) * d["opportunity"].astype(float)
    d["yards"] = pd.to_numeric(d["yards"], errors="coerce")
    d["plays"] = plays
    d["start_ytg"] = start_ytg
    # tempo: elapsed seconds per play
    el = d["elapsed"].apply(lambda e: (e or {}).get("minutes", 0) * 60 + (e or {}).get("seconds", 0)
                           if isinstance(e, dict) else np.nan)
    d["elapsed_sec"] = pd.to_numeric(el, errors="coerce")
    real = ~res.isin({"END OF GAME", "END OF HALF", "END OF 4TH QUARTER", "Uncategorized"})
    dr = d[real]
    g = dr.groupby(["game_id", "offense"])
    agg = pd.DataFrame({
        "dr_n": g.size(),
        "dr_td": g["is_td"].sum(),
        "dr_fg": g["is_fg"].sum(),
        "dr_to": g["is_to"].sum(),
        "dr_3out": g["three_out"].sum(),
        "dr_opp": g["opportunity"].sum(),
        "dr_opp_pts": g["opp_points"].sum(),
        "dr_start_ytg": g["start_ytg"].mean(),
        "dr_yards": g["yards"].sum(),
        "dr_plays": g["plays"].sum(),
        "dr_elapsed": g["elapsed_sec"].sum(),
    }).reset_index().rename(columns={"offense": "team"})
    agg["dr_pts_per_drive"] = (7 * agg["dr_td"] + 3 * agg["dr_fg"]) / agg["dr_n"].replace(0, np.nan)
    agg["dr_ppo"] = agg["dr_opp_pts"] / agg["dr_opp"].replace(0, np.nan)
    agg["dr_to_rate"] = agg["dr_to"] / agg["dr_n"].replace(0, np.nan)
    agg["dr_3out_rate"] = agg["dr_3out"] / agg["dr_n"].replace(0, np.nan)
    agg["dr_sec_per_play"] = agg["dr_elapsed"] / agg["dr_plays"].replace(0, np.nan)
    return agg


def build_team_game_table(cache, seasons: list[int]) -> pd.DataFrame:
    """Merge advanced (+nogarbage), box, and drive aggregates for `seasons`.
    Returns one row per (game_id, team) with an `opponent` column where
    known. Defensive drive metrics are attached by joining the opponent's
    offensive drive row."""
    frames = []
    for s in seasons:
        adv = pd.concat([advanced_rows(cache.load(s, "advanced_regular")),
                         advanced_rows(cache.load(s, "advanced_postseason"))], ignore_index=True)
        if len(adv):
            adv = adv.drop_duplicates(["game_id", "team"])
        ng = advanced_rows(cache.load(s, "advanced_regular_nogarbage"), suffix="_ng")
        if len(ng):
            keep = ["game_id", "team"] + [c for c in ng.columns if c.endswith("_ng")]
            adv = adv.merge(ng[keep], on=["game_id", "team"], how="left") if len(adv) else ng[keep]
        box = box_rows(cache.load(s, "games_teams"))
        drv = drive_rows(cache.load(s, "drives_regular") + cache.load(s, "drives_postseason"))
        df = adv
        if len(box):
            df = df.merge(box, on=["game_id", "team"], how="outer") if len(df) else box
        if len(drv):
            df = df.merge(drv, on=["game_id", "team"], how="left") if len(df) else drv
        df["season"] = s
        frames.append(df)
    tg = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
    if not len(tg):
        return tg
    # defensive drive metrics = opponent's offensive drive row in the same game
    if "opponent" in tg.columns and "dr_n" in tg.columns:
        opp = tg[["game_id", "team", "dr_pts_per_drive", "dr_ppo", "dr_to_rate", "dr_3out_rate", "dr_start_ytg",
                  "dr_sec_per_play", "dr_n"]].rename(columns={
            "team": "opponent", "dr_pts_per_drive": "ddr_pts_per_drive", "dr_ppo": "ddr_ppo",
            "dr_to_rate": "ddr_to_rate", "dr_3out_rate": "ddr_3out_rate", "dr_start_ytg": "ddr_start_ytg",
            "dr_sec_per_play": "ddr_sec_per_play", "dr_n": "ddr_n"})
        tg = tg.merge(opp, on=["game_id", "opponent"], how="left")
    return tg
