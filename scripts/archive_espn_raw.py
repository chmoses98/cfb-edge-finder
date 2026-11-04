#!/usr/bin/env python3
"""Archive raw ESPN college-football summaries (drives, kicking, plays) for COMPLETED FBS-involved games into an append-only directory.

    python scripts/archive_espn_raw.py --archive-dir /path/to/archive --schedule data/football/2026/schedule.json --through 2026-11-05

Postgame only. Provider failures are recorded as rows, never as zeros. Exit 0 unless the archive could not be written."""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cfb_edge_finder.ladder_archive import espn_raw as er  # noqa: E402
from cfb_edge_finder.ladder_archive import espn_store as es  # noqa: E402
from cfb_edge_finder.scripting.espn import FBS_CONFERENCE_IDS  # noqa: E402


def involves_fbs(ev: dict) -> bool:
    return any(str(c.get("conference_id")) in FBS_CONFERENCE_IDS for c in ev.get("competitors", []))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--through", default=None, help="YYYY-MM-DD (UTC) latest kickoff day to consider; default today")
    ap.add_argument(
        "--min-age-hours", type=float, default=4.0, help="only games that kicked off at least this long ago"
    )
    ap.add_argument("--max-games", type=int, default=120)
    ap.add_argument(
        "--refresh-days",
        type=int,
        default=4,
        help="also read the live ESPN scoreboard for the last N days (0 = schedule file only)",
    )
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args(argv)
    now = datetime.now(UTC)
    run_id = a.run_id or uuid.uuid4().hex[:12]
    archive = Path(a.archive_dir)
    events = {e["id"]: e for e in json.loads(Path(a.schedule).read_text()).get("events", [])}
    refresh_errors: list[str] = []
    if a.refresh_days > 0:
        import collect_football_gamelog as gl
        import collect_live_context as transport

        for k in range(a.refresh_days + 1):
            day = (now - timedelta(days=k)).strftime("%Y%m%d")
            evs, err = transport.fetch_scoreboard(day)
            if evs is None:
                refresh_errors.append(f"scoreboard {day}: {err}")
                continue
            for ev in evs:
                row = gl.compact_event(ev)
                events[row["id"]] = row  # the live scoreboard wins over a stale schedule row
    hist = es.history(archive)
    cutoff = now - timedelta(hours=a.min_age_hours)
    through = date.fromisoformat(a.through) if a.through else now.date()
    skipped: dict[str, list[str]] = {}
    todo = []
    for eid, ev in sorted(events.items(), key=lambda kv: kv[1].get("date") or ""):
        kick = er_parse(ev.get("date"))
        reason = None
        if kick is None:
            reason = "NO_KICKOFF_TIME"
        elif kick.date() > through or kick > cutoff:
            reason = "TOO_RECENT_OR_FUTURE"
        elif not ev.get("completed") and ev.get("state") != "post":
            reason = "NOT_COMPLETED_PER_SCHEDULE"
        elif not involves_fbs(ev):
            reason = "NON_FBS"
        else:
            ok, why = es.should_fetch(hist.get(eid))
            if not ok:
                reason = why
        if reason:
            skipped.setdefault(reason, []).append(eid)
        else:
            todo.append(ev)
    todo = todo[: a.max_games]
    rows, bodies, failed = [], {}, []
    for ev in todo:
        f = er.fetch_raw(ev["id"])
        row = er.index_row(ev["id"], f, ev, run_id)
        rows.append(row)
        if f["ok"]:
            bodies[ev["id"]] = f["body"]
        else:
            failed.append({"event_id": ev["id"], "status": f["status"], "error": f["error"]})
    finished = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    miss: dict[str, int] = {}
    for r in rows:
        for m in r["missingness"]:
            miss[m] = miss.get(m, 0) + 1
    manifest = {
        "schema": "cfb_espn_raw_run/1.0",
        "run_id": run_id,
        "started_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        "finished_at": finished,
        "candidates": len(todo),
        "fetched_ok": sum(1 for r in rows if r.get("fetch_ok")),
        "failed": failed,
        "skipped_counts": {k: len(v) for k, v in skipped.items()},
        "missingness_counts": miss,
        "scoreboard_refresh_errors": refresh_errors,
        "authenticated": False,
    }
    out = es.write_run(archive, manifest, rows, bodies)
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "run_id",
                    "candidates",
                    "fetched_ok",
                    "skipped_counts",
                    "missingness_counts",
                    "scoreboard_refresh_errors",
                )
            },
            indent=1,
        )
    )
    print("failed:", len(failed))
    return 0


def er_parse(v):
    if not isinstance(v, str) or not v:
        return None
    try:
        d = datetime.fromisoformat(v.replace("Z", "+00:00"))
    except ValueError:
        return None
    return d if d.tzinfo else d.replace(tzinfo=UTC)


if __name__ == "__main__":
    raise SystemExit(main())
