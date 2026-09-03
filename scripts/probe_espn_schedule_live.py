#!/usr/bin/env python3
"""Read-only live probe: which ESPN schedule surface is reachable, and
what EXACT shape does it return? Section C of the capture-resilience
mission requires verifying the live response before relying on any
field. Runs on a GitHub runner because the dev container's egress proxy
403s every ESPN host. Makes ZERO CFBD calls."""
from __future__ import annotations

import json
import sys
from datetime import UTC, datetime, timedelta

import requests

UA = {"User-Agent": "cfb-edge-finder capture-resilience probe (read-only)"}


def get(url: str, params: dict | None = None) -> tuple[int, object]:
    try:
        r = requests.get(url, params=params, headers=UA, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return 0, f"{type(exc).__name__}: {exc}"
    if r.status_code >= 400:
        return r.status_code, r.text[:300]
    try:
        return r.status_code, r.json()
    except ValueError:
        return r.status_code, r.text[:300]


def main() -> int:
    now = datetime.now(UTC)
    out: dict = {"probed_at": now.isoformat()}

    # 1. site.api scoreboard -- the surface research/result_provider.py parses.
    for offset in (0, 1, 2):
        day = (now + timedelta(days=offset)).strftime("%Y%m%d")
        status, body = get(
            "https://site.api.espn.com/apis/site/v2/sports/football/college-football/scoreboard",
            {"groups": 80, "dates": day, "limit": 400},
        )
        rec: dict = {"http": status}
        if isinstance(body, dict):
            events = body.get("events") or []
            rec["n_events"] = len(events)
            if events:
                ev = events[0]
                comp = (ev.get("competitions") or [{}])[0]
                comps = comp.get("competitors") or []
                rec["sample"] = {
                    "id": ev.get("id"),
                    "date_event": ev.get("date"),
                    "date_competition": comp.get("date"),
                    "name": ev.get("name"),
                    "season": ev.get("season"),
                    "neutralSite": comp.get("neutralSite"),
                    "status_type": ((comp.get("status") or {}).get("type") or {}),
                    "competitors": [
                        {
                            "homeAway": c.get("homeAway"),
                            "location": (c.get("team") or {}).get("location"),
                            "displayName": (c.get("team") or {}).get("displayName"),
                            "id": (c.get("team") or {}).get("id"),
                        }
                        for c in comps
                    ],
                }
                # distinct status vocabulary across the whole day
                vocab = {}
                for e in events:
                    c0 = (e.get("competitions") or [{}])[0]
                    t = (c0.get("status") or {}).get("type") or {}
                    key = f"{t.get('id')}|{t.get('name')}|{t.get('state')}|{t.get('completed')}"
                    vocab[key] = vocab.get(key, 0) + 1
                rec["status_vocabulary"] = vocab
                rec["all_have_two_competitors"] = all(
                    len(((e.get("competitions") or [{}])[0]).get("competitors") or []) == 2 for e in events
                )
                rec["all_have_id_and_date"] = all(
                    e.get("id") and (((e.get("competitions") or [{}])[0]).get("date") or e.get("date"))
                    for e in events
                )
                rec["neutral_site_present"] = sum(
                    1 for e in events if isinstance((((e.get("competitions") or [{}])[0])).get("neutralSite"), bool)
                )
        else:
            rec["body"] = body
        out[f"site_api_scoreboard_{day}"] = rec

    # 2. core API events index (the surface the V2 sidecar used).
    status, body = get(
        "https://sports.core.api.espn.com/v2/sports/football/leagues/college-football/events",
        {"limit": 5, "dates": now.strftime("%Y%m%d")},
    )
    out["core_api_events"] = {
        "http": status,
        "count": (body.get("count") if isinstance(body, dict) else None),
        "keys": (sorted(body.keys()) if isinstance(body, dict) else None),
    }

    print(json.dumps(out, indent=1, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
