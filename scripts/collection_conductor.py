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
    ScheduleHealth,
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

MAX_CHAIN_GENERATIONS = 24
"""How many times one chain may hand off before it must stop and let cron
restart it. At the ~5.5h job budget this is far more than any real game
day needs; it exists so a chain cannot outlive its purpose unnoticed."""

MAX_CHAIN_LIFETIME_SECONDS = 12 * 3600
"""Wall-clock lease on a whole chain, independent of generation count. A
chain that has been alive half a day is guarding nothing real any more,
whatever its counters say."""

MIN_LIFETIME_FOR_HANDOFF_SECONDS = 600.0
"""A conductor that lived under 10 minutes must not start another.

Structural anti-runaway backstop, independent of why the run ended. On
2026-08-27T23:01Z a logic error let every run hand off after ~20 seconds,
producing roughly three runs a minute with no game within 41 hours. A
correct conductor either guards for minutes or exits; nothing legitimate
finishes in seconds AND needs a successor, so this floor cannot suppress
real work."""

MAX_IDLE_SLEEP_SECONDS = 30 * 60
"""Longest single wait when no band is open. Bounds how much runner time
one conductor burns doing nothing, and how stale its schedule view gets."""


@dataclass(frozen=True)
class ChainLineage:
    """Identity of one conductor chain, inherited by each successor.

    Without it a runaway is invisible: every run looks like a fresh
    manual dispatch, so nothing can tell "the 25th generation of a chain
    that should have stopped" from "someone pressed Run". Carrying the
    lineage forward is what makes generation count and chain age
    enforceable at all."""

    chain_id: str
    generation: int
    chain_started_at: datetime

    def child(self) -> ChainLineage:
        return ChainLineage(self.chain_id, self.generation + 1, self.chain_started_at)

    def as_inputs(self) -> dict[str, str]:
        return {
            "chain_id": self.chain_id,
            "generation": str(self.generation + 1),
            "chain_started_at": self.chain_started_at.isoformat(),
        }


def may_dispatch_successor(
    *,
    self_continue_enabled: bool,
    handoff_reason: str | None,
    run_lifetime_seconds: float,
    lineage: ChainLineage,
    now: datetime,
    guard_still_needed: bool,
) -> tuple[bool, str]:
    """Every condition a successor must satisfy, in one pure function.

    *** THE INVARIANT ***
    A successor is dispatched ONLY when ALL of these hold. There is no
    fallthrough: the default is STOP, and each guard is independent, so a
    logic error in any single one cannot by itself recreate a runaway.

    The incident this encodes: on 2026-08-27T23:01Z the loop broke out on
    "nothing to guard" and then fell THROUGH to an unconditional
    dispatch. One manual dispatch became 25+ runs at ~3/minute. Making
    the decision a pure function with an explicit deny-by-default is what
    stops that shape of bug from being expressible here again."""
    if not self_continue_enabled:
        return False, "self-continue disabled by flag"
    if handoff_reason is None:
        return False, "nothing left to guard -- cron restarts a conductor when a game nears"
    if not guard_still_needed:
        return False, "no supported kickoff remains in the horizon"
    if run_lifetime_seconds < MIN_LIFETIME_FOR_HANDOFF_SECONDS:
        return False, (
            f"run lived {run_lifetime_seconds:.0f}s, under the "
            f"{MIN_LIFETIME_FOR_HANDOFF_SECONDS:.0f}s floor -- refusing to chain at this rate"
        )
    if lineage.generation + 1 > MAX_CHAIN_GENERATIONS:
        return False, f"chain reached generation {lineage.generation}, cap is {MAX_CHAIN_GENERATIONS}"
    chain_age = (now - lineage.chain_started_at).total_seconds()
    if chain_age > MAX_CHAIN_LIFETIME_SECONDS:
        return False, f"chain has lived {chain_age / 3600:.1f}h, lease is {MAX_CHAIN_LIFETIME_SECONDS / 3600:.0f}h"
    return True, handoff_reason


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


def fetch_schedule_health(season: int, now: datetime, horizon_hours: float = 36.0) -> ScheduleHealth:
    """Fetch the schedule and report POSITIVELY what was found.

    Returns counts and timestamps rather than just the in-horizon
    kickoffs, so a caller can tell "fetched 3,550 games, next supported
    kickoff in 40.6h, none inside the horizon" from "fetched nothing".
    The old signature returned only the in-horizon list, which made those
    two states identical -- exactly how the credential bug hid.

    A fetch failure is returned as data, not raised: the trigger layer
    must degrade to "cron is still the fallback" rather than crash, and
    the caller needs the failure state to report it."""
    horizon_end = now + timedelta(hours=horizon_hours)
    sys.path.insert(0, str(REPO_ROOT / "scripts"))

    # The imports live INSIDE the try with the fetch. A missing dependency
    # is just another way the schedule can be unavailable, and it must
    # degrade to FETCH_FAILED like any other -- not crash the trigger
    # layer and take the conductor down with it.
    try:
        import capture_kalshi_cfb_snapshot as milestone_d

        from cfb_edge_finder.config import Settings
        from cfb_edge_finder.data.cfbd_client import CFBDClient

        # from_env(), NOT Settings(): the bare constructor returns
        # dataclass defaults with every key None, so the conductor
        # silently ran with no CFBD credential, saw zero kickoffs, and
        # concluded it had nothing to guard -- on every single
        # invocation. The collector has always used from_env(); this line
        # was the one place that diverged.
        settings = Settings.from_env()
        client = CFBDClient(api_key=settings.cfbd_api_key)
        # The SAME schedule fetch and FBS/FCS classification the collector
        # uses, so the conductor cannot decide to guard a game the
        # collector would classify as unsupported and skip.
        games, classification = milestone_d._fetch_candidate_games(season, client, now)  # noqa: SLF001
    except Exception as exc:  # noqa: BLE001 -- a dead schedule source must not kill the trigger layer
        return ScheduleHealth(
            fetch_success=False,
            total_games=0,
            upcoming_games=0,
            supported_upcoming_games=0,
            supported_inside_horizon=0,
            horizon_end=horizon_end,
            detail=f"{type(exc).__name__}: {exc}",
        )

    upcoming: list[datetime] = []
    supported: list[datetime] = []
    for game in games:
        kickoff = game.kickoff_utc
        if kickoff is None or kickoff <= now:
            continue
        upcoming.append(kickoff)
        if classification.get(game.game_id, (None, None)) == ("fbs", "fbs"):
            supported.append(kickoff)

    upcoming.sort()
    supported.sort()
    inside = [k for k in supported if k <= horizon_end]

    return ScheduleHealth(
        fetch_success=True,
        total_games=len(games),
        upcoming_games=len(upcoming),
        supported_upcoming_games=len(supported),
        supported_inside_horizon=len(inside),
        horizon_end=horizon_end,
        next_upcoming_kickoff=upcoming[0] if upcoming else None,
        next_supported_kickoff=supported[0] if supported else None,
        next_supported_kickoff_inside_horizon=inside[0] if inside else None,
        kickoffs_inside_horizon=tuple(inside),
    )


def supported_upcoming_kickoffs(season: int, now: datetime, horizon_hours: float = 36.0) -> list[datetime]:
    """Backwards-compatible view: just the in-horizon supported kickoffs."""
    return list(fetch_schedule_health(season, now, horizon_hours).kickoffs_inside_horizon)


def plan(now: datetime, kickoffs: list[datetime]) -> tuple[bool, float | None]:
    """(guard active now, seconds until the next band opens)."""
    return guard_should_be_active(now, kickoffs), seconds_until_guard_needed(now, kickoffs)


def _write_step_summary(health: ScheduleHealth, telemetry: dict) -> None:
    """Surface the same facts in the Actions run summary, so normal state
    is diagnosable without opening raw logs."""
    path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not path:
        return
    verdict = "PASS" if health.fetch_success else "FAIL"
    try:
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(f"### Conductor — schedule {verdict} — {health.state.value}\n\n")
            handle.write("| field | value |\n|---|---|\n")
            for key, value in sorted(telemetry.items()):
                handle.write(f"| {key} | {value} |\n")
    except OSError:
        # Telemetry must never fail the run that produced it.
        return


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
    parser.add_argument("--chain-id", default=None, help="Inherited chain identity; a new one is minted if absent.")
    parser.add_argument("--generation", type=int, default=0, help="Inherited handoff count.")
    parser.add_argument("--chain-started-at", default=None, help="Inherited chain start (ISO).")
    parser.add_argument(
        "--no-self-continue",
        action="store_true",
        help="Do not hand off to a successor conductor when this one exits.",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "")
    started = datetime.now(UTC)

    chain_started_at = started
    if args.chain_started_at:
        try:
            chain_started_at = datetime.fromisoformat(args.chain_started_at.replace("Z", "+00:00"))
        except ValueError:
            chain_started_at = started
    lineage = ChainLineage(
        chain_id=args.chain_id or f"chain-{started.strftime('%Y%m%dT%H%M%S')}-{os.urandom(3).hex()}",
        generation=max(0, args.generation),
        chain_started_at=chain_started_at,
    )

    print(f"conductor start        : {started.isoformat()}")
    print(f"chain id               : {lineage.chain_id}")
    print(f"generation             : {lineage.generation} of max {MAX_CHAIN_GENERATIONS}")
    print(f"chain started at       : {lineage.chain_started_at.isoformat()}")
    print(f"chain age              : {(started - lineage.chain_started_at).total_seconds() / 60:.1f} min")
    print(f"repo / ref             : {args.repo or '(unset)'} / {args.ref}")
    print(f"tight interval         : {args.interval_seconds:.0f}s")
    print(f"guard lead             : {CLOSING_GUARD_LEAD_MINUTES:.0f} min before kickoff")
    print(f"token present          : {bool(token)}")

    # POSITIVE schedule telemetry. The post-incident run was judged
    # healthy only because an error line was absent -- which was equally
    # true of the broken conductor that fetched nothing. This states what
    # was actually retrieved, so the two are never confusable again.
    health = fetch_schedule_health(args.season, started)
    print(health.render())
    if not health.fetch_success:
        print("Schedule unavailable; GitHub cron remains the fallback trigger.")
    if health.state.is_operationally_suspicious:
        print(f"WARNING: schedule state {health.state.value} -- treating as unguardable and stopping")
    kickoffs = list(health.kickoffs_inside_horizon)
    for kickoff in kickoffs[:5]:
        print(f"   in-horizon kickoff  : {kickoff.isoformat()}  (T-{(kickoff - started).total_seconds() / 3600:.1f}h)")

    active, until_band = plan(started, kickoffs)
    print(f"guard active now       : {active}")
    print(f"next band opens in     : {'n/a' if until_band is None else f'{until_band / 60:.0f} min'}")

    if args.dry_run:
        # A dry run is the supported way to prove schedule health without
        # touching anything, so it emits the SAME structured telemetry a
        # real run does -- otherwise the safe diagnostic would be the one
        # that tells you least.
        planning_only = {
            **health.as_telemetry(),
            "chain_id": lineage.chain_id,
            "generation": lineage.generation,
            "collector_dispatches": 0,
            "successor_dispatched": False,
            "decision": "DRY_RUN_NO_ACTION",
            "decision_reason": "planning only",
        }
        print("\nCONDUCTOR " + json.dumps(planning_only, sort_keys=True))
        _write_step_summary(health, planning_only)
        print("\nDRY RUN: nothing dispatched, nothing slept.")
        print("STATUS: trigger planning only. No pricing, no capture, no recommendation.")
        return 0

    if not args.repo or not token:
        print("\nMISSING repo or GITHUB_TOKEN -- cannot dispatch. GitHub cron remains the fallback.")
        return 1

    dispatches = 0
    failures = 0
    handoff_reason: str | None = None
    while dispatches < MAX_DISPATCHES_PER_RUN:
        now = datetime.now(UTC)
        elapsed = (now - started).total_seconds()
        if elapsed > args.max_seconds:
            print(f"job budget reached after {elapsed / 60:.0f} min")
            handoff_reason = "job budget reached with work still ahead"
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
            # NOT a handoff. Nothing to guard means this conductor's work
            # is done; the hourly cron restarts one when a game
            # approaches. Dispatching a successor here is what produced
            # the runaway observed at 2026-08-27T23:01Z: with the next
            # kickoff 41h out (beyond the 36h horizon) every run exited
            # in ~20s and immediately started another, ~3 runs/minute
            # with no game anywhere near.
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
            handoff_reason = "next wait exceeds the job budget"
            break
        time.sleep(sleep_for)

    print(f"\ndispatches issued      : {dispatches}")
    print(f"dispatch failures      : {failures}")

    lifetime = (datetime.now(UTC) - started).total_seconds()
    print(f"run lifetime           : {lifetime:.0f}s")
    print(f"handoff reason         : {handoff_reason or 'none -- nothing left to guard'}")

    # A successor is for CONTINUING work, never for "there was nothing to
    # do". Every condition lives in may_dispatch_successor, which denies
    # by default -- see its docstring for the full invariant and the
    # incident it encodes.
    now_end = datetime.now(UTC)
    guard_still_needed = seconds_until_guard_needed(now_end, kickoffs) is not None
    allowed, decision = may_dispatch_successor(
        self_continue_enabled=not args.no_self_continue,
        handoff_reason=handoff_reason,
        run_lifetime_seconds=lifetime,
        lineage=lineage,
        now=now_end,
        guard_still_needed=guard_still_needed,
    )
    print(f"guard still needed     : {guard_still_needed}")
    print(f"successor decision     : {'DISPATCH' if allowed else 'STOP'} -- {decision}")

    if not allowed:
        print("chain ends here. The */10 collector cron and manual dispatch remain unaffected.")
    else:
        successor = dispatch_workflow(
            args.repo, CONDUCTOR_WORKFLOW, args.ref, token,
            {"season": str(args.season), **lineage.as_inputs()},
        )
        print(f"successor conductor    : {successor.status} {'ok' if successor.ok else successor.detail}")
        print(f"successor generation   : {lineage.generation + 1}")
        if not successor.ok:
            print("NO SUCCESSOR -- the chain has stopped; GitHub cron is now the only trigger.")

    # One machine-readable line so an operator (or a future tool) never
    # has to infer health from prose or from a missing error message.
    telemetry = {
        **health.as_telemetry(),
        "chain_id": lineage.chain_id,
        "generation": lineage.generation,
        "run_lifetime_seconds": round(lifetime, 1),
        "collector_dispatches": dispatches,
        "dispatch_failures": failures,
        "successor_dispatched": bool(allowed),
        "decision": "DISPATCH" if allowed else "STOP",
        "decision_reason": decision,
    }
    print("\nCONDUCTOR " + json.dumps(telemetry, sort_keys=True))
    _write_step_summary(health, telemetry)

    print("STATUS: trigger layer only. No pricing, staking, or trading action anywhere in this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
