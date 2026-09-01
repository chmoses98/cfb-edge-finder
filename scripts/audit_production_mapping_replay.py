#!/usr/bin/env python3
"""Forensic replay of the EXACT production mapping/accounting path,
with zero CFBD requests.

`audit_mapping_coverage.py` classifies the whole unresolved population
but takes its schedule from a live CFBD fetch (metered) and maps against
the FULL season schedule. The scheduled collector
(`research_scan_and_capture.py`) does neither: it maps against the
NOT-STARTED slate served from the durable `football_state` artifact, and
its health check counts `mapping_failures` per MARKET under each failed
EVENT (`scan_logic.is_genuine_mapping_failure`). When a live health
alarm needs a forensic answer, the replay has to reproduce those exact
choices -- this script does, reading the artifact via
`football_state.load_football_state_from_git` (read-only, no checkout,
no CFBD call) and reusing the production fetch/evidence/mapping helpers
verbatim.

    python scripts/audit_production_mapping_replay.py \\
        --schedule-season 2026 --json out.json

Read-only diagnosis: nothing is captured, priced, persisted, or pushed.

Reports BOTH failure rates (one unresolved event fans out across its
whole contract ladder, so market-level counts overstate event-level
breakage), and decomposes every genuine-failure event by what each raw
side actually is: FBS-registry resolved, known FCS school, known non-FBS
program in another division (CFBD /teams classification: ii/iii/...), or
genuinely unknown. The decomposition is exact-match only -- the same
no-fuzzy-matching philosophy as `teams.registry` and
`teams.fcs_identity`.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.kalshi.game_mapping import _split_title, map_kalshi_event_to_game  # noqa: E402
from cfb_edge_finder.research import football_state, scan_logic  # noqa: E402
from cfb_edge_finder.teams.fcs_identity import (  # noqa: E402
    is_known_fcs_school,
    is_known_non_fbs_school,
    normalize_school_name,
)
from cfb_edge_finder.teams.registry import (  # noqa: E402
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    resolve_team_alias,
)


def _load_capture_module():
    """Same pattern as audit_mapping_coverage.py: import the Milestone D
    script as a module so this replay reuses its exact fetch/evidence
    helpers instead of a second, possibly-diverging implementation."""
    spec = importlib.util.spec_from_file_location(
        "capture_for_production_replay", REPO_ROOT / "scripts" / "capture_kalshi_cfb_snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _side_status(
    raw_name: str | None,
    fcs_school_names: frozenset[str],
    non_fbs_school_names: frozenset[str],
    cls_by_school: dict[str, str],
) -> str:
    """What ONE raw Kalshi side deterministically is. Exact match only."""
    if not raw_name:
        return "missing"
    try:
        resolve_team_alias(raw_name)
        return "fbs_registry"
    except AmbiguousTeamAliasError:
        return "ambiguous_alias"
    except UnknownTeamAliasError:
        pass
    if is_known_fcs_school(raw_name, fcs_school_names):
        return "known_fcs"
    division = cls_by_school.get(normalize_school_name(raw_name))
    if division:
        return f"known_division_{division}"
    if is_known_non_fbs_school(raw_name, non_fbs_school_names):
        return "known_non_fbs_variant"
    return "unknown"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schedule-season", type=int, default=2026)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--data-branch", default="research-data")
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--max-samples", type=int, default=10)
    args = parser.parse_args()

    now = datetime.now(UTC)
    state, verdict = football_state.load_football_state_from_git(
        args.data_repo_dir, args.data_branch, args.schedule_season
    )
    if state is None:
        print(f"ERROR: durable football_state unavailable ({verdict}); nothing to replay.", file=sys.stderr)
        return 2
    inputs = state.to_scan_inputs(now)
    games = inputs.games
    not_started = [g for g in games if g.status == "scheduled"]
    fcs_school_names = inputs.fcs_school_names
    non_fbs_school_names = inputs.non_fbs_school_names
    cls_by_school = {
        normalize_school_name(str(row.get("school"))): str(row.get("classification"))
        for row in state.all_division_teams
        if row.get("school") and row.get("classification")
    }

    print(f"football_state           : {verdict} (schedule_fetched_at={state.schedule_fetched_at.isoformat()})")
    print(f"schedule games           : {len(games)} total, {len(not_started)} not started (production pool)")
    print(f"known FCS school names   : {len(fcs_school_names)}")

    capture = _load_capture_module()
    kalshi = KalshiClient()

    events: list[dict] = []
    api_failures = 0
    for series_ticker, family in capture.CORE_V1_SERIES_TO_FAMILY.items():
        markets, failed = capture._fetch_active_markets_with_status(kalshi, series_ticker)  # noqa: SLF001
        if failed:
            api_failures += 1
        by_event: dict[str, list[dict]] = {}
        for market in markets:
            by_event.setdefault(str(market.get("event_ticker", "")), []).append(market)
        for event_ticker, event_markets in by_event.items():
            evidence = capture._evidence_from_market(event_markets[0], event_ticker)  # noqa: SLF001
            mapping = map_kalshi_event_to_game(
                evidence, not_started,
                fcs_school_names=fcs_school_names,
                non_fbs_school_names=non_fbs_school_names,
            )
            mapping_full = map_kalshi_event_to_game(
                evidence, games,
                fcs_school_names=fcs_school_names,
                non_fbs_school_names=non_fbs_school_names,
            )
            if evidence.raw_home_name and evidence.raw_away_name:
                raw_pair = (evidence.raw_home_name, evidence.raw_away_name)
            else:
                raw_pair = _split_title(evidence.title) if evidence.title else None
            side_a = raw_pair[0] if raw_pair else None
            side_b = raw_pair[1] if raw_pair else None
            events.append(
                {
                    "series": series_ticker,
                    "family": family.value,
                    "event_ticker": event_ticker,
                    "n_markets": len(event_markets),
                    "matchup": evidence.title,
                    "reason": mapping.reason.value if mapping.reason else "identity_mapped",
                    "reason_full_pool": mapping_full.reason.value if mapping_full.reason else "identity_mapped",
                    "genuine_failure": scan_logic.is_genuine_mapping_failure(mapping.reason),
                    "detail": mapping.detail[:220],
                    "side_a": side_a,
                    "side_b": side_b,
                    "side_a_status": _side_status(side_a, fcs_school_names, non_fbs_school_names, cls_by_school),
                    "side_b_status": _side_status(side_b, fcs_school_names, non_fbs_school_names, cls_by_school),
                    "game_id": mapping.game_id,
                }
            )

    n_events = len(events)
    n_markets = sum(e["n_markets"] for e in events)
    failed_events = [e for e in events if e["genuine_failure"]]
    failed_markets = sum(e["n_markets"] for e in failed_events)

    print(f"\nkalshi api failures      : {api_failures}")
    print(f"events scanned           : {n_events}")
    print(f"markets scanned          : {n_markets}")
    if n_events and n_markets:
        print(
            f"genuine-failure EVENTS   : {len(failed_events)} ({len(failed_events) / n_events:.1%})\n"
            f"genuine-failure MARKETS  : {failed_markets} ({failed_markets / n_markets:.1%})"
            "   <- the collector's mapping_failures numerator"
        )

    print("\n=== taxonomy by production mapping reason (events / markets) ===")
    reason_events: Counter[str] = Counter()
    reason_markets: Counter[str] = Counter()
    for e in events:
        reason_events[e["reason"]] += 1
        reason_markets[e["reason"]] += e["n_markets"]
    for reason, count in reason_events.most_common():
        mk = reason_markets[reason]
        print(f"    {reason:<28} events={count:>5} ({count / n_events:6.1%})   markets={mk:>5} ({mk / n_markets:6.1%})")

    print("\n=== genuine-failure sub-buckets by (reason, side_a, side_b) ===")
    sub: Counter[tuple[str, str, str]] = Counter()
    sub_markets: Counter[tuple[str, str, str]] = Counter()
    for e in failed_events:
        key = (e["reason"], e["side_a_status"], e["side_b_status"])
        sub[key] += 1
        sub_markets[key] += e["n_markets"]
    for key, count in sub.most_common():
        print(f"    events={count:>5} markets={sub_markets[key]:>5}  {key}")

    print("\n=== failures whose reason changes when mapped against the FULL schedule ===")
    pool_diff = Counter(
        (e["reason"], e["reason_full_pool"]) for e in events if e["reason"] != e["reason_full_pool"]
    )
    if not pool_diff:
        print("    (none -- candidate-pool status filtering changes nothing)")
    for (prod, full), count in pool_diff.most_common():
        print(f"    {count:>5}  production={prod}  full_schedule={full}")

    print("\n=== unknown tokens among failures (neither FBS registry, FCS list, nor CFBD /teams) ===")
    unknown: Counter[str] = Counter()
    for e in failed_events:
        for side, status in ((e["side_a"], e["side_a_status"]), (e["side_b"], e["side_b_status"])):
            if status == "unknown" and side:
                unknown[side] += e["n_markets"]
    for token, count in unknown.most_common(30):
        print(f"    {count:>5}  {token!r}")
    if not unknown:
        print("    (none)")

    print("\n=== sample failing events per sub-bucket ===")
    shown: set[tuple[str, str, str]] = set()
    per_bucket: Counter[tuple[str, str, str]] = Counter()
    for e in failed_events:
        key = (e["reason"], e["side_a_status"], e["side_b_status"])
        per_bucket[key] += 1
        if per_bucket[key] > args.max_samples or (key in shown and per_bucket[key] > 3):
            continue
        shown.add(key)
        print(f"    {e['event_ticker']:<34} n={e['n_markets']:<3} {e['matchup']!r}")
        print(f"        {key}  detail={e['detail'][:140]}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "generated_at": now.isoformat(),
                    "schedule_fetched_at": state.schedule_fetched_at.isoformat(),
                    "events_scanned": n_events,
                    "markets_scanned": n_markets,
                    "genuine_failure_events": len(failed_events),
                    "genuine_failure_markets": failed_markets,
                    "api_failures": api_failures,
                    "reason_events": dict(reason_events),
                    "reason_markets": dict(reason_markets),
                    "events": events,
                },
                indent=1,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    print("\nSTATUS: read-only forensic replay. Nothing captured, priced, persisted, or recommended.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
