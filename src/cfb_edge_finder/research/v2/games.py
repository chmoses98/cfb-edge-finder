"""Canonical game table for V2 research, built from raw CFBD /games rows.

One row per completed game (any classification) with resolved team ids,
season/week/as-of ordering, kickoff, neutral flag, conference metadata,
classification, and the postgame targets. Only the TARGET columns are
postgame; everything else is pregame-known.

Team identity: the production resolver (`resolve_team_id_for_game`) is
used for FBS sides, so every join with ratings/features keys the same
way production does. FCS sides that fail resolution get a deterministic
slug from the raw name (they are never individually rated).

Postseason weeks are mapped through the production `postseason_week_rank`
so an AsOf(season, week) ordering sorts every bowl/CFP game after every
regular-season week -- identical leakage semantics to modeling/corpus.py.
"""

from __future__ import annotations

import gzip
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import pandas as pd

from cfb_edge_finder.ids import slugify_team
from cfb_edge_finder.ingestion.team_matching import resolve_team_id_for_game
from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError, derive_week_metadata
from cfb_edge_finder.modeling.leakage import postseason_week_rank

CFBD = "cfbd"


def read_json_gz(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        return json.load(fh)


def _resolve(name, classification) -> str | None:
    if not name:
        return None
    try:
        return resolve_team_id_for_game(str(name), CFBD, classification)
    except Exception:
        if (classification or "").lower() == "fbs":
            return None
        return slugify_team(str(name))


@dataclass(frozen=True)
class GameTableBuild:
    games: pd.DataFrame
    skipped: list[dict]


def _week_rank(raw: dict) -> int | None:
    st = raw.get("seasonType") or raw.get("season_type")
    if st == "postseason":
        try:
            meta = derive_week_metadata(
                season_type_raw=st,
                week_raw=raw.get("week"),
                postseason_descriptor=raw.get("notes") or raw.get("name"),
                playoff=raw.get("playoff"),
            )
            return postseason_week_rank(meta.season_type, meta.cfp_round)
        except (UnclassifiablePostseasonError, ValueError):
            return None
    return raw.get("week")


def build_game_table(raw_games: list[dict], *, completed_only: bool = True) -> GameTableBuild:
    rows: list[dict] = []
    skipped: list[dict] = []
    for raw in raw_games:
        gid = str(raw.get("id"))
        completed = bool(raw.get("completed"))
        hp = raw.get("homePoints")
        ap = raw.get("awayPoints")
        if completed_only and (not completed or hp is None or ap is None):
            skipped.append({"game_id": gid, "reason": "not completed"})
            continue
        season = raw.get("season")
        week = _week_rank(raw)
        if season is None or week is None:
            skipped.append({"game_id": gid, "reason": "missing season/week"})
            continue
        hc = (raw.get("homeClassification") or "").lower() or None
        ac = (raw.get("awayClassification") or "").lower() or None
        if hc != "fbs" and ac != "fbs":
            skipped.append({"game_id": gid, "reason": "no FBS side"})
            continue
        home = _resolve(raw.get("homeTeam"), hc)
        away = _resolve(raw.get("awayTeam"), ac)
        if home is None or away is None:
            skipped.append({"game_id": gid, "reason": "unresolved FBS name"})
            continue
        start = raw.get("startDate") or raw.get("start_date")
        try:
            kickoff = datetime.fromisoformat(start.replace("Z", "+00:00")) if start else None
        except ValueError:
            kickoff = None
        rows.append({
            "game_id": gid,
            "season": int(season),
            "week": int(week),
            "season_type": raw.get("seasonType") or raw.get("season_type"),
            "kickoff": kickoff,
            "start_time_tbd": bool(raw.get("startTimeTBD")),
            "home": home,
            "away": away,
            "home_raw": raw.get("homeTeam"),
            "away_raw": raw.get("awayTeam"),
            "home_class": hc,
            "away_class": ac,
            "home_conf": raw.get("homeConference"),
            "away_conf": raw.get("awayConference"),
            "conference_game": raw.get("conferenceGame"),
            "neutral": bool(raw.get("neutralSite")),
            "venue_id": raw.get("venueId"),
            "venue": raw.get("venue"),
            "home_pregame_elo": raw.get("homePregameElo"),
            "away_pregame_elo": raw.get("awayPregameElo"),
            "home_points": None if hp is None else int(hp),
            "away_points": None if ap is None else int(ap),
            "completed": completed,
        })
    df = pd.DataFrame(rows)
    if len(df):
        df["margin"] = df["home_points"] - df["away_points"]
        df["total"] = df["home_points"] + df["away_points"]
        df["home_won"] = (df["margin"] > 0).astype(int)
        df["both_fbs"] = (df["home_class"] == "fbs") & (df["away_class"] == "fbs")
        df = df.sort_values(["season", "week", "kickoff", "game_id"]).reset_index(drop=True)
    return GameTableBuild(games=df, skipped=skipped)
