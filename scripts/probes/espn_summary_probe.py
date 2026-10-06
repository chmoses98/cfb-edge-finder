"""Throwaway probe: print the real shape of ESPN CFB summary payloads.

Run only by .github/workflows/script-engine-probe.yml on the script-engine
development branch; removed before review. Read-only public GETs.
"""

from __future__ import annotations

import base64
import collections
import gzip
import json
import sys

import requests

UA = {"User-Agent": "cfb-edge-finder-probe/1.0"}
SB = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard"
SUM = "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary"


def get(url, params=None):
    r = requests.get(url, params=params, headers=UA, timeout=30)
    print("GET", r.url, r.status_code)
    r.raise_for_status()
    return r.json()


def trim(summary):
    keep = {k: summary.get(k) for k in ("boxscore", "drives", "header", "gameInfo", "scoringPlays")}
    for team in (keep.get("boxscore") or {}).get("players") or []:
        for stat in team.get("statistics") or []:
            for ath in stat.get("athletes") or []:
                a = ath.get("athlete") or {}
                ath["athlete"] = {k: a.get(k) for k in ("id", "displayName", "position")}
    return keep


def main():
    dates = sys.argv[1:] or ["20261003"]
    for d in dates:
        sb = get(SB, {"groups": 80, "dates": d, "limit": 400})
        events = sb.get("events") or []
        print("events", len(events))
        done = [e for e in events if ((e.get("status") or {}).get("type") or {}).get("completed")]
        print("completed", len(done))
        ev0 = events[0]
        print("SCOREBOARD_EVENT_KEYS", sorted(ev0.keys()))
        comp = ev0["competitions"][0]
        print("COMPETITION_KEYS", sorted(comp.keys()))
        print("COMPETITOR0", json.dumps({k: v for k, v in comp["competitors"][0].items() if k != "statistics"})[:1500])
        for e in done[:2]:
            s = get(SUM, {"event": e["id"]})
            print("SUMMARY_KEYS", sorted(s.keys()))
            bx = s.get("boxscore") or {}
            for t in bx.get("teams") or []:
                print("TEAM", t.get("homeAway"), (t.get("team") or {}).get("displayName"),
                      [(x.get("name"), x.get("displayValue")) for x in t.get("statistics") or []])
            for t in bx.get("players") or []:
                for st in t.get("statistics") or []:
                    print("PLAYERCAT", (t.get("team") or {}).get("abbreviation"), st.get("name"),
                          st.get("keys"), st.get("labels"), st.get("totals"))
            drives = (s.get("drives") or {}).get("previous") or []
            print("DRIVES", len(drives))
            if drives:
                d0 = drives[0]
                print("DRIVE_KEYS", sorted(d0.keys()))
                print("DRIVE0", json.dumps({k: v for k, v in d0.items() if k != "plays"})[:2000])
                print("PLAY0", json.dumps((d0.get("plays") or [{}])[0])[:2500])
            types = collections.Counter()
            for dr in drives:
                for p in dr.get("plays") or []:
                    types[(p.get("type") or {}).get("text")] += 1
            print("PLAYTYPES", dict(types))
            print("HEADER", json.dumps(s.get("header"))[:3000])
            blob = base64.b64encode(gzip.compress(json.dumps(trim(s), sort_keys=True).encode())).decode()
            print("FIXTURE_B64_LEN", len(blob))
            for i in range(0, len(blob), 4000):
                print(f"FIXTURE_B64 {e['id']} {i // 4000:04d} {blob[i:i + 4000]}")
            break
    inj = get("https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/seasons/2026/teams/333/injuries")
    print("INJURIES", json.dumps(inj)[:1500])


if __name__ == "__main__":
    main()
