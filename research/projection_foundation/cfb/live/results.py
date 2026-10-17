"""Final results -> sift.outcome.v1 rows, from FINAL games only.

Sources (reused, no new fetchers):
  data/football/<season>/team_games.jsonl   the repo's ESPN game log (script-engine job). One row per team per COMPLETED
                                            game (points_for/points_against, primary passer) - in-progress games are never
                                            in it, and a late stat correction rewrites the row, which pf_core.settle turns
                                            into a correction record (the original settlement is never touched).
  sportsdataverse espn_cfb_player_box       player passing/rushing/receiving lines (via cfb.data.load_players), used only for
                                            games that are final in the ESPN log.

Outcome conventions:
  team_points   entity = team_id, actual = points scored.      game_total entity TOTAL (home+away).   margin entity MARGIN
  (home minus away; ESPN's home team as listed on the schedule, same orientation as the forecast).
  passer families: participated = False when the projected passer recorded no passing line (his props void).
  rusher/receiver families: ESPN lists only players with a stat, so a candidate absent from the box is scored 0 and
  counted `zero_filled` (the same convention as the historical evaluation; DNP and zero cannot be told apart).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd

from . import records as rec

STAT_OF = {"passing_attempts": "pass_att", "passing_yards": "pass_yds", "carries": "carries",
           "rushing_yards": "rush_yds", "receptions": "receptions", "receiving_yards": "rec_yds"}
ROLE_OF = {"passing_attempts": "passer", "passing_yards": "passer", "carries": "rusher", "rushing_yards": "rusher",
           "receptions": "receiver", "receiving_yards": "receiver"}


def read_gamelog(path: str) -> list[dict]:
    return [json.loads(x) for x in open(path) if x.strip()]


def game_date(kickoff_utc: str) -> str:
    return kickoff_utc[:10]


def team_outcomes(rows: list[dict], schedule: dict, wanted_games: set[str] | None = None) -> tuple[list[dict], dict]:
    """team_points / game_total / margin outcomes for completed games (both team rows present)."""
    ev = {str(e["id"]): e for e in schedule.get("events", [])}
    by_game: dict[str, dict[str, dict]] = {}
    for r in rows:
        if r.get("points_for") is None:
            continue
        by_game.setdefault(str(r["game_id"]), {})[str(r["team_id"])] = r
    out, stats = [], {"games_final": 0, "games_one_row": 0, "not_completed_in_schedule": 0}
    for gid, pair in by_game.items():
        if wanted_games is not None and gid not in wanted_games:
            continue
        e = ev.get(gid)
        if e is not None and not e.get("completed"):
            stats["not_completed_in_schedule"] += 1
            continue
        if len(pair) != 2:
            stats["games_one_row"] += 1
            continue
        comp = {c["home_away"]: str(c["team_id"]) for c in (e or {}).get("competitors", []) if c.get("home_away")}
        home, away = comp.get("home"), comp.get("away")
        if home not in pair or away not in pair:
            stats["games_one_row"] += 1
            continue
        h, a = pair[home], pair[away]
        gd = game_date(h["kickoff_utc"])
        base = {"game_id": gid, "final": True, "participated": True, "source": "espn_game_log", "game_date": gd,
                "observed_stat_source_time": h.get("observed_at")}
        out += [{**base, "entity_id": home, "family": "team_points", "actual": float(h["points_for"])},
                {**base, "entity_id": away, "family": "team_points", "actual": float(a["points_for"])},
                {**base, "entity_id": rec.TEAM_ENTITY_TOTAL, "family": "game_total", "actual": float(h["points_for"] + a["points_for"])},
                {**base, "entity_id": rec.TEAM_ENTITY_MARGIN, "family": "margin", "actual": float(h["points_for"] - a["points_for"])}]
        stats["games_final"] += 1
    return out, stats


def qb_segments(rows: list[dict]) -> dict[tuple[str, str], dict]:
    """Per (game_id, team_id): the leading passer, the team's previous leading passer, and whether it changed (post-hoc;
    the same definition as the study's 'Starting-QB change' investigation)."""
    by_team: dict[str, list[dict]] = {}
    for r in rows:
        if r.get("points_for") is None or not r.get("primary_passer"):
            continue
        by_team.setdefault(str(r["team_id"]), []).append(r)
    out = {}
    for t, rs in by_team.items():
        rs.sort(key=lambda x: x["kickoff_utc"])
        for i, r in enumerate(rs):
            prev = rs[i - 1]["primary_passer"] if i else None
            out[(str(r["game_id"]), t)] = {"leading_passer": r["primary_passer"], "previous_leading_passer": prev,
                                           "qb_changed": None if prev is None else bool(prev != r["primary_passer"])}
    return out


def attach_qb_segments(outcomes: list[dict], seg: dict) -> list[dict]:
    for o in outcomes:
        if o["family"] == "team_points":
            s = seg.get((o["game_id"], o["entity_id"]))
            if s:
                o.update(s)
    return outcomes


def player_outcomes(forecasts: pd.DataFrame, P: pd.DataFrame, final_games: dict[str, str]) -> tuple[list[dict], dict]:
    """Outcomes for archived player forecasts of games that are final in the ESPN log. A game is used only once the player
    box has rows for it (the release lags the ESPN log by hours)."""
    stats = {"games_without_box": 0, "zero_filled": 0, "void_passer": 0, "rows": 0}
    if forecasts.empty:
        return [], stats
    f = forecasts[forecasts["family"].isin(STAT_OF) & forecasts["game_id"].isin(final_games)]
    f = f.drop_duplicates(["game_id", "entity_id", "family"])
    box_games = set(P["game_id"].astype(str).unique())
    Pi = P.assign(game_id=P["game_id"].astype(str), player_id=P["player_id"].astype(str)).set_index(["game_id", "player_id"])
    out = []
    for r in f.itertuples():
        if str(r.game_id) not in box_games:
            stats["games_without_box"] += 1
            continue
        stat = STAT_OF[r.family]
        role = ROLE_OF[r.family]
        key = (str(r.game_id), str(r.entity_id))
        row = Pi.loc[key] if key in Pi.index else None
        if isinstance(row, pd.DataFrame):
            row = row.iloc[0]
        base = {"game_id": str(r.game_id), "entity_id": str(r.entity_id), "family": r.family, "final": True,
                "source": "sportsdataverse_espn_cfb_player_box", "game_date": final_games[str(r.game_id)]}
        if role == "passer":
            if row is None or not (row.get("pass_att") and row["pass_att"] > 0):
                out.append({**base, "actual": 0.0, "participated": False})
                stats["void_passer"] += 1
                continue
            out.append({**base, "actual": float(row[stat]), "participated": True})
        else:
            val = row.get(stat) if row is not None else None
            if val is None or (isinstance(val, float) and pd.isna(val)):
                out.append({**base, "actual": 0.0, "participated": True, "zero_filled": True})
                stats["zero_filled"] += 1
            else:
                out.append({**base, "actual": float(val), "participated": True})
        stats["rows"] += 1
    return out, stats


def final_game_dates(rows: list[dict]) -> dict[str, str]:
    return {str(r["game_id"]): game_date(r["kickoff_utc"]) for r in rows if r.get("points_for") is not None}
