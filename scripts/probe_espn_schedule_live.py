#!/usr/bin/env python3
"""Read-only live probe: which ESPN schedule surface is reachable from
CI, and what EXACT shape does it return? Section C of the
capture-resilience mission requires verifying the live response before
relying on any field. Runs on a GitHub runner because the dev
container's egress proxy 403s every ESPN host. ZERO CFBD calls.

Probe 1 (run 33789268655, 2026-09-03T18:14Z) established that
site.api.espn.com -- the host research/result_provider.py's settlement
fallback uses -- now answers 403 "Access Denied" (Akamai) from GitHub
runners, while sports.core.api.espn.com answers 200. This probe maps
what the reachable surfaces actually carry."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import requests

UA = {"User-Agent": "cfb-edge-finder capture-resilience probe (read-only)"}
CORE = "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football"


def get(url: str, params: dict | None = None) -> tuple[int, object]:
    try:
        r = requests.get(url, params=params, headers=UA, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"
    if r.status_code >= 400:
        return r.status_code, r.text[:200]
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:200]


def keys(o) -> object:
    return sorted(o.keys()) if isinstance(o, dict) else type(o).__name__


def main() -> int:
    now = datetime.now(UTC)
    day = now.strftime("%Y%m%d")
    span = f"{day}-{(now + timedelta(days=7)).strftime('%Y%m%d')}"
    out: dict = {"probed_at": now.isoformat()}

    # A. core events index with a DATE RANGE + limit (one call for a week?)
    st, body = get(f"{CORE}/events", {"dates": span, "limit": 500})
    out["A_core_events_range"] = {"http": st, "count": (body.get("count") if isinstance(body, dict) else None),
                                  "pageCount": (body.get("pageCount") if isinstance(body, dict) else None),
                                  "pageSize": (body.get("pageSize") if isinstance(body, dict) else None),
                                  "first_ref": ((body.get("items") or [{}])[0].get("$ref") if isinstance(body, dict) else body)}

    # B. follow ONE event $ref: is competition/competitor data inlined?
    ref = None
    if isinstance(body, dict) and body.get("items"):
        ref = body["items"][0].get("$ref")
    if ref:
        st2, ev = get(ref)
        rec: dict = {"http": st2, "keys": keys(ev)}
        if isinstance(ev, dict):
            comps = ev.get("competitions") or []
            comp = comps[0] if comps else {}
            rec["event_scalars"] = {k: ev.get(k) for k in ("id", "date", "name", "shortName")}
            rec["season"] = ev.get("season")
            rec["competition_keys"] = keys(comp)
            rec["competition_scalars"] = {
                k: comp.get(k) for k in ("id", "date", "neutralSite", "conferenceCompetition", "timeValid")
            }
            rec["status_is_ref"] = isinstance(comp.get("status"), dict) and "$ref" in (comp.get("status") or {})
            competitors = comp.get("competitors") or []
            rec["n_competitors"] = len(competitors)
            rec["competitor_sample"] = [
                {"homeAway": c.get("homeAway"), "id": c.get("id"), "team_keys": keys(c.get("team")),
                 "team_ref": (c.get("team") or {}).get("$ref")}
                for c in competitors
            ]
            # follow the status ref and one team ref
            status_ref = (comp.get("status") or {}).get("$ref")
            if status_ref:
                sts, sbody = get(status_ref)
                rec["status_payload"] = {"http": sts, "body": sbody if isinstance(sbody, dict) else str(sbody)[:200]}
            if competitors and (competitors[0].get("team") or {}).get("$ref"):
                tts, tbody = get(competitors[0]["team"]["$ref"])
                if isinstance(tbody, dict):
                    rec["team_payload"] = {k: tbody.get(k) for k in
                                           ("id", "location", "name", "displayName", "abbreviation", "shortDisplayName")}
                else:
                    rec["team_payload"] = {"http": tts, "body": str(tbody)[:200]}
        out["B_core_event_detail"] = rec

    # C. cdn.espn.com "core" xhr surface -- structured JSON page payload
    st3, b3 = get("https://cdn.espn.com/core/college-football/schedule",
                  {"xhr": 1, "year": now.year, "week": 2, "group": 80})
    rec3: dict = {"http": st3, "keys": keys(b3)}
    if isinstance(b3, dict):
        content = b3.get("content") or {}
        rec3["content_keys"] = keys(content)
        sched = content.get("schedule")
        if isinstance(sched, dict):
            first_day = next(iter(sched.values()), {})
            games = (first_day or {}).get("games") or []
            rec3["n_days"] = len(sched)
            rec3["n_games_first_day"] = len(games)
            if games:
                g = games[0]
                comp = (g.get("competitions") or [{}])[0]
                rec3["game_sample"] = {
                    "id": g.get("id"), "date": g.get("date"), "name": g.get("name"),
                    "season": g.get("season"),
                    "comp_date": comp.get("date"), "neutralSite": comp.get("neutralSite"),
                    "status_type": ((comp.get("status") or {}).get("type") or {}),
                    "competitors": [
                        {"homeAway": c.get("homeAway"),
                         "location": (c.get("team") or {}).get("location"),
                         "displayName": (c.get("team") or {}).get("displayName"),
                         "id": (c.get("team") or {}).get("id")}
                        for c in (comp.get("competitors") or [])
                    ],
                }
    else:
        rec3["body"] = b3
    out["C_cdn_schedule_xhr"] = rec3

    # D. cdn.espn.com scoreboard xhr (same shape family as site.api scoreboard)
    st4, b4 = get("https://cdn.espn.com/core/college-football/scoreboard",
                  {"xhr": 1, "dates": day, "groups": 80, "limit": 400})
    rec4: dict = {"http": st4, "keys": keys(b4)}
    if isinstance(b4, dict):
        content = b4.get("content") or {}
        rec4["content_keys"] = keys(content)
        sbd = content.get("sbData") or {}
        events = sbd.get("events") or []
        rec4["n_events"] = len(events)
        if events:
            e = events[0]
            comp = (e.get("competitions") or [{}])[0]
            rec4["event_sample"] = {
                "id": e.get("id"), "date": e.get("date"), "name": e.get("name"), "season": e.get("season"),
                "comp_date": comp.get("date"), "neutralSite": comp.get("neutralSite"),
                "status_type": ((comp.get("status") or {}).get("type") or {}),
                "competitors": [
                    {"homeAway": c.get("homeAway"),
                     "location": (c.get("team") or {}).get("location"),
                     "displayName": (c.get("team") or {}).get("displayName")}
                    for c in (comp.get("competitors") or [])
                ],
            }
            vocab: dict = {}
            for ev2 in events:
                t = (((ev2.get("competitions") or [{}])[0]).get("status") or {}).get("type") or {}
                k = f"{t.get('id')}|{t.get('name')}|{t.get('state')}|{t.get('completed')}"
                vocab[k] = vocab.get(k, 0) + 1
            rec4["status_vocabulary"] = vocab
            rec4["all_two_competitors"] = all(
                len((((e2.get("competitions") or [{}])[0]).get("competitors") or [])) == 2 for e2 in events
            )
            rec4["neutral_bool_count"] = sum(
                1 for e2 in events
                if isinstance((((e2.get("competitions") or [{}])[0])).get("neutralSite"), bool)
            )
    else:
        rec4["body"] = b4
    out["D_cdn_scoreboard_xhr"] = rec4

    # E. site.web.api scoreboard (different host from site.api)
    st5, b5 = get("https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
                  {"groups": 80, "dates": day, "limit": 400})
    out["E_site_web_api"] = {"http": st5, "keys": keys(b5),
                             "n_events": (len(b5.get("events") or []) if isinstance(b5, dict) else None)}

    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
