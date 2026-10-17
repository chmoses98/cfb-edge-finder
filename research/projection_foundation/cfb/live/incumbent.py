"""The incumbent: the script engine's opponent-adjusted scoring baseline, read UNCHANGED from its frozen artifacts.

    data/scripting/live/frozen/<game_key>.json.gz  ->  content.matchup_profile.scoring_baseline
        {home_points, away_points, total_points, home_margin, uncertainty_points, label: "... NOT a projection"}

The engine itself calls this baseline descriptive and uncalibrated; it is the only per-game points number SIFT's
script engine shows, which is why it is the incumbent for team points / total / margin. It carries a mean only (no
distribution), so incumbent forecasts are archived with empty `probs` and scored on MAE / bias. Nothing here writes to
src/cfb_edge_finder or data/scripting.
"""
from __future__ import annotations

import glob
import gzip
import json
import os

from . import records as rec

LABEL = "script-engine scoring_baseline (opponent-adjusted points per game; descriptive, uncalibrated)"


def load(frozen_dir: str) -> dict[str, dict]:
    out = {}
    for fp in sorted(glob.glob(os.path.join(frozen_dir, "*.json.gz"))):
        try:
            d = json.load(gzip.open(fp))
            c = d["content"]
            sb = c["matchup_profile"]["scoring_baseline"]
            out[str(c["event_id"])] = {
                "event_id": str(c["event_id"]), "kickoff_utc": c.get("kickoff_utc"), "cutoff": c.get("football_data_cutoff"),
                "home_id": str(c["teams"]["home"]["team_id"]), "away_id": str(c["teams"]["away"]["team_id"]),
                "home_name": c["teams"]["home"]["name"], "away_name": c["teams"]["away"]["name"],
                "home_points": sb.get("home_points"), "away_points": sb.get("away_points"),
                "total_points": sb.get("total_points"), "home_margin": sb.get("home_margin"),
                "artifact_hash": d.get("artifact_hash"), "file": os.path.basename(fp)}
        except Exception:  # noqa: BLE001
            continue
    return out


def forecasts(inc: dict[str, dict], games: list[dict], now_utc: str, version: str) -> tuple[list[dict], dict]:
    """Incumbent records for the SAME games the challenger projects (`games` = project output entries)."""
    out, miss = [], {"no_artifact": 0, "no_baseline": 0}
    for g in games:
        a = inc.get(str(g["game_id"]))
        if a is None:
            miss["no_artifact"] += 1
            continue
        if a["home_points"] is None or a["away_points"] is None:
            miss["no_baseline"] += 1
            continue
        common = dict(game_id=g["game_id"], start_utc=g["start_utc"], dist_lo=0, probs=[], gate_status="N/A", participation=None,
                      readiness="incumbent", now_utc=now_utc, asof_utc=a["cutoff"] or now_utc, version=version,
                      role="incumbent", extra={"artifact_hash": a["artifact_hash"], "label": LABEL})
        for fam, eid, ename, mean in (("team_points", g["home_id"], g["home"], a["home_points"]),
                                      ("team_points", g["away_id"], g["away"], a["away_points"]),
                                      ("game_total", rec.TEAM_ENTITY_TOTAL, "game_total", a["total_points"]),
                                      ("margin", rec.TEAM_ENTITY_MARGIN, "margin", a["home_margin"])):
            out.append(rec.forecast_record(family=fam, entity_id=str(eid), entity_name=ename,
                                           team=f"{g['away']} @ {g['home']}" if fam != "team_points" else ename,
                                           mean=float(mean), **common))
    return out, miss
