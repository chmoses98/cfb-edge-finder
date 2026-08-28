#!/usr/bin/env python3
"""Postgame research report for one date.

Summarises what genuinely settled: outcomes, settled contracts, prices by
timing label, genuine CLOSING, CLV, fee-adjusted one-contract research
P/L, gap buckets, missing closes and provenance.

*** WHAT IT REFUSES TO SAY ***

No hindsight recommendations. No "we should have bet X" -- a research
unit is one contract, always, and the report never implies a stake was
available or advisable. No threshold mined from one slate: with a single
day's games the sample is a handful of independent clusters, and the
report states that rather than fitting a cutoff to it.

Works gracefully with zero settlements, which is the current state.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter, defaultdict
from datetime import date, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.protocol import manifest as protocol_manifest  # noqa: E402
from cfb_edge_finder.schemas.settlement import MarketSettlementStatus  # noqa: E402

GAP_BUCKET_EDGES = (-1.0, -0.10, -0.05, -0.02, 0.0, 0.02, 0.05, 0.10, 1.0)
"""Descriptive reporting buckets, fixed in advance. NOT candidate cut
points -- see the protocol's section 5. They exist so a reader can see
shape, not so a threshold can be read off the best-looking edge."""


def gap_bucket(gap: float | None) -> str:
    if gap is None:
        return "unknown"
    for low, high in zip(GAP_BUCKET_EDGES, GAP_BUCKET_EDGES[1:], strict=False):
        if low <= gap < high:
            return f"[{low:+.2f},{high:+.2f})"
    return "out_of_range"


def load_jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    out = []
    with path.open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                try:
                    out.append(json.loads(line))
                except json.JSONDecodeError:
                    continue
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument("--date", type=str, default=None, help="YYYY-MM-DD; omit for all dates")
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    target: date | None = datetime.fromisoformat(args.date).date() if args.date else None
    base = args.data_repo_dir / "data" / "research"
    settlements = load_jsonl(base / "settlements" / f"{args.season}.jsonl")
    attributions = load_jsonl(base / "attributions" / f"{args.season}.jsonl")
    observations = load_jsonl(base / "observations" / f"{args.season}.jsonl")

    settled = [r for r in settlements if r.get("status") == MarketSettlementStatus.SETTLED.value]
    if target is not None:
        def on_date(row: dict) -> bool:
            stamp = row.get("settled_at") or ""
            return stamp[:10] == target.isoformat()

        settled = [r for r in settled if on_date(r)]

    print("=" * 78)
    print(f"POSTGAME RESEARCH REPORT -- {target.isoformat() if target else 'ALL DATES'}")
    print("=" * 78)
    pm = protocol_manifest()
    print(f"  protocol            : {pm.version} ({pm.document_sha256[:16]}...)")
    print(f"  settlement rows     : {len(settlements)}")
    print(f"  terminal settled    : {len(settled)}")
    print(f"  unique settled games: {len({r.get('game_id') for r in settled})}")

    if not settled:
        print()
        print("  No games have settled yet.")
        print("  Nothing to summarise, and nothing may be inferred from nothing.")
        print("  EMPIRICAL THRESHOLD RESEARCH BLOCKED ON NATURAL SAMPLE SIZE")
        if args.json_out:
            args.json_out.parent.mkdir(parents=True, exist_ok=True)
            args.json_out.write_text(
                json.dumps(
                    {
                        "protocol": pm.to_dict(),
                        "date": target.isoformat() if target else None,
                        "settled_contracts": 0,
                        "unique_settled_games": 0,
                        "status": "NO_SETTLEMENTS_YET",
                    },
                    indent=2,
                    sort_keys=True,
                )
                + "\n"
            )
            print(f"  wrote {args.json_out}")
        return 0

    # ------- observations indexed once, never scanned per settlement
    obs_by_ticker: dict[str, list[dict]] = defaultdict(list)
    for row in observations:
        obs = row.get("observation") or {}
        ticker = obs.get("kalshi_market_ticker")
        if ticker:
            obs_by_ticker[ticker].append(row)
    attr_by_key: dict[str, dict] = {
        r.get("observation_key"): r for r in attributions if r.get("observation_key")
    }

    games = Counter()
    by_family = Counter()
    by_timing = Counter()
    gap_buckets = Counter()
    pl_gross: list[float] = []
    pl_fee_adjusted: list[float] = []
    clv_values: list[float] = []
    missing_closes = 0
    mismatches = 0

    print()
    print(f"  {'game_id':<44} {'ticker':<30} {'family':<10} {'settled'}")
    for row in sorted(settled, key=lambda r: (r.get("game_id") or "", r.get("kalshi_market_ticker") or "")):
        games[row.get("game_id")] += 1
        by_family[row.get("family")] += 1
        if row.get("settlement_mismatch_flagged"):
            mismatches += 1
        print(
            f"  {str(row.get('game_id'))[:44]:<44} {str(row.get('kalshi_market_ticker'))[:30]:<30} "
            f"{str(row.get('family')):<10} {row.get('derived_contract_settlement')}"
        )

        for obs_row in obs_by_ticker.get(row.get("kalshi_market_ticker"), []):
            obs = obs_row.get("observation") or {}
            label = (obs.get("snapshot_timing") or {}).get("label")
            by_timing[label] += 1
            price = obs.get("executable_yes_price")
            fee = obs.get("estimated_taker_fee") or 0.0
            if price is None:
                continue
            won = row.get("derived_contract_settlement") == "yes"
            pl_gross.append((1.0 - price) if won else -price)
            pl_fee_adjusted.append((1.0 - price - fee) if won else -(price + fee))
            gap_buckets[gap_bucket(obs.get("fee_adjusted_research_gap"))] += 1

            attribution = attr_by_key.get(obs_row.get("observation_key"))
            closing = (attribution or {}).get("closing") or {}
            if closing.get("closing_captured") and closing.get("closing_yes_price") is not None:
                clv_values.append(closing["closing_yes_price"] - price)
            else:
                missing_closes += 1

    print()
    print(f"  unique settled games      : {len(games)}")
    print(f"  settled contracts         : {len(settled)}")
    print(f"  by family                 : {dict(sorted(by_family.items(), key=lambda i: str(i[0])))}")
    print(f"  observations by timing    : {dict(sorted(by_timing.items(), key=lambda i: str(i[0])))}")
    print(f"  gap buckets (descriptive) : {dict(sorted(gap_buckets.items()))}")
    if pl_gross:
        print(f"  gross 1-contract P/L      : mean {sum(pl_gross) / len(pl_gross):+.4f} over {len(pl_gross)}")
        print(
            f"  fee-adjusted 1-contract   : mean "
            f"{sum(pl_fee_adjusted) / len(pl_fee_adjusted):+.4f} over {len(pl_fee_adjusted)}"
        )
    print(f"  CLV observations          : {len(clv_values)}")
    if clv_values:
        print(f"  mean CLV                  : {sum(clv_values) / len(clv_values):+.4f}")
    print(f"  missing closes            : {missing_closes}")
    print(f"  settlement mismatches     : {mismatches}")

    print()
    print("  A research unit is ONE contract. No stake was available or implied.")
    print("  One slate is a handful of independent game clusters: these numbers")
    print("  describe what happened, and no threshold may be fitted to them.")

    if args.json_out:
        payload = {
            "protocol": pm.to_dict(),
            "date": target.isoformat() if target else None,
            "unique_settled_games": len(games),
            "settled_contracts": len(settled),
            "by_family": dict(sorted(by_family.items(), key=lambda i: str(i[0]))),
            "by_timing": dict(sorted(by_timing.items(), key=lambda i: str(i[0]))),
            "gap_buckets": dict(sorted(gap_buckets.items())),
            "mean_gross_unit_pl": (sum(pl_gross) / len(pl_gross)) if pl_gross else None,
            "mean_fee_adjusted_unit_pl": (
                sum(pl_fee_adjusted) / len(pl_fee_adjusted) if pl_fee_adjusted else None
            ),
            "clv_observations": len(clv_values),
            "mean_clv": (sum(clv_values) / len(clv_values)) if clv_values else None,
            "missing_closes": missing_closes,
            "settlement_mismatches": mismatches,
            "status": "SETTLED_DATA_PRESENT",
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"  wrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
