#!/usr/bin/env python3
"""Offline empirical threshold DISCOVERY. Cannot approve anything.

Reads settled prospective observations, evaluates the prespecified
protocol slices, and emits DRAFT_RESEARCH_FINDING objects with
game-clustered intervals.

It ranks nothing by profitability, invents no cut point, and has no code
path to an approved threshold artifact. With zero settled games -- the
current state -- it prints
EMPIRICAL_THRESHOLD_RESEARCH_BLOCKED_ON_SAMPLE and exits 0, which is the
correct and expected result, not a failure.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.protocol import manifest  # noqa: E402
from cfb_edge_finder.research.threshold_discovery import (  # noqa: E402
    BLOCKED_ON_SAMPLE,
    SettledResearchObservation,
    discover_threshold_candidates,
)

SUPPORTED_FAMILIES = frozenset({"moneyline", "spread", "total"})


def load_settled_observations(
    observations_path: Path, settlements_path: Path
) -> tuple[list[SettledResearchObservation], dict[str, int]]:
    """Join prospective observations to TERMINAL settlements.

    One pass over each file with a dict index, never a nested scan: the
    ledger is already thousands of rows and will be hundreds of thousands
    by season's end.

    Only `status == "settled"` counts. A settlement row exists for every
    market the settler has looked at, including games that have not
    kicked off."""
    stats = {
        "observation_rows": 0,
        "settlement_rows": 0,
        "terminal_settlements": 0,
        "excluded_not_prospective": 0,
        "excluded_unsupported_family": 0,
        "excluded_unpriced": 0,
        "excluded_fee_unverified": 0,
        "excluded_no_settlement": 0,
        "joined": 0,
    }

    settled_by_ticker: dict[str, dict] = {}
    if settlements_path.exists():
        with settlements_path.open(encoding="utf-8") as handle:
            for line in handle:
                if not line.strip():
                    continue
                row = json.loads(line)
                stats["settlement_rows"] += 1
                if row.get("status") != "settled":
                    continue
                stats["terminal_settlements"] += 1
                ticker = row.get("kalshi_market_ticker")
                if ticker:
                    settled_by_ticker[ticker] = row

    out: list[SettledResearchObservation] = []
    if not observations_path.exists():
        return out, stats

    with observations_path.open(encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            row = json.loads(line)
            stats["observation_rows"] += 1
            obs = row.get("observation") or {}

            if row.get("capture_mode") != "PROSPECTIVE":
                stats["excluded_not_prospective"] += 1
                continue
            family = obs.get("family")
            if family not in SUPPORTED_FAMILIES:
                stats["excluded_unsupported_family"] += 1
                continue
            if obs.get("pricing_status") != "model_priced" or obs.get("model_probability") is None:
                stats["excluded_unpriced"] += 1
                continue
            if obs.get("fee_status") != "VERIFIED_CURRENT":
                stats["excluded_fee_unverified"] += 1
                continue

            ticker = obs.get("kalshi_market_ticker")
            settlement = settled_by_ticker.get(ticker)
            if settlement is None:
                stats["excluded_no_settlement"] += 1
                continue

            price = obs.get("executable_yes_price")
            fee = obs.get("estimated_taker_fee") or 0.0
            model_version = (obs.get("model_version") or {}).get("model_version")
            if price is None or not model_version:
                stats["excluded_unpriced"] += 1
                continue

            out.append(
                SettledResearchObservation(
                    game_id=obs.get("game_id", ""),
                    market_ticker=ticker,
                    family=family,
                    timing_label=(obs.get("snapshot_timing") or {}).get("label", "unknown"),
                    model_version=model_version,
                    side="yes",
                    executable_price=float(price),
                    fee_adjusted_break_even=float(price) + float(fee),
                    model_probability=float(obs["model_probability"]),
                    settled_yes=settlement.get("derived_contract_settlement") == "yes",
                    capture_mode="PROSPECTIVE",
                )
            )
            stats["joined"] += 1
    return out, stats


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument(
        "--minimum-settled-games",
        type=int,
        default=None,
        help="The independent-game sample YOU will accept for a slice. No default: "
        "inferring it from the data it will be applied to is circular, so omitting it "
        "produces a refusal on every slice.",
    )
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    base = args.data_repo_dir / "data" / "research"
    observations, stats = load_settled_observations(
        base / "observations" / f"{args.season}.jsonl",
        base / "settlements" / f"{args.season}.jsonl",
    )
    report = discover_threshold_candidates(
        observations, minimum_settled_games=args.minimum_settled_games
    )

    m = manifest()
    print("=" * 74)
    print("EMPIRICAL THRESHOLD DISCOVERY (research only -- cannot approve anything)")
    print("=" * 74)
    print(f"  protocol            : {m.version}")
    print(f"  protocol sha256     : {m.document_sha256}")
    print(f"  cluster unit        : {m.cluster_unit}")
    print()
    for key, value in stats.items():
        print(f"  {key:28}: {value}")
    print()
    print(f"  settled observations: {report.total_settled_observations}")
    print(f"  settled games       : {report.total_settled_games}")
    print(f"  slices examined     : {report.slices_examined}")
    print(f"  STATUS              : {report.status}")

    if report.refusals:
        print("\n  refusals:")
        for slice_label, reason in sorted(report.refusals.items()):
            print(f"    {slice_label:44} {reason}")

    if report.findings:
        print("\n  DRAFT RESEARCH FINDINGS (descriptive; sorted by identifier, never by result):")
        for f in report.findings:
            print(
                f"    {f.slice_key.family}|{f.slice_key.timing_label}|{f.slice_key.model_version}: "
                f"obs={f.observations} contracts={f.distinct_contracts} games={f.distinct_games} "
                f"mean_pl={f.mean_research_unit_pl:+.4f} "
                f"ci=[{f.cluster_ci_low}, {f.cluster_ci_high}] "
                f"(1 of {f.slices_examined} slices examined)"
            )

    print()
    print("  No threshold artifact is written. No approval state is produced.")
    print("  A finding is the beginning of an argument, not a rule.")
    if report.status == BLOCKED_ON_SAMPLE:
        print(f"\n  {BLOCKED_ON_SAMPLE}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = report.to_payload()
        payload["protocol_manifest"] = m.to_dict()
        payload["join_stats"] = stats
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"  wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
