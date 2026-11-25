"""Build one consistent team-game table 2014-2026 from ESPN postgame summaries (repo parser).

2014-2025: vault ESPN summaries parsed with the production parser `scripting.espn.team_games_from_summary`,
divisions/week/season-type from the CFBD games cache (ESPN id == CFBD id). 2026: the repo's ESPN game log
(`data/football/2026/team_games.jsonl`, same parser). Betting-line files are never opened.
"""
from __future__ import annotations

import gzip
import json
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

import pandas as pd

REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO / "src"))

PBP_KEYS = ("drives", "drive_points", "scrim_plays", "success_plays", "early_plays", "early_success",
            "pass_plays", "pass_success", "rush_plays", "rush_success", "scrim_yards", "explosive_yards",
            "scoring_opps", "garbage_plays_excluded")
BOX_KEYS = ("plays", "total_yards", "turnovers", "pass_att", "rush_att", "pass_yards", "rush_yards", "first_downs")


def _flatten(r) -> dict:
    d = r if isinstance(r, dict) else json.loads(json.dumps(r, default=lambda o: o.__dict__))
    out = {k: d.get(k) for k in ("game_id", "season", "week", "kickoff_utc", "team_id", "opponent_id", "site",
                                 "team_division", "opponent_division", "points_for", "points_against")}
    box, pbp = d.get("box") or {}, d.get("pbp") or {}
    for k in BOX_KEYS:
        out[f"box_{k}"] = box.get(k)
    for k in PBP_KEYS:
        out[f"pbp_{k}"] = pbp.get(k)
    return out


def _cfbd_games(cfbd_root: Path) -> dict[str, dict]:
    meta = {}
    for s in range(2014, 2027):
        f = cfbd_root / str(s) / "games.json.gz"
        if not f.exists():
            continue
        for g in json.load(gzip.open(f)):
            meta[str(g["id"])] = {"season": g["season"], "week": g["week"], "postseason": g.get("seasonType") != "regular",
                                  "start": g.get("startDate"), "hdiv": g.get("homeClassification"),
                                  "adiv": g.get("awayClassification"), "hid": str(g.get("homeId")),
                                  "aid": str(g.get("awayId")), "hconf": g.get("homeConference"),
                                  "aconf": g.get("awayConference")}
    return meta


def _parse_one(args):
    path, m = args
    from cfb_edge_finder.scripting.espn import team_games_from_summary
    try:
        summ = json.load(open(path))
        rows = team_games_from_summary(summ, observed_at="2026-11-16T00:00:00Z",
                                       divisions={m["hid"]: m["hdiv"] or "fcs", m["aid"]: m["adiv"] or "fcs"},
                                       week=m["week"])
    except Exception:  # noqa: BLE001
        return []
    out = []
    for r in rows:
        f = _flatten(r.as_dict())
        f["postseason"] = m["postseason"]
        out.append(f)
    return out


def build_team_games(espn_dir: Path, cfbd_root: Path, out: Path, workers: int = 4) -> pd.DataFrame:
    meta = _cfbd_games(cfbd_root)
    jobs = [(p, meta[p.stem]) for p in sorted(espn_dir.glob("*.json")) if p.stem in meta and meta[p.stem]["season"] < 2026]
    rows = []
    with ProcessPoolExecutor(workers) as ex:
        for part in ex.map(_parse_one, jobs, chunksize=64):
            rows.extend(part)
    for line in open(REPO / "data/football/2026/team_games.jsonl"):
        d = json.loads(line)
        f = _flatten(d)
        f["postseason"] = False
        rows.append(f)
    df = pd.DataFrame(rows)
    conf = {}
    for gid, m in meta.items():
        conf[(gid, m["hid"])] = m["hconf"]
        conf[(gid, m["aid"])] = m["aconf"]
    df["game_id"] = df["game_id"].astype(str)
    df["team_id"] = df["team_id"].astype(str)
    df["opponent_id"] = df["opponent_id"].astype(str)
    df["conference"] = [conf.get((g, t)) for g, t in zip(df.game_id, df.team_id)]
    df["date"] = (pd.to_datetime(df["kickoff_utc"], utc=True) - pd.Timedelta(hours=6)).dt.date.astype(str)  # US local game day
    df = df.drop_duplicates(["game_id", "team_id"]).reset_index(drop=True)
    df.to_parquet(out)
    return df
