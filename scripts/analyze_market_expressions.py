#!/usr/bin/env python3
"""Research-only market-expression and correlation report.

    python scripts/analyze_market_expressions.py --season 2026
    python scripts/analyze_market_expressions.py --season 2026 --timing-label T_24H
    python scripts/analyze_market_expressions.py --season 2026 --json out.json

Organizes related Kalshi contracts into game-level structures, shows which
executable expressions settle on the same event, prices each expression
after fees, and flags structural anomalies in spread/total ladders.

*** RESEARCH-ONLY ***
No recommendation, no qualification tier, no staking, no order. Nothing
here selects a contract or ranks contracts by attractiveness. "Lowest
break-even expression" is an arithmetic fact about identical payouts, not
a suggestion to hold any of them.
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
from cfb_edge_finder.expression.grouping import build_universe  # noqa: E402
from cfb_edge_finder.expression.ladders import LadderAnomaly  # noqa: E402
from cfb_edge_finder.expression.taxonomy import MarketDimension  # noqa: E402
from cfb_edge_finder.research import persistence  # noqa: E402

EXPRESSION_CODE_VERSION = "expression_v1"


def _fmt(value, digits=4):
    return "-" if value is None else f"{value:.{digits}f}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument(
        "--timing-label", default=None,
        help="Restrict to one checkpoint (default: all, latest per ticker).",
    )
    parser.add_argument("--snapshot-selection", choices=["latest", "earliest"], default="latest")
    parser.add_argument("--max-examples", type=int, default=10)
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    started = time.perf_counter()
    obs_path = persistence.canonical_path(
        args.data_repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, args.season
    )
    loaded = load_contract_snapshots(
        obs_path, snapshot_selection=args.snapshot_selection, timing_label=args.timing_label
    )
    universe = build_universe(loaded.snapshots)

    families = Counter()
    for snapshot in loaded.snapshots:
        family = snapshot.semantics.family
        if snapshot.pricing_status == "model_priced" and family is not None:
            families[family.value] += 1

    ladder_counts = Counter(f.anomaly.value for f in universe.ladder_findings)
    multi = universe.multi_expression_groups
    cost_spreads = [g.all_in_cost_spread for g in multi if g.all_in_cost_spread is not None]

    margin_ladders = [
        ladder
        for game in universe.games.values()
        for dim, group in game.dimensions.items()
        if dim is MarketDimension.MARGIN
        for ladder in group.ladders.values()
    ]
    total_ladders = [
        ladder
        for game in universe.games.values()
        for dim, group in game.dimensions.items()
        if dim is MarketDimension.TOTAL
        for ladder in group.ladders.values()
    ]

    print("# Market Expression & Correlation Report")
    print(f"\n*Generated {datetime.now(UTC).isoformat()} - {EXPRESSION_CODE_VERSION}*")
    print("\n**Research-only.** Structural and arithmetic facts. No recommendation, tier, stake, or order.\n")

    print("## Corpus")
    print(f"  rows read                : {loaded.rows_read}")
    print(f"  distinct tickers         : {loaded.tickers_seen}")
    print(
        f"  snapshots collapsed      : {loaded.snapshots_collapsed} "
        f"(one snapshot per ticker, {args.snapshot_selection})"
    )
    print(f"  malformed rows           : {loaded.malformed_rows}")
    print(f"  timing labels present    : {dict(loaded.timing_labels)}")
    print(f"  model versions           : {sorted(universe.model_versions)}")
    print(f"  supported contracts      : {universe.contract_count}")
    print(f"    winner                 : {families.get('moneyline', 0)}")
    print(f"    spread                 : {families.get('spread', 0)}")
    print(f"    total                  : {families.get('total', 0)}")
    print(f"  unsupported/unpriced     : {len(universe.unsupported_tickers)}")
    print(f"  semantics unresolved     : {len(universe.unresolved_semantics_tickers)}")

    print("\n## Grouping hierarchy")
    print(f"  game groups              : {universe.game_group_count}")
    print(f"  dimension groups         : {universe.dimension_group_count}")
    print(f"  equivalence groups       : {universe.equivalence_group_count}")
    margin_rungs = sum(x.size for x in margin_ladders)
    total_rungs = sum(x.size for x in total_ladders)
    print(f"  nested margin ladders    : {len(margin_ladders)} (rungs: {margin_rungs})")
    print(f"  nested total ladders     : {len(total_ladders)} (rungs: {total_rungs})")
    print(
        "\n  NOTE: raw contract count is NOT an independent-sample count. "
        f"{universe.contract_count} contracts arise from {universe.game_group_count} games "
        f"and {universe.dimension_group_count} dimension groups."
    )

    print("\n## Exact equivalence")
    print(f"  equivalence groups                       : {universe.equivalence_group_count}")
    print(f"  groups with >1 executable expression     : {len(multi)}")
    print(f"  economically dominated equivalents       : {len(universe.dominance_findings)}")
    if cost_spreads:
        mean_spread = sum(cost_spreads) / len(cost_spreads)
        print(f"  all-in cost difference mean / max        : {_fmt(mean_spread)} / {_fmt(max(cost_spreads))}")
    else:
        print("  all-in cost difference mean / max        : - / - (no multi-expression group priceable both sides)")

    if universe.dominance_findings:
        print(f"\n  Examples (first {args.max_examples}):")
        for finding in sorted(universe.dominance_findings, key=lambda f: -f.cost_difference)[: args.max_examples]:
            cheap_side = finding.cheaper_side.value.upper()
            dom_side = finding.dominated_side.value.upper()
            print(f"    {finding.truth_condition_key}")
            print(
                f"      cheaper  : {finding.cheaper_ticker} {cheap_side:<3} "
                f"all-in {finding.cheaper_all_in_cost:.4f}"
            )
            print(
                f"      dominated: {finding.dominated_ticker} {dom_side:<3} "
                f"all-in {finding.dominated_all_in_cost:.4f} (+{finding.cost_difference:.4f}, "
                f"fee diff {finding.fee_difference:+.4f})"
            )

    print("\n## Ladder structure")
    for label, ladders in (("margin (spread)", margin_ladders), ("total", total_ladders)):
        sizes = [x.size for x in ladders]
        lo = min(sizes) if sizes else 0
        hi = max(sizes) if sizes else 0
        mean = (sum(sizes) / len(sizes)) if sizes else 0.0
        print(f"  {label:<16}: {len(ladders)} ladders | rungs min/mean/max {lo}/{mean:.1f}/{hi}")
    for anomaly in LadderAnomaly:
        print(f"  {anomaly.value:<32}: {ladder_counts.get(anomaly.value, 0)}")

    for anomaly in (LadderAnomaly.MODEL_MONOTONICITY_VIOLATION, LadderAnomaly.MARKET_LADDER_INCOHERENCE,
                    LadderAnomaly.MODEL_TIE_MASS):
        examples = [f for f in universe.ladder_findings if f.anomaly is anomaly]
        if not examples:
            continue
        print(f"\n  {anomaly.value} examples (first {min(args.max_examples, len(examples))} of {len(examples)}):")
        for finding in examples[: args.max_examples]:
            print(f"    {finding.ladder_key}: {finding.detail}")

    print("\n## Static price inconsistency (research diagnostic)")
    print(f"  complementary pairs flagged : {len(universe.static_inconsistencies)}")
    for finding in universe.static_inconsistencies[: args.max_examples]:
        print(
            f"    {finding.event_key} + {finding.complement_key}: combined all-in "
            f"{finding.combined_cost:.4f} < $1.00 (shortfall {finding.guaranteed_shortfall:.4f})"
        )
    if not universe.static_inconsistencies:
        print("    none -- captured quotes were mutually consistent on every complementary pair")

    payload = {
        "generated_at": datetime.now(UTC).isoformat(),
        "expression_code_version": EXPRESSION_CODE_VERSION,
        "season": args.season,
        "observations_path": str(obs_path),
        "runtime_seconds": round(time.perf_counter() - started, 4),
        "corpus": {
            "rows_read": loaded.rows_read,
            "distinct_tickers": loaded.tickers_seen,
            "snapshots_collapsed": loaded.snapshots_collapsed,
            "malformed_rows": loaded.malformed_rows,
            "timing_labels": loaded.timing_labels,
            "model_versions": sorted(universe.model_versions),
            "supported_contracts": universe.contract_count,
            "winner_contracts": families.get("moneyline", 0),
            "spread_contracts": families.get("spread", 0),
            "total_contracts": families.get("total", 0),
            "unsupported_or_unpriced": len(universe.unsupported_tickers),
            "semantics_unresolved": len(universe.unresolved_semantics_tickers),
        },
        "grouping": {
            "game_groups": universe.game_group_count,
            "dimension_groups": universe.dimension_group_count,
            "equivalence_groups": universe.equivalence_group_count,
            "multi_expression_groups": len(multi),
            "margin_ladders": len(margin_ladders),
            "total_ladders": len(total_ladders),
        },
        "findings": {
            "economically_dominated_equivalents": len(universe.dominance_findings),
            "mean_all_in_cost_difference": (sum(cost_spreads) / len(cost_spreads)) if cost_spreads else None,
            "max_all_in_cost_difference": max(cost_spreads) if cost_spreads else None,
            "ladder_anomalies": dict(ladder_counts),
            "static_price_inconsistencies": len(universe.static_inconsistencies),
        },
    }
    if args.json is not None:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8")
        print(f"\nWrote {args.json}")

    print(
        "\nSTATUS: RESEARCH-ONLY market-structure analysis. No bet recommendation, qualification tier, "
        "stake, or order anywhere in this output."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
