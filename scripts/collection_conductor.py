#!/usr/bin/env python3
"""Drives the canonical collector on a dependable cadence when it matters.

*** WHAT THIS IS AND IS NOT ***

It is a TRIGGER. It decides *when* `research_scan_and_capture.py` should
run and dispatches it. It never scans, prices, resolves a due label, or
writes an observation itself -- there is deliberately no second capture
implementation to drift out of step with the first.

*** WHY IT EXISTS ***

GitHub's cron scheduler is not dependable enough to protect CLOSING.
Measured 2026-08-27 against an HOURLY cron, consecutive scheduled runs
arrived at gaps up to 653 minutes, and after the 10-minute cadence merged
none fired for three hours. CLOSING is 14 minutes wide, strictly
pre-kickoff, and unrecoverable once the ball is kicked.

The key observation from that audit: GitHub's *scheduler* is unreliable,
but its *runner* is not -- every run that started completed normally. So
the fix is to stop needing many precise cron firings and instead let a
running job drive the cadence itself.

A probe on 2026-08-27T22:02Z established the mechanism: a workflow run
CAN start the next run using the built-in GITHUB_TOKEN (run 33120829196,
actor github-actions[bot]). No user-supplied credential is required.

*** COST, AND WHY THE GUARD IS NARROW ***

Any wait costs runner minutes, so a 24/7 tight loop would be both
wasteful and, on a private repo, expensive. It is also unnecessary:
T_7D/T_3D/T_24H/T_6H are hours-to-days wide and T_90/T_60/T_30 are
60/30/30 minutes wide, all comfortably caught by the existing 10-minute
cron even with substantial drift. Only CLOSING is both narrow and
unrecoverable.

So the tight loop engages ONLY inside CLOSING_GUARD_LEAD_MINUTES of a
supported kickoff. Kickoffs cluster (noon, 3:30, 7:00), so overlapping
bands collapse into a handful of short windows per game day rather than
running around the clock.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.trigger import (  # noqa: E402
    CLOSING_GUARD_LEAD_MINUTES,
    TIGHT_INTERVAL_SECONDS,
    guard_should_be_active,
    seconds_until_guard_needed,
)

COLLECTOR_WORKFLOW = "research-capture.yml"
CONDUCTOR_WORKFLOW = "research-collection-conductor.yml"

MAX_DISPATCHES_PER_RUN = 120
"""Hard backstop against a runaway loop. At the tight interval this is
about 8 hours of guarding, far more than any single band needs."""

MAX_JOB_SECONDS = 5 * 3600 + 30 * 60
"""Stay clear of GitHub's 6-hour job ceiling, leaving room to hand off to
a successor before being killed mid-sleep."""

MAX_IDLE_SLEEP_SECONDS = 30 * 60
"""Longest single wait when no band is open. Bounds how much runner time
one conductor burns doing nothing, and how stale its schedule view gets."""


@dataclass
class DispatchOutcome:
    ok: bool
    status: int
    detail: str


def _api(url: str, token: str, payload: dict | None = None, method: str = "POST") -> DispatchOutcome:
    body = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(url, data=body, method=method)
    request.add_header("Accept", "application/vnd.github+json")
    request.add_header("Authorization", f"Bearer {token}")
    request.add_header("X-GitHub-Api-Version", "2022-11-28")
    request.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return DispatchOutcome(True, response.status, "ok")
    except urllib.error.HTTPError as exc:
        # 401/403 mean the token cannot dispatch. Surfaced, never retried
        # into a loop: a broken credential must be loud, not a silent
        # trickle of failures that looks like the scheduler being slow.
        return DispatchOutcome(False, exc.code, exc.read().decode()[:300])
    except (urllib.error.URLError, TimeoutError) as exc:
        return DispatchOutcome(False, 0, f"{type(exc).__name__}: {exc}")


def dispatch_workflow(repo: str, workflow: str, ref: str, token: str, inputs: dict) -> DispatchOutcome:
    return _api(
        f"https://api.github.com/repos/{repo}/actions/workflows/{workflow}/dispatches",
        token,
        {"ref": ref, "inputs": inputs},
    )


def supported_upcoming_kickoffs(season: int, now: datetime, horizon_hours: float = 36.0) -> list[datetime]:
    """Kickoffs of supported (FBS-vs-FBS) games inside the horizon.

    Uses the same ingestion and classification the collector uses, so the
    conductor cannot decide to guard a game the collector would not
    capture."""
    sys.path.insert(0, str(REPO_ROOT / "scripts"))
    import capture_kalshi_cfb_snapshot as milestone_d

    from cfb_edge_finder.config import Settings
    from cfb_edge_finder.data.cfbd_client import CFBDClient

    settings = Settings()
    client = CFBDClient(api_key=settings.cfbd_api_key)
    # The SAME schedule fetch and FBS/FCS classification the collector
    # uses, so the conductor cannot decide to guard a game the collector
    # would classify as unsupported and skip.
    games, classification = milestone_d._fetch_candidate_games(season, client, now)  # noqa: SLF001
    horizon = now + timedelta(hours=horizon_hours)
    kickoffs = []
    for game in games:
        if game.kickoff_utc is None or not (now < game.kickoff_utc <= horizon):
            continue
        if classification.get(game.game_id, (None, None)) != ("fbs", "fbs"):
            continue
        kickoffs.append(game.kickoff_utc)
    return sorted(kickoffs)


def plan(now: datetime, kickoffs: list[datetime]) -> tuple[bool, float | None]:
    """(guard active now, seconds until the next band opens)."""
    return guard_should_be_active(now, kickoffs), seconds_until_guard_needed(now, kickoffs)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--ref", default=os.environ.get("GITHUB_REF_NAME", "main"))
    parser.add_argument("--interval-seconds", type=float, default=TIGHT_INTERVAL_SECONDS)
    parser.add_argument("--max-seconds", type=float, default=MAX_JOB_SECONDS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute and print the plan; dispatch nothing and sleep not at all.",
    )
    parser.add_argument(
        "--no-self-continue",
        action="store_true",
        help="Do not hand off to a successor conductor when this one exits.",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    started = datetime.now(UTC)
    print(f"conductor start        : {started.isoformat()}")
    print(f"repo / ref             : {args.repo or '(unset)'} / {args.ref}")
    print(f"tight interval         : {args.interval_seconds:.0f}s")
    print(f"guard lead             : {CLOSING_GUARD_LEAD_MINUTES:.0f} min before kickoff")
    print(f"token present          : {bool(token)}")

    try:
        kickoffs = supported_upcoming_kickoffs(args.season, started)
    except Exception as exc:  # noqa: BLE001 -- schedule source down must not kill the trigger layer
        print(f"SCHEDULE LOOKUP FAILED : {type(exc).__name__}: {exc}")
        print("Falling back to a single collector dispatch; GitHub cron remains the fallback trigger.")
        kickoffs = []

    print(f"supported kickoffs (36h): {len(kickoffs)}")
    for kickoff in kickoffs[:5]:
        print(f"   {kickoff.isoformat()}  (T-{(kickoff - started).total_seconds() / 3600:.1f}h)")

    active, until_band = plan(started, kickoffs)
    print(f"guard active now       : {active}")
    print(f"next band opens in     : {'n/a' if until_band is None else f'{until_band / 60:.0f} min'}")

    if args.dry_run:
        print("\nDRY RUN: nothing dispatched, nothing slept.")
        print("STATUS: trigger planning only. No pricing, no capture, no recommendation.")
        return 0

    if not args.repo or not token:
        print("\nMISSING repo or GITHUB_TOKEN -- cannot dispatch. GitHub cron remains the fallback.")
        return 1

    dispatches = 0
    failures = 0
    while dispatches < MAX_DISPATCHES_PER_RUN:
        now = datetime.now(UTC)
        elapsed = (now - started).total_seconds()
        if elapsed > args.max_seconds:
            print(f"job budget reached after {elapsed / 60:.0f} min")
            break

        active, until_band = plan(now, kickoffs)
        if active:
            outcome = dispatch_workflow(
                args.repo, COLLECTOR_WORKFLOW, args.ref, token,
                {"schedule_season": str(args.season), "no_push": "false"},
            )
            dispatches += 1
            status_note = "ok" if outcome.ok else outcome.detail
            print(f"[{now.isoformat()}] dispatch #{dispatches} -> {outcome.status} {status_note}")
            if not outcome.ok:
                failures += 1
                if outcome.status in (401, 403):
                    print("AUTH FAILURE -- refusing to spin. GitHub cron remains the fallback trigger.")
                    return 1
                if failures >= 5:
                    print("repeated dispatch failures -- stopping rather than looping")
                    return 1
            sleep_for = args.interval_seconds
        elif until_band is None:
            print("no upcoming supported kickoff in horizon -- conductor has nothing to guard")
            break
        else:
            sleep_for = min(until_band, MAX_IDLE_SLEEP_SECONDS)
            print(
                f"[{now.isoformat()}] idle; next band in {until_band / 60:.0f} min, "
                f"sleeping {sleep_for / 60:.0f} min"
            )

        remaining_budget = args.max_seconds - (datetime.now(UTC) - started).total_seconds()
        if sleep_for >= remaining_budget:
            print("next wait would exceed the job budget -- handing off")
            break
        time.sleep(sleep_for)

    print(f"\ndispatches issued      : {dispatches}")
    print(f"dispatch failures      : {failures}")

    if not args.no_self_continue:
        successor = dispatch_workflow(
            args.repo, CONDUCTOR_WORKFLOW, args.ref, token, {"season": str(args.season)}
        )
        print(f"successor conductor    : {successor.status} {'ok' if successor.ok else successor.detail}")
        if not successor.ok:
            print("NO SUCCESSOR -- the chain has stopped; GitHub cron is now the only trigger.")

    print("STATUS: trigger layer only. No pricing, staking, or trading action anywhere in this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
