#!/usr/bin/env python3
"""Trigger and Actions-budget telemetry. Changes no scheduling.

Reports what the heartbeat ledger can actually show about how collection
is being triggered, and answers the one operational question that matters
during Week 1:

    Is the current LOW cadence still acceptable, and when must it become
    TIGHT?

*** THE TEMPORARY WEEK 1 POLICY THIS SERVES ***

    QUIET             external scheduler at a low cadence (~hours), to
                      conserve private-repository Actions minutes.
    CRITICAL WINDOW   the owner manually switches the external scheduler
                      to a tight cadence (~5 minutes).
    AFTER             the owner may return it to low cadence.

This is an INTENTIONAL TEMPORARY POLICY operated by hand, not the final
automated architecture. It is documented here so a future reader does not
mistake a deliberate cost decision for a broken scheduler -- which is
exactly the mistake an earlier version of the ops health check made.

*** WHAT IT CANNOT KNOW ***

cron-job.org's configured schedule. Nothing here reads it, infers it, or
requires credentials for it. Every cadence number is MEASURED from
observed run timestamps and labelled as such.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from week1_ops_health import assess_protection, trigger_observations  # noqa: E402

from cfb_edge_finder.decision.collection_protection import (  # noqa: E402
    ProtectionState,
    observed_interval_minutes,
)
from cfb_edge_finder.research.heartbeat import heartbeat_path, load_heartbeats  # noqa: E402
from cfb_edge_finder.research.timing import CLOSING_WINDOW_MINUTES  # noqa: E402
from cfb_edge_finder.research.trigger import CLOSING_GUARD_LEAD_MINUTES  # noqa: E402

FULL_COLLECTOR_RUNTIME_SECONDS = 55.0
"""Measured full-scan runtime, used only to ESTIMATE Actions minutes.
GitHub bills whole minutes per job, so a run costs at least 1 minute
however short it is."""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--now", type=str, default=None)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    heartbeats = load_heartbeats(heartbeat_path(args.data_repo_dir, args.season))
    observations = trigger_observations(heartbeats)
    protection = assess_protection(heartbeats, now)

    overall, gaps = observed_interval_minutes(observations)
    external, external_gaps = observed_interval_minutes(
        observations, trigger_type="EXTERNAL_SCHEDULE"
    )
    provenance = Counter(o.trigger_type for o in observations)

    print("=" * 78)
    print(f"TRIGGER / ACTIONS BUDGET TELEMETRY -- {now.isoformat()}")
    print("=" * 78)
    print("  Scheduling is NOT changed by this report.\n")

    print("  RECENT COLLECTOR RUNS")
    for beat in heartbeats[-8:]:
        print(
            f"    {beat.get('invoked_at')}  {str(beat.get('trigger_type')):<17} "
            f"ok={beat.get('succeeded')} markets={beat.get('markets_discovered')} "
            f"due={beat.get('labels_due')} captured={beat.get('labels_captured')}"
        )

    print("\n  TRIGGER PROVENANCE")
    for source, count in sorted(provenance.items()):
        print(f"    {source:<20} {count}")

    print("\n  OBSERVED CADENCE (measured, never a configuration)")
    print(f"    all triggers        : "
          f"{'not measurable' if overall is None else f'{overall:.0f} min'} "
          f"(median of {gaps} gap(s))")
    print(f"    EXTERNAL_SCHEDULE   : "
          f"{'not measurable' if external is None else f'{external:.0f} min'} "
          f"(median of {external_gaps} gap(s))")

    successful = sum(1 for o in observations if o.succeeded)
    print("\n  ACTIONS USAGE (estimate)")
    print(f"    recorded runs       : {len(observations)} ({successful} successful)")
    print(f"    typical runtime     : ~{FULL_COLLECTOR_RUNTIME_SECONDS:.0f}s per full scan")
    print(f"    billed minutes est. : ~{len(observations)} (GitHub bills whole minutes per job)")
    if overall:
        print(f"    at the observed {overall:.0f} min cadence: ~{24 * 60 / overall:.0f} runs/day")
        print(f"    at a 5 min tight cadence         : ~{24 * 60 / 5:.0f} runs/day")
        print("    -- which is why the quiet period exists, and why the tight")
        print("       cadence is worth turning on only around critical windows.")

    print("\n  NEXT CRITICAL CHECKPOINT")
    print(f"    label               : {protection.checkpoint_label}")
    if protection.minutes_to_checkpoint is not None:
        hours = protection.minutes_to_checkpoint / 60
        print(f"    time until          : {protection.minutes_to_checkpoint:.0f} min ({hours:.1f} h)")
    print(f"    window width        : {protection.checkpoint_window_minutes} min")
    print(f"    protection state    : {protection.state.value}")

    print("\n  CADENCE POLICY")
    low_ok = protection.state in (ProtectionState.QUIET_PERIOD, ProtectionState.COVERED_TIGHT_CADENCE)
    print(f"    LOW cadence acceptable right now : {low_ok}")
    if protection.tighten_by is not None:
        print(f"    switch to TIGHT (~5 min) by      : {protection.tighten_by.isoformat()}")
        print(f"      (that is {CLOSING_GUARD_LEAD_MINUTES:.0f} min before the "
              f"{CLOSING_WINDOW_MINUTES:.0f}-minute CLOSING window opens)")
    arm_manual = protection.state in (
        ProtectionState.CLOSING_AT_RISK,
        ProtectionState.COLLECTION_STOPPED,
    )
    print(f"    manual fallback should be armed  : {arm_manual}")
    print("\n  This hand-operated LOW/TIGHT policy is a TEMPORARY Week 1 measure,")
    print("  not the final automated architecture.")

    if args.json_out:
        payload = {
            "generated_at": now.isoformat(),
            "recorded_runs": len(observations),
            "successful_runs": successful,
            "trigger_provenance": dict(sorted(provenance.items())),
            "observed_interval_minutes_all": overall,
            "observed_interval_minutes_external": external,
            "estimated_billed_minutes": len(observations),
            "next_checkpoint_label": protection.checkpoint_label,
            "minutes_to_checkpoint": protection.minutes_to_checkpoint,
            "checkpoint_window_minutes": protection.checkpoint_window_minutes,
            "protection_state": protection.state.value,
            "low_cadence_acceptable_now": low_ok,
            "tighten_by": protection.tighten_by.isoformat() if protection.tighten_by else None,
            "arm_manual_fallback": arm_manual,
            "policy": "TEMPORARY_WEEK1_MANUAL_LOW_TIGHT",
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"\nwrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
