#!/usr/bin/env python3
"""Milestone D closure, item 1: root-cause audit of the live capture's
AMBIGUOUS_TEAM_MAPPING population (1,902 observations in the prior live
run). Read-only; reuses the EXACT production code path
(`capture_kalshi_cfb_snapshot.py`'s own fetch helpers +
`kalshi.game_mapping.map_kalshi_event_to_game`) rather than
reimplementing mapping logic, so this audit's findings describe the real
pipeline's real behavior, not a parallel approximation of it.

    python scripts/audit_ambiguous_team_mappings.py --schedule-season 2026

For every discovered CORE_V1 event whose mapping lands on
AMBIGUOUS_TEAM_MAPPING, this script independently reconstructs WHY: which
raw token(s) failed, whether each failure was a genuine
`AmbiguousTeamAliasError` (a real collision, e.g. bare "Miami") or a
genuine `UnknownTeamAliasError` (the token simply isn't in
`teams.registry` at all), and whether the failing token is itself a known
FCS school name (per the same `fcs_school_names` set the live capture
already builds) -- since a Milestone D hardening pass already showed that
"unknown token" and "FCS identity" are NOT the same thing, and this
enum's own docstring already documents `AMBIGUOUS_TEAM_MAPPING` as
covering BOTH real ambiguity and real unknown-token failures (a
conflation this audit is specifically meant to unpack, not paper over).

Never modifies mapping/alias logic itself -- this is diagnosis only."""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason  # noqa: E402
from cfb_edge_finder.kalshi.game_mapping import _split_title, map_kalshi_event_to_game  # noqa: E402
from cfb_edge_finder.teams.fcs_identity import is_known_fcs_school  # noqa: E402
from cfb_edge_finder.teams.registry import (  # noqa: E402
    AmbiguousTeamAliasError,
    UnknownTeamAliasError,
    resolve_team_alias,
)


def _load_capture_module():
    """Imports capture_kalshi_cfb_snapshot.py as a module (mirrors
    tests/test_kalshi_milestone_d_guards.py's own pattern) so this audit
    reuses its exact fetch/evidence helpers instead of duplicating them
    with a second, possibly-diverging implementation."""
    repo_root = Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "capture_kalshi_cfb_snapshot_for_audit", repo_root / "scripts" / "capture_kalshi_cfb_snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _classify_side(raw_name: str, fcs_school_names: frozenset[str]) -> dict:
    """Independently resolves ONE side, exactly like
    `game_mapping._resolve_one`, and adds an FCS-identity cross-check --
    returns a small dict, not a tuple, so the aggregation code below stays
    readable."""
    try:
        team_id = resolve_team_alias(raw_name)
        return {"raw": raw_name, "outcome": "resolved", "team_id": team_id}
    except AmbiguousTeamAliasError as exc:
        return {"raw": raw_name, "outcome": "ambiguous", "detail": str(exc)}
    except UnknownTeamAliasError:
        is_fcs = is_known_fcs_school(raw_name, fcs_school_names)
        return {"raw": raw_name, "outcome": "unknown", "is_known_fcs": is_fcs}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schedule-season", type=int, default=2026)
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.cfbd_api_key:
        print("ERROR: CFBD_API_KEY not set -- this audit requires a genuine live schedule fetch.", file=sys.stderr)
        return 2

    capture = _load_capture_module()
    captured_at = datetime.now(UTC)
    cfbd_client = CFBDClient(api_key=settings.cfbd_api_key)
    try:
        candidate_games, _classification_by_game_id = capture._fetch_candidate_games(
            args.schedule_season, cfbd_client, captured_at
        )
        fcs_school_names = capture._fetch_fcs_school_names(cfbd_client, args.schedule_season)
    except CFBDAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print(f"Fetched {len(candidate_games)} candidate games, {len(fcs_school_names)} known FCS school names.")

    kalshi_client = KalshiClient()

    token_failure_counts: Counter[str] = Counter()
    token_outcome: dict[str, str] = {}
    family_counts: Counter[str] = Counter()
    event_ticker_ambiguous_count: Counter[str] = Counter()
    mechanism_counts: Counter[str] = Counter()
    total_ambiguous = 0
    sample_rows: list[dict] = []

    for series_ticker, family in capture.CORE_V1_SERIES_TO_FAMILY.items():
        markets = capture._fetch_active_markets_safe(kalshi_client, series_ticker)
        markets_by_event: dict[str, list[dict]] = {}
        for market in markets:
            event_ticker = str(market.get("event_ticker", ""))
            markets_by_event.setdefault(event_ticker, []).append(market)

        for event_ticker, event_markets in markets_by_event.items():
            probe_market = event_markets[0]
            evidence = capture._evidence_from_market(probe_market, event_ticker)
            mapping = map_kalshi_event_to_game(evidence, candidate_games, fcs_school_names=fcs_school_names)

            if mapping.reason != KalshiCfbCoverageReason.AMBIGUOUS_TEAM_MAPPING:
                continue

            # This event's AMBIGUOUS_TEAM_MAPPING applies to EVERY market
            # under it (every threshold/team rung shares the same failed
            # game-level mapping) -- count observations, not just events.
            n_observations_this_event = len(event_markets)
            total_ambiguous += n_observations_this_event
            family_counts[family.value] += n_observations_this_event
            event_ticker_ambiguous_count[event_ticker] += n_observations_this_event

            split = None
            if evidence.raw_home_name and evidence.raw_away_name:
                split = (evidence.raw_home_name, evidence.raw_away_name)
            elif evidence.title:
                split = _split_title(evidence.title)

            if split is None:
                mechanism_counts["ticker_grammar_unsplittable_title"] += n_observations_this_event
                sample_rows.append(
                    {
                        "event_ticker": event_ticker,
                        "family": family.value,
                        "title": evidence.title,
                        "mechanism": "unsplittable_title",
                    }
                )
                continue

            first_raw, second_raw = split
            first = _classify_side(first_raw, fcs_school_names)
            second = _classify_side(second_raw, fcs_school_names)

            for side in (first, second):
                if side["outcome"] in ("unknown", "ambiguous"):
                    token_failure_counts[side["raw"]] += n_observations_this_event
                    token_outcome[side["raw"]] = side["outcome"]

            # Mechanism classification, per the mission's own candidate list.
            outcomes = {first["outcome"], second["outcome"]}
            if "ambiguous" in outcomes:
                mechanism_counts["duplicate_alias_generic_name"] += n_observations_this_event
            elif first["outcome"] == "unknown" and second["outcome"] == "unknown":
                both_fcs = first.get("is_known_fcs") and second.get("is_known_fcs")
                one_fcs = first.get("is_known_fcs") or second.get("is_known_fcs")
                if both_fcs:
                    # Should be structurally impossible (map_kalshi_event_to_game
                    # already special-cases both-FCS) -- flagged if seen, since
                    # it would indicate a real bug in that branch.
                    mechanism_counts["UNEXPECTED_both_sides_fcs_but_not_classified_fcs_vs_fcs"] += (
                        n_observations_this_event
                    )
                elif one_fcs:
                    mechanism_counts["mixed_one_side_fcs_one_side_unknown_fbs_alias_gap"] += n_observations_this_event
                else:
                    mechanism_counts["unknown_token_not_fcs_possible_alias_gap_or_novel_program"] += (
                        n_observations_this_event
                    )
            else:
                # exactly one side unknown, other side resolved -- a real,
                # specific alias gap on one side only.
                mechanism_counts["single_side_alias_gap"] += n_observations_this_event

            if len(sample_rows) < 60:
                sample_rows.append(
                    {
                        "event_ticker": event_ticker,
                        "family": family.value,
                        "first": first,
                        "second": second,
                    }
                )

    print(f"\n=== AMBIGUOUS_TEAM_MAPPING audit: {total_ambiguous} observations ===")
    print(f"\nMechanism counts: {dict(mechanism_counts)}")
    print(f"\nCount by market family: {dict(family_counts)}")
    print(f"\nDistinct ambiguous events: {len(event_ticker_ambiguous_count)}")
    print("Top 20 events by observation count:")
    for event_ticker, count in event_ticker_ambiguous_count.most_common(20):
        print(f"  {event_ticker}: {count}")

    print(f"\nDistinct failing tokens: {len(token_failure_counts)}")
    print("Top 40 failing tokens by observation count (token: count, outcome):")
    for token, count in token_failure_counts.most_common(40):
        print(f"  {token!r}: {count} ({token_outcome[token]})")

    print("\nSample rows (up to 60, full detail):")
    for row in sample_rows:
        print(f"  {row}")

    print("\nSTATUS: DIAGNOSIS-ONLY. No mapping/alias logic modified by this script.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
