#!/usr/bin/env python3
"""Research-only diagnostic over the structural candidate universe.

    python scripts/build_research_candidate_report.py --season 2026

*** THIS PRODUCES NO ACTIONABLE OUTPUT ***
Qualification is disabled pending a versioned, approved empirical
threshold artifact, and none exists. The report shows counts only: how
many expressions were formed, how they group, and why every one of them
is blocked. It contains no price ceilings, no allocations, and no
instructions of any kind.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.expression.corpus import load_contract_snapshots  # noqa: E402
from cfb_edge_finder.recommendation.card import (  # noqa: E402
    PORTFOLIO_LAYER_ABSENT,
    PortfolioBoundary,
)
from cfb_edge_finder.recommendation.eligibility import EligibilityConfig  # noqa: E402
from cfb_edge_finder.recommendation.pipeline import evidence_state_distribution, run_pipeline  # noqa: E402
from cfb_edge_finder.recommendation.scoring import SCORING_DISABLED  # noqa: E402
from cfb_edge_finder.research import persistence  # noqa: E402

SKELETON_CODE_VERSION = "recommendation_skeleton_v1"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--timing-label", default=None)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    obs_path = persistence.canonical_path(
        args.data_repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, args.season
    )
    loaded = load_contract_snapshots(obs_path, timing_label=args.timing_label)
    result = run_pipeline(loaded.snapshots, config=EligibilityConfig(), now=datetime.now(UTC))

    card = result.card
    diagnostics = card.diagnostics
    quality_failures = Counter(
        failure.value for r in result.eligibility_results for failure in r.quality_failures
    )

    print("# Research Candidate Diagnostic")
    print(f"\n*Generated {datetime.now(UTC).isoformat()} - {SKELETON_CODE_VERSION}*")
    print("\n**Research-only structural diagnostic.** Qualification is disabled; no actionable output exists.\n")

    print("## Universe")
    print(f"  games scanned            : {len(result.games)}")
    print(f"  contracts (tickers)      : {loaded.tickers_seen}")
    print(f"  candidate expressions    : {diagnostics.candidates_considered}")
    print(f"  malformed rows           : {loaded.malformed_rows}")

    print("\n## Grouping")
    print(f"  equivalence clusters     : {diagnostics.equivalence_clusters}")
    print(f"  multi-expression clusters: {diagnostics.multi_expression_clusters}")
    print(f"  dominated expressions    : {diagnostics.dominated_expressions}")
    print(f"  nested ladder groups     : {diagnostics.nested_ladder_groups}")
    print(f"  unresolved candidates    : {diagnostics.unresolved_candidates}")

    print("\n## Family research status")
    for family, status in sorted(result.family_statuses.items()):
        print(f"  {family:<10}: {status}")

    print("\n## Evidence readiness")
    for family, readiness in sorted(result.readiness_by_family.items()):
        print(f"  {family:<10}: {readiness.state.value} (settled n={readiness.settled_n}, "
              f"clusters={readiness.unique_game_clusters}) -- {readiness.detail}")
    print(f"  distribution            : {evidence_state_distribution(result)}")

    print("\n## Blocking")
    print(f"  blocked: qualification disabled : {diagnostics.candidates_blocked_qualification_disabled}")
    print(f"  blocked: data-quality gate      : {diagnostics.candidates_blocked_quality}")
    for failure, count in sorted(quality_failures.items()):
        print(f"    {failure:<28}: {count}")

    print("\n## Exposure (counted, never enforced)")
    print(f"  risk status                     : {diagnostics.risk_status}")
    print(f"  max expressions in one game     : {diagnostics.max_expressions_per_game_observed}")
    print(f"  max expressions on one event    : {diagnostics.max_expressions_per_equivalence_observed}")

    print("\n## Downstream stages")
    print(f"  card status                     : {card.status}")
    print(f"  ACTIONABLE CANDIDATES           : {card.actionable_count}")
    print(f"  card entries                    : {len(card.entries)}")
    print(f"  maximum acceptable price        : {card.maximum_acceptable_price.status}")
    print(f"  shadow mode                     : {card.shadow_status}")
    print(f"  scoring                         : {SCORING_DISABLED}")
    print(f"  portfolio/sizing layer          : {PortfolioBoundary().downstream_status}")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "skeleton_code_version": SKELETON_CODE_VERSION,
        "season": args.season,
        "runtime_seconds": round(time.perf_counter() - started, 4),
        "universe": {
            "games": len(result.games),
            "tickers": loaded.tickers_seen,
            "candidate_expressions": diagnostics.candidates_considered,
        },
        "grouping": {
            "equivalence_clusters": diagnostics.equivalence_clusters,
            "multi_expression_clusters": diagnostics.multi_expression_clusters,
            "dominated_expressions": diagnostics.dominated_expressions,
            "nested_ladder_groups": diagnostics.nested_ladder_groups,
            "unresolved_candidates": diagnostics.unresolved_candidates,
        },
        "family_statuses": result.family_statuses,
        "evidence_states": {f: r.state.value for f, r in result.readiness_by_family.items()},
        "blocking": {
            "qualification_disabled": diagnostics.candidates_blocked_qualification_disabled,
            "data_quality": diagnostics.candidates_blocked_quality,
            "quality_failure_breakdown": dict(quality_failures),
        },
        "downstream": {
            "card_status": card.status,
            "actionable_candidates": card.actionable_count,
            "card_entries": len(card.entries),
            "maximum_acceptable_price": card.maximum_acceptable_price.status,
            "shadow_status": card.shadow_status,
            "scoring_status": SCORING_DISABLED,
            "portfolio_status": PORTFOLIO_LAYER_ABSENT,
            "risk_status": diagnostics.risk_status,
        },
    }
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nWrote {args.json}")

    print(
        "\nSTATUS: RESEARCH-ONLY structural diagnostic. Qualification disabled, no validated thresholds, "
        "no sizing layer, no execution surface."
    )
    # A non-zero actionable count would be a safety defect, not a result.
    if card.actionable_count != 0:
        print("\nFATAL: actionable candidates were produced with qualification disabled.", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
