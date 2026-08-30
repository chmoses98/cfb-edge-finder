"""Live, read-only probe of ESPN's keyless college-football scoreboard
endpoint as a SETTLEMENT-RESULT source: exactly which fields carry event
identity, finality, and final scores, verified from real responses
rather than community folklore.

*** WHY (2026-08-29 CFBD quota outage) ***
Research settlement is CFBD-primary and CFBD is returning HTTP 429, so
completed opening-slate games cannot be canonically settled even though
their results are public. Before building the fail-closed ESPN fallback,
this probe records the REAL response shape the fallback will be built
and fixtured from. This dev environment's egress to ESPN is
policy-blocked (403 CONNECT), so this runs from a GitHub Actions runner
via workflow_dispatch, exactly like the other validate_* scripts.

READ-ONLY: keyless public GETs only; prints results; writes nothing.
"""

from __future__ import annotations

import json
import sys

from cfb_edge_finder.data.espn_client import ESPNClient

DATES = ("20260829", "20260830")

STATUS_FIELDS = ("id", "name", "state", "completed", "detail", "shortDetail")


def _competitor_summary(competitor: dict) -> dict:
    team = competitor.get("team") or {}
    return {
        "homeAway": competitor.get("homeAway"),
        "score": competitor.get("score"),
        "winner": competitor.get("winner"),
        "team.id": team.get("id"),
        "team.location": team.get("location"),
        "team.name": team.get("name"),
        "team.displayName": team.get("displayName"),
        "team.shortDisplayName": team.get("shortDisplayName"),
        "team.abbreviation": team.get("abbreviation"),
    }


def main() -> int:
    client = ESPNClient()
    for date in DATES:
        body = client.fetch_scoreboard(date)
        events = body.get("events") or []
        print(f"\n{'=' * 78}\nSCOREBOARD {date}: {len(events)} events; top-level keys: {sorted(body.keys())}")
        leagues = body.get("leagues") or []
        if leagues:
            print(f"league: {leagues[0].get('name')!r} season={leagues[0].get('season', {}).get('year')}")

        status_names = {}
        for event in events:
            competitions = event.get("competitions") or []
            comp = competitions[0] if competitions else {}
            status = (comp.get("status") or event.get("status") or {}).get("type") or {}
            status_names.setdefault(
                (status.get("name"), status.get("state"), status.get("completed")), 0
            )
            status_names[(status.get("name"), status.get("state"), status.get("completed"))] += 1
        print(f"status.type (name, state, completed) distribution: {status_names}")

        for event in events:
            competitions = event.get("competitions") or []
            comp = competitions[0] if competitions else {}
            status = (comp.get("status") or event.get("status") or {}).get("type") or {}
            competitors = comp.get("competitors") or []
            print(
                json.dumps(
                    {
                        "event.id": event.get("id"),
                        "event.date": event.get("date"),
                        "event.name": event.get("name"),
                        "comp.date": comp.get("date"),
                        "comp.neutralSite": comp.get("neutralSite"),
                        "status.type": {k: status.get(k) for k in STATUS_FIELDS},
                        "competitors": [_competitor_summary(c) for c in competitors],
                    },
                    default=str,
                )
            )

        # One FULL raw event (bounded) as ground-truth schema evidence.
        if events:
            print("\nFULL RAW FIRST EVENT (first 4000 chars):")
            print(json.dumps(events[0], default=str)[:4000])

    print("\nSTATUS: READ-ONLY ESPN probe. Nothing captured, settled, or written.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
