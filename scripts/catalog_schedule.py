#!/usr/bin/env python3
"""Keep the Kalshi catalog on its cadence, and report missed capture windows.

GitHub creates only a handful of the catalog's ``*/30`` cron runs per day
(see src/cfb_edge_finder/catalog/schedule.py), so the CFB Research
Conductor calls ``keepalive --dispatch`` every 5-minute cycle and this
dispatches a catalog run whenever one is due.

    python scripts/catalog_schedule.py keepalive              # decide only
    python scripts/catalog_schedule.py keepalive --dispatch   # decide and dispatch
    python scripts/catalog_schedule.py report --hours 24      # capture-gap diagnostics

Needs the ``gh`` CLI with GH_TOKEN (actions: read; actions: write to
dispatch). Both commands are diagnostics around the catalog, never part of
it: ``report`` always exits 0, and ``keepalive`` exits 1 only when the run
history could not be read.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta

from cfb_edge_finder.catalog.schedule import (
    CATALOG_WORKFLOW_FILE,
    capture_gaps,
    keepalive_decision,
    parse_runs,
    run_summary,
)

FIELDS = "databaseId,createdAt,updatedAt,status,conclusion,event"


def fetch_runs(limit: int) -> list[dict]:
    out = subprocess.run(
        ["gh", "run", "list", "--workflow", CATALOG_WORKFLOW_FILE, "--branch", "main",
         "--limit", str(limit), "--json", FIELDS],
        check=True, capture_output=True, text=True, timeout=60,
    ).stdout
    return json.loads(out)


def _now(value: str | None) -> datetime:
    if value:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    return datetime.now(UTC)


def cmd_keepalive(args: argparse.Namespace) -> int:
    try:
        runs = parse_runs(fetch_runs(20))
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(json.dumps({"catalog_keepalive": "error", "error": str(exc)}))
        return 1
    decision = keepalive_decision(runs, _now(args.now))
    result = {"catalog_keepalive": decision.as_dict(), "dispatched": False}
    if decision.dispatch and args.dispatch:
        try:
            subprocess.run(
                ["gh", "workflow", "run", CATALOG_WORKFLOW_FILE, "--ref", "main"],
                check=True, capture_output=True, text=True, timeout=60,
            )
            result["dispatched"] = True
        except (subprocess.SubprocessError, OSError) as exc:
            result["dispatch_error"] = str(exc)
    print(json.dumps(result, sort_keys=True))
    if decision.capture_overdue:
        print(
            f"::warning title=Kalshi catalog capture overdue::last successful capture "
            f"{decision.minutes_since_last_success} min ago; RUN CFB would read CATALOG STALE"
        )
    return 0


def cmd_report(args: argparse.Namespace) -> int:
    now = _now(args.now)
    since = now - timedelta(hours=args.hours)
    try:
        runs = parse_runs(fetch_runs(100))
    except (subprocess.SubprocessError, OSError, ValueError) as exc:
        print(f"::warning title=Capture-gap report unavailable::{exc}")
        return 0
    gaps = capture_gaps(runs, now, since)
    counts = run_summary(runs, since)
    decision = keepalive_decision([r for r in runs if not r.active], now)
    lines = [
        f"### Kalshi catalog capture history (last {args.hours} h)",
        "",
        f"- runs created: {counts.get('created', 0)} "
        f"(scheduled {counts.get('scheduled', 0)}, dispatched {counts.get('dispatched', 0)})",
        f"- succeeded: {counts.get('success', 0)}, cancelled while queued: {counts.get('cancelled', 0)}, "
        f"failed: {counts.get('failure', 0)}",
        f"- previous successful capture: {decision.minutes_since_last_success} min ago",
        f"- capture windows missed (gap over the RUN CFB staleness tolerance): **{len(gaps)}**",
    ]
    lines += [f"  - {gap.describe()}" for gap in gaps]
    text = "\n".join(lines)
    print(text)
    for gap in gaps:
        print(f"::warning title=Missed Kalshi capture window::{gap.describe()}")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(text + "\n")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    keep = sub.add_parser("keepalive", help="dispatch a catalog run when one is due")
    keep.add_argument("--dispatch", action="store_true", help="actually dispatch (otherwise decide only)")
    keep.add_argument("--now", help="override the clock (ISO UTC), for testing")
    report = sub.add_parser("report", help="list missed capture windows")
    report.add_argument("--hours", type=int, default=24)
    report.add_argument("--now", help="override the clock (ISO UTC), for testing")
    args = parser.parse_args(argv)
    return cmd_keepalive(args) if args.command == "keepalive" else cmd_report(args)


if __name__ == "__main__":
    sys.exit(main())
