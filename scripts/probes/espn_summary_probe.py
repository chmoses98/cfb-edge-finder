"""Throwaway probe: capture real ESPN CFB payloads as test fixtures.

Run only by .github/workflows/script-engine-probe.yml on the script-engine
development branch; removed before review. Read-only public GETs. Writes
trimmed payloads under tests/fixtures/espn/ for the workflow to commit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import requests

UA = {"User-Agent": "cfb-edge-finder-probe/1.0"}
SB = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
SUM = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"
OUT = Path("tests/fixtures/espn")


def get(url, params=None):
    r = requests.get(url, params=params, headers=UA, timeout=30)
    print("GET", r.url, r.status_code)
    return r.json() if r.status_code == 200 else None


def trim_summary(summary):
    keep = {k: summary.get(k) for k in ("boxscore", "drives", "header", "gameInfo")}
    for team in (keep.get("boxscore") or {}).get("players") or []:
        team["team"] = {k: (team.get("team") or {}).get(k) for k in ("id", "abbreviation", "displayName", "location")}
        for stat in team.get("statistics") or []:
            for ath in stat.get("athletes") or []:
                a = ath.get("athlete") or {}
                ath["athlete"] = {"id": a.get("id"), "displayName": a.get("displayName"),
                                  "position": {"abbreviation": ((a.get("position") or {}).get("abbreviation"))}}
    for team in (keep.get("boxscore") or {}).get("teams") or []:
        t = team.get("team") or {}
        team["team"] = {k: t.get(k) for k in ("id", "abbreviation", "displayName", "location", "name", "shortDisplayName")}
    header = keep.get("header") or {}
    for comp in header.get("competitions") or []:
        for c in comp.get("competitors") or []:
            t = c.get("team") or {}
            c["team"] = {k: t.get(k) for k in ("id", "abbreviation", "displayName", "location", "name", "shortDisplayName")}
    for dr in (keep.get("drives") or {}).get("previous") or []:
        t = dr.get("team") or {}
        dr["team"] = {k: t.get(k) for k in ("id", "abbreviation", "displayName", "shortDisplayName")}
        for p in dr.get("plays") or []:
            for k in ("participants", "wallclock", "modified", "mediaId"):
                p.pop(k, None)
    gi = keep.get("gameInfo") or {}
    keep["gameInfo"] = {"venue": gi.get("venue")}
    return keep


def trim_scoreboard(sb):
    out = []
    for e in sb.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        out.append({
            "id": e.get("id"), "date": e.get("date"), "name": e.get("name"),
            "status": {"type": ((e.get("status") or {}).get("type") or {})},
            "competitions": [{
                "id": comp.get("id"), "date": comp.get("date"), "neutralSite": comp.get("neutralSite"),
                "conferenceCompetition": comp.get("conferenceCompetition"),
                "venue": comp.get("venue"),
                "competitors": [{
                    "id": c.get("id"), "homeAway": c.get("homeAway"), "score": c.get("score"),
                    "winner": c.get("winner"), "records": c.get("records"),
                    "team": {k: (c.get("team") or {}).get(k) for k in (
                        "id", "abbreviation", "displayName", "shortDisplayName", "location", "name", "conferenceId")},
                } for c in comp.get("competitors") or []],
            }],
        })
    return {"events": out}


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    date = sys.argv[1] if len(sys.argv) > 1 else "20261003"
    for group in (80, 81):
        sb = get(SB, {"groups": group, "dates": date, "limit": 400})
        if sb is None:
            continue
        (OUT / f"scoreboard_{date}_g{group}.json").write_text(json.dumps(trim_scoreboard(sb), sort_keys=True, indent=1))
        done = [e for e in sb.get("events") or [] if ((e.get("status") or {}).get("type") or {}).get("completed")]
        picks = done[:3] if group == 80 else done[:1]
        for e in picks:
            s = get(SUM, {"event": e["id"]})
            if s is not None:
                (OUT / f"summary_{e['id']}.json").write_text(json.dumps(trim_summary(s), sort_keys=True, indent=1))
    upcoming = get(SB, {"groups": 80, "dates": "20261010", "limit": 400})
    if upcoming is not None:
        (OUT / "scoreboard_20261010_g80.json").write_text(json.dumps(trim_scoreboard(upcoming), sort_keys=True, indent=1))
    for url in (
        "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/teams/61/injuries",
        "https://site.api.espn.com/apis/site/v2/sports/football/college-football/teams/61/injuries",
    ):
        inj = get(url)
        print("INJURIES", url, json.dumps(inj)[:800] if inj is not None else None)


if __name__ == "__main__":
    main()
