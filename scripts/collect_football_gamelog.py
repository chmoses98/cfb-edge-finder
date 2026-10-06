#!/usr/bin/env python3
"""Collect the season game log the script engine is built on. Keyless, read-only.

    python scripts/collect_football_gamelog.py --season 2026 --out-dir data/football/2026

Walks the ESPN scoreboard (FBS group 80 and FCS group 81) from the start of
the season to `--horizon-days` ahead, and writes:

  schedule.json       every event: id, kickoff, status, teams, neutral site,
                      conference ids (compact; the market-blind identity and
                      freshness source)
  team_games.jsonl    one `TeamGame` row per team per COMPLETED game that
                      involved at least one FBS team (box score + play log)
  availability.json   ESPN injury listings for teams with an upcoming game,
                      with "could not look" kept distinct from "none listed"
  manifest.json       counts, coverage, failures and content hashes

Incremental: a completed game already in the log is not re-fetched unless it
finished within `--refetch-days` (late stat corrections). A failed summary
is recorded in the manifest and retried next run; it is never written as a
row of zeros.

Fail-soft like `collect_live_context.py`, whose transport it reuses: a
provider problem is recorded and the exit code stays 0. Exit 3 only when the
scoreboard itself could not be read for any date.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import collect_live_context as transport  # noqa: E402

from cfb_edge_finder.scripting.espn import (  # noqa: E402
    FBS_CONFERENCE_IDS,
    divisions_from_scoreboard,
    team_games_from_summary,
)
from cfb_edge_finder.scripting.gamelog import TeamGame, read_jsonl, write_jsonl  # noqa: E402

SUMMARY_HOSTS = (
    "https://site.api.espn.com/apis/site/v2/sports/football/college-football/summary",
    "https://site.web.api.espn.com/apis/site/v2/sports/football/college-football/summary",
)
INJURY_URL = transport.ESPN_CORE + "/teams/{team_id}/injuries"
QB_UNCERTAIN_STATUSES = ("out", "doubtful", "questionable", "day-to-day", "game-time decision")
EXIT_OK = 0
EXIT_NO_SCOREBOARD = 3


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def compact_event(event: dict[str, Any]) -> dict[str, Any]:
    """The schedule row: identity facts only. No odds block is ever kept."""
    comp = (event.get("competitions") or [{}])[0] or {}
    status = (event.get("status") or comp.get("status") or {}).get("type") or {}
    return {
        "id": str(event.get("id") or ""),
        "date": event.get("date") or comp.get("date"),
        "completed": bool(status.get("completed")),
        "state": status.get("state"),
        "neutral_site": bool(comp.get("neutralSite")),
        "conference_game": comp.get("conferenceCompetition"),
        "competitors": [
            {
                "home_away": c.get("homeAway"),
                "team_id": str((c.get("team") or {}).get("id") or c.get("id") or ""),
                "names": transport.team_names(c),
                "location": (c.get("team") or {}).get("location"),
                "abbreviation": (c.get("team") or {}).get("abbreviation"),
                "conference_id": (c.get("team") or {}).get("conferenceId"),
                "score": c.get("score") if isinstance(c.get("score"), (str, int, float)) else None,
            }
            for c in comp.get("competitors") or []
            if isinstance(c, dict)
        ],
    }


def as_espn_event(row: dict[str, Any]) -> dict[str, Any]:
    """A compact schedule row back in the scoreboard shape `_match_event` reads."""
    return {
        "id": row["id"],
        "date": row["date"],
        "competitions": [
            {
                "neutralSite": row["neutral_site"],
                "competitors": [
                    {
                        "homeAway": c["home_away"],
                        "team": {
                            "id": c["team_id"],
                            "displayName": (c["names"] or [None])[0],
                            "location": c.get("location"),
                            "abbreviation": c.get("abbreviation"),
                            "shortDisplayName": (c["names"] or [None])[-1],
                            "conferenceId": c.get("conference_id"),
                        },
                    }
                    for c in row["competitors"]
                ],
            }
        ],
    }


def fetch_summary(event_id: str) -> tuple[dict[str, Any] | None, str | None]:
    error = None
    for url in SUMMARY_HOSTS:
        payload, error = transport._get(url, {"event": event_id})
        if isinstance(payload, dict) and payload.get("boxscore"):
            return payload, None
        error = error or "summary carried no boxscore"
    return None, error


def availability_for(team_id: str, observed_at: str) -> dict[str, Any]:
    payload, error = transport._get(INJURY_URL.format(team_id=team_id))
    if payload is None:
        status = "NOT_PUBLISHED" if error and "404" in error else "NOT_OBSERVED"
        return {"status": status, "observed_at": observed_at, "error": error, "items": None}
    items = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        athlete = item.get("athlete") or {}
        items.append(
            {
                "athlete": athlete.get("displayName") or athlete.get("fullName"),
                "position": (athlete.get("position") or {}).get("abbreviation"),
                "status": item.get("status") or (item.get("type") or {}).get("description"),
            }
        )
    qb = next(
        (
            i
            for i in items
            if (i.get("position") or "").upper() == "QB"
            and any(s in str(i.get("status") or "").lower() for s in QB_UNCERTAIN_STATUSES)
        ),
        None,
    )
    return {
        "status": "OBSERVED",
        "observed_at": observed_at,
        "items": items,
        "qb_uncertain": qb is not None,
        "qb_name": qb.get("athlete") if qb else None,
        "qb_status": qb.get("status") if qb else None,
    }


def _hash_file(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--start", type=str, default=None, help="YYYY-MM-DD; default Aug 20 of the season")
    parser.add_argument("--through", type=str, default=None, help="YYYY-MM-DD; default today (UTC)")
    parser.add_argument("--horizon-days", type=int, default=14)
    parser.add_argument("--refetch-days", type=int, default=3)
    parser.add_argument("--skip-availability", action="store_true")
    parser.add_argument("--max-summaries", type=int, default=2000)
    args = parser.parse_args(argv)

    now = _now()
    observed = _iso(now)
    start = date.fromisoformat(args.start) if args.start else date(args.season, 8, 20)
    through = date.fromisoformat(args.through) if args.through else now.date()
    horizon = through + timedelta(days=args.horizon_days)
    out = args.out_dir
    out.mkdir(parents=True, exist_ok=True)

    schedule: dict[str, dict[str, Any]] = {}
    raw_events: list[dict[str, Any]] = []
    scoreboard_failures: list[dict[str, str]] = []
    day = start
    while day <= horizon:
        events, error = transport.fetch_scoreboard(day.strftime("%Y%m%d"))
        if error:
            scoreboard_failures.append({"date": day.isoformat(), "error": error})
        for event in events:
            row = compact_event(event)
            if row["id"]:
                schedule[row["id"]] = row
                raw_events.append(event)
        day += timedelta(days=1)
    if not schedule:
        print("no scoreboard date could be read; nothing written", file=sys.stderr)
        return EXIT_NO_SCOREBOARD

    divisions = divisions_from_scoreboard(raw_events)
    rows_path = out / "team_games.jsonl"
    existing = read_jsonl(rows_path)
    by_game: dict[str, list[TeamGame]] = {}
    for row in existing:
        by_game.setdefault(row.game_id, []).append(row)
    manifest_path = out / "manifest.json"
    previous_manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else {}
    failures: dict[str, str] = {}

    refetch_after = now - timedelta(days=args.refetch_days)
    wanted = []
    for event_id, row in sorted(schedule.items(), key=lambda kv: (kv[1]["date"] or "", kv[0])):
        if not row["completed"]:
            continue
        if not any(str(c.get("conference_id")) in FBS_CONFERENCE_IDS for c in row["competitors"]):
            continue
        kickoff = datetime.fromisoformat(str(row["date"]).replace("Z", "+00:00"))
        if event_id in by_game and kickoff < refetch_after:
            continue
        wanted.append(event_id)

    fetched = 0
    for event_id in wanted[: args.max_summaries]:
        summary, error = fetch_summary(event_id)
        if summary is None:
            failures[event_id] = error or "unknown"
            continue
        rows = team_games_from_summary(summary, observed_at=observed, divisions=divisions)
        if len(rows) != 2:
            failures[event_id] = "summary did not yield two team rows (incomplete header or box score)"
            continue
        by_game[event_id] = rows
        fetched += 1

    all_rows = [r for rows in by_game.values() for r in rows]
    rows_hash = write_jsonl(rows_path, all_rows)

    availability: dict[str, Any] = {}
    if not args.skip_availability:
        upcoming = [
            r
            for r in schedule.values()
            if not r["completed"]
            and r["date"]
            and r["date"][:10] <= horizon.isoformat()
            and any(str(c.get("conference_id")) in FBS_CONFERENCE_IDS for c in r["competitors"])
        ]
        for team_id in sorted({c["team_id"] for r in upcoming for c in r["competitors"] if c["team_id"]}):
            availability[team_id] = availability_for(team_id, observed)
    else:
        availability = (
            json.loads((out / "availability.json").read_text()) if (out / "availability.json").exists() else {}
        )
    (out / "availability.json").write_text(json.dumps(availability, indent=1, sort_keys=True) + "\n")

    sched_rows = sorted(schedule.values(), key=lambda r: (r["date"] or "", r["id"]))
    (out / "schedule.json").write_text(
        json.dumps({"season": args.season, "events": sched_rows}, sort_keys=True, separators=(",", ":")) + "\n"
    )

    completed_fbs = [
        r["id"]
        for r in sched_rows
        if r["completed"] and any(str(c.get("conference_id")) in FBS_CONFERENCE_IDS for c in r["competitors"])
    ]
    in_log = {r.game_id for r in all_rows}
    missing = sorted(set(completed_fbs) - in_log)
    pbp_rows = sum(1 for r in all_rows if r.pbp is not None)
    carried = {k: v for k, v in (previous_manifest.get("failures") or {}).items() if k in missing and k not in failures}
    manifest = {
        "schema_version": "cfb_football_gamelog_manifest/1.0.0",
        "season": args.season,
        "fetched_at": observed,
        "window": {"start": start.isoformat(), "through": through.isoformat(), "horizon": horizon.isoformat()},
        "sources": ["espn.scoreboard", "espn.summary.boxscore", "espn.summary.drives", "espn.core.injuries"],
        "events_in_schedule": len(sched_rows),
        "completed_games_with_fbs_team": len(completed_fbs),
        "games_in_log": len(in_log),
        "games_missing_from_log": missing,
        "summaries_fetched_this_run": fetched,
        "summaries_wanted_this_run": len(wanted),
        "team_game_rows": len(all_rows),
        "rows_with_play_log": pbp_rows,
        "play_log_coverage": round(pbp_rows / len(all_rows), 4) if all_rows else None,
        "failures": {**carried, **failures},
        "scoreboard_failures": scoreboard_failures,
        "availability_teams": len(availability),
        "availability_status_counts": {
            s: sum(1 for a in availability.values() if a.get("status") == s)
            for s in sorted({a.get("status") for a in availability.values()})
        },
        "hashes": {"team_games.jsonl": rows_hash, "schedule.json": _hash_file(out / "schedule.json")},
        "not_a_market_input": "nothing in this directory carries a price, a line, a total or an odds block",
    }
    manifest_path.write_text(json.dumps(manifest, indent=1, sort_keys=True) + "\n")
    print(
        f"gamelog: {len(in_log)} games / {len(all_rows)} rows ({pbp_rows} with play log); "
        f"fetched {fetched} this run; missing {len(missing)}; failures {len(failures)}; "
        f"schedule {len(sched_rows)} events; availability {manifest['availability_status_counts']}"
    )
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
