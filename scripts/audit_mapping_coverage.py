#!/usr/bin/env python3
"""Classify the ENTIRE unresolved Kalshi population into explicit reasons.

Read-only diagnosis. Reuses the exact production path
(`capture_kalshi_cfb_snapshot`'s fetch/evidence helpers plus
`kalshi.game_mapping.map_kalshi_event_to_game`), so what it reports is
what the collector actually does -- not a parallel approximation.

*** THE QUESTION THIS ANSWERS ***

Live runs report ~1,400 genuinely-unresolved markets against ~4,578
discovered. That number alone says nothing about whether we are LOSING
anything: an unresolved FCS-vs-FCS market is a correctly-declined
population, while an unresolved FBS-vs-FBS market is a missed research
opportunity. Those two must never sit in the same bucket.

Every unresolved market is therefore assigned exactly one category, and
the categories are asserted to reconcile to the unresolved total. The
metric that matters is the FBS-vs-FBS count, not the headline.

`audit_ambiguous_team_mappings.py` already unpacks AMBIGUOUS_TEAM_MAPPING
in depth; this covers the whole population and is deliberately the
broader, shallower view.
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

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.data.kalshi_client import KalshiClient  # noqa: E402
from cfb_edge_finder.kalshi.cfb_coverage_reason import KalshiCfbCoverageReason  # noqa: E402
from cfb_edge_finder.kalshi.game_mapping import _split_title, map_kalshi_event_to_game  # noqa: E402
from cfb_edge_finder.teams.fcs_identity import is_known_fcs_school  # noqa: E402
from cfb_edge_finder.teams.registry import (  # noqa: E402
    REGISTRY,
    AmbiguousTeamAliasError,
    Subdivision,
    UnknownTeamAliasError,
    resolve_team_alias,
)

# The mission's categories. Every unresolved market gets exactly one.
FBS_VS_FBS_POTENTIAL_LEAK = "FBS_VS_FBS_POTENTIAL_LEAK"
FBS_VS_FCS_UNSUPPORTED = "FBS_VS_FCS_UNSUPPORTED"
FCS_VS_FCS_UNSUPPORTED = "FCS_VS_FCS_UNSUPPORTED"
FUTURES_OR_NON_CORE = "FUTURES_OR_NON_CORE"
AMBIGUOUS_TEAM_NAME = "AMBIGUOUS_TEAM_NAME"
DETERMINISTIC_ALIAS_MISSING = "DETERMINISTIC_ALIAS_MISSING"
MALFORMED_OR_UNSUPPORTED_MARKET = "MALFORMED_OR_UNSUPPORTED_MARKET"
OTHER_EXPLICIT_REASON = "OTHER_EXPLICIT_REASON"


def _load_capture_module():
    spec = importlib.util.spec_from_file_location(
        "capture_for_coverage_audit", REPO_ROOT / "scripts" / "capture_kalshi_cfb_snapshot.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _side_identity(raw_name: str | None, fcs_school_names: frozenset[str], fbs_ids: set[str]) -> dict:
    """What ONE raw Kalshi side actually is.

    Distinguishes four things the headline count conflates: a clean FBS
    resolve, a clean non-FBS resolve, a genuinely ambiguous token (bare
    "Miami"), and an unknown token -- with unknown further split by
    whether it is a recognised FCS school, because "not in our registry"
    and "is an FCS team" are different facts."""
    if not raw_name:
        return {"raw": raw_name, "kind": "missing"}
    try:
        team_id = resolve_team_alias(raw_name)
    except AmbiguousTeamAliasError as exc:
        return {"raw": raw_name, "kind": "ambiguous", "detail": str(exc)[:160]}
    except UnknownTeamAliasError:
        if is_known_fcs_school(raw_name, fcs_school_names):
            return {"raw": raw_name, "kind": "known_fcs"}
        return {"raw": raw_name, "kind": "unknown"}
    return {
        "raw": raw_name,
        "kind": "fbs" if team_id in fbs_ids else "non_fbs",
        "team_id": team_id,
    }


def classify_unresolved(reason, home: dict, away: dict) -> tuple[str, str]:
    """(category, why) for one unresolved market. Deny-by-default: an
    unrecognised shape lands in OTHER_EXPLICIT_REASON rather than being
    quietly folded into a benign bucket."""
    if reason == KalshiCfbCoverageReason.NON_GAME_FUTURES:
        return FUTURES_OR_NON_CORE, "series is a futures/non-single-game market"

    kinds = {home.get("kind"), away.get("kind")}

    # Populations we deliberately do not price. Checked BEFORE alias
    # gaps: an FCS school missing from the registry is expected, not a
    # defect, because the registry is an FBS registry by design.
    if "known_fcs" in kinds:
        if kinds <= {"known_fcs"}:
            return FCS_VS_FCS_UNSUPPORTED, "both sides are known FCS schools"
        if "fbs" in kinds:
            return FBS_VS_FCS_UNSUPPORTED, "one side FBS, one side a known FCS school"
        # One side is definitely an FCS school; the other could not be
        # pinned down. Either way the fixture involves an FCS team and is
        # a population we decline, so it is bucketed here -- but the
        # reason says plainly that the opposite side is undetermined,
        # rather than silently asserting FCS-vs-FCS.
        return (
            FCS_VS_FCS_UNSUPPORTED,
            "one side is a known FCS school; opposite side undetermined -- unsupported either way",
        )

    if "ambiguous" in kinds:
        return AMBIGUOUS_TEAM_NAME, "a raw name maps to more than one team; refusing to guess"

    if "missing" in kinds:
        return MALFORMED_OR_UNSUPPORTED_MARKET, "market carries no usable team names"

    if kinds <= {"fbs"}:
        # Both sides are real FBS teams and still nothing mapped -- the
        # only shape that can cost us a research opportunity.
        return FBS_VS_FBS_POTENTIAL_LEAK, "both sides resolve to FBS teams but no scheduled game matched"

    if "unknown" in kinds:
        return DETERMINISTIC_ALIAS_MISSING, "a token is in neither the FBS registry nor the FCS school list"

    if "non_fbs" in kinds:
        return FBS_VS_FCS_UNSUPPORTED, "a side resolves to a non-FBS registry team"

    return OTHER_EXPLICIT_REASON, f"unhandled shape: reason={reason} kinds={sorted(k or '?' for k in kinds)}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--schedule-season", type=int, default=2026)
    parser.add_argument("--json", type=Path, default=None)
    parser.add_argument("--max-samples", type=int, default=25)
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.cfbd_api_key:
        print("ERROR: CFBD_API_KEY not set -- this audit requires a genuine live schedule.", file=sys.stderr)
        return 2

    capture = _load_capture_module()
    now = datetime.now(UTC)
    cfbd = CFBDClient(api_key=settings.cfbd_api_key)
    try:
        games, classification = capture._fetch_candidate_games(args.schedule_season, cfbd, now)  # noqa: SLF001
        fcs_school_names = capture._fetch_fcs_school_names(cfbd, args.schedule_season)  # noqa: SLF001
    except CFBDAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    fbs_ids = {t.team_id for t in REGISTRY if t.subdivision is Subdivision.FBS}
    print(f"schedule games         : {len(games)}")
    print(f"known FCS school names : {len(fcs_school_names)}")
    print(f"FBS teams in registry  : {len(fbs_ids)}")

    kalshi = KalshiClient()
    reason_counts: Counter[str] = Counter()
    category_counts: Counter[str] = Counter()
    family_counts: Counter[str] = Counter()
    population_counts: Counter[str] = Counter()
    api_failures = 0
    total_markets = 0
    unresolved_markets = 0
    leak_samples: list[dict] = []
    alias_gap_tokens: Counter[str] = Counter()
    samples: dict[str, list[dict]] = {}

    for series_ticker, family in capture.CORE_V1_SERIES_TO_FAMILY.items():
        markets, failed = capture._fetch_active_markets_with_status(kalshi, series_ticker)  # noqa: SLF001
        if failed:
            api_failures += 1
        by_event: dict[str, list[dict]] = {}
        for market in markets:
            by_event.setdefault(str(market.get("event_ticker", "")), []).append(market)

        for event_ticker, event_markets in by_event.items():
            n = len(event_markets)
            total_markets += n
            family_counts[family.value] += n

            evidence = capture._evidence_from_market(event_markets[0], event_ticker)  # noqa: SLF001
            mapping = map_kalshi_event_to_game(evidence, games, fcs_school_names=fcs_school_names)
            reason = mapping.reason
            reason_counts[str(reason.value) if reason else "mapped"] += n

            if mapping.game_id:
                pair = classification.get(mapping.game_id, (None, None))
                population_counts[f"{pair[0]}_vs_{pair[1]}"] += n

            if not capture_is_unresolved(reason):
                continue

            unresolved_markets += n
            # Sides come from raw_home/away when present, else from the
            # title -- the SAME order map_kalshi_event_to_game itself
            # uses. A first pass read only the raw fields, which are unset
            # for these events, so every market looked "malformed" and
            # the whole population collapsed into one bucket.
            if evidence.raw_home_name and evidence.raw_away_name:
                raw_pair = (evidence.raw_home_name, evidence.raw_away_name)
            else:
                raw_pair = _split_title(evidence.title) if evidence.title else None
            if raw_pair is None:
                home = {"raw": None, "kind": "missing"}
                away = {"raw": None, "kind": "missing"}
            else:
                home = _side_identity(raw_pair[0], fcs_school_names, fbs_ids)
                away = _side_identity(raw_pair[1], fcs_school_names, fbs_ids)
            category, why = classify_unresolved(reason, home, away)
            category_counts[category] += n

            row = {
                "event_ticker": event_ticker,
                "market_ticker": event_markets[0].get("ticker"),
                "family": family.value,
                "reason": str(reason.value) if reason else None,
                "raw_home": home.get("raw"),
                "raw_away": away.get("raw"),
                "home_kind": home.get("kind"),
                "away_kind": away.get("kind"),
                "markets_in_event": n,
                "category": category,
                "why": why,
                "title": (evidence.title or "")[:120],
            }
            samples.setdefault(category, [])
            if len(samples[category]) < args.max_samples:
                samples[category].append(row)
            if category == FBS_VS_FBS_POTENTIAL_LEAK:
                leak_samples.append(row)
            if category == DETERMINISTIC_ALIAS_MISSING:
                for side in (home, away):
                    if side.get("kind") == "unknown":
                        alias_gap_tokens[str(side.get("raw"))] += n

    print(f"\ntotal core markets     : {total_markets}")
    print(f"api failures (series)  : {api_failures}")
    print(f"unresolved markets     : {unresolved_markets}")
    print(f"\nby family              : {dict(family_counts)}")
    print(f"mapped populations     : {dict(population_counts)}")
    print("\nraw coverage reasons   :")
    for reason, count in reason_counts.most_common():
        print(f"    {reason:<32}: {count}")

    print(f"\n=== UNRESOLVED CLASSIFICATION (must reconcile to {unresolved_markets}) ===")
    for category, count in category_counts.most_common():
        print(f"    {category:<34}: {count}")
    total_classified = sum(category_counts.values())
    print(f"    {'TOTAL':<34}: {total_classified}")
    reconciles = total_classified == unresolved_markets
    print(f"    reconciles: {reconciles}")

    print(f"\n=== FBS-vs-FBS POTENTIAL LEAKS: {len(leak_samples)} events ===")
    for row in leak_samples[: args.max_samples]:
        print(f"    {row['event_ticker']}  {row['raw_away']} @ {row['raw_home']}  ({row['reason']})")

    if alias_gap_tokens:
        print("\n=== UNKNOWN TOKENS (neither FBS registry nor FCS list), top 25 ===")
        for token, count in alias_gap_tokens.most_common(25):
            print(f"    {count:>5}  {token}")

    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(
            json.dumps(
                {
                    "total_markets": total_markets,
                    "unresolved_markets": unresolved_markets,
                    "api_failures": api_failures,
                    "reason_counts": dict(reason_counts),
                    "category_counts": dict(category_counts),
                    "family_counts": dict(family_counts),
                    "population_counts": dict(population_counts),
                    "reconciles": reconciles,
                    "fbs_vs_fbs_leaks": leak_samples,
                    "unknown_tokens": dict(alias_gap_tokens.most_common(100)),
                    "samples": samples,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        print(f"\nwrote {args.json}")

    print("\nSTATUS: read-only diagnosis. Nothing priced, captured, or recommended.")
    return 0 if reconciles else 1


def capture_is_unresolved(reason) -> bool:
    """Unresolved == the collector could not tie this market to a game it
    would price. Uses the SAME reason set the health check counts, so the
    audit's denominator matches the collector's own."""
    from cfb_edge_finder.research.scan_logic import is_genuine_mapping_failure

    return is_genuine_mapping_failure(reason) or reason == KalshiCfbCoverageReason.NON_GAME_FUTURES


if __name__ == "__main__":
    raise SystemExit(main())
