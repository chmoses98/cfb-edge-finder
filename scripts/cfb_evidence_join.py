#!/usr/bin/env python3
"""Per-wager evidence join: recommendation link, fill ids, entry context and closing-price proxy (CLV).

    python scripts/cfb_evidence_join.py --wagers app/latest/wagers.json \
        --from 2026-10-22 --to 2026-11-01 --history-repo <clone of main with catalog history> \
        [--decision-records <dir of cfb_decision_record JSON files>] \
        --summary-out docs/reports/evidence_join_2026-10-22_2026-11-01.json \
        [--rows-out <path OUTSIDE the repo for the per-wager rows>]

The committed summary is AGGREGATE ONLY (coverage counts and mean CLV, no tickers or prices). Per-wager rows are
written only where ``--rows-out`` says. Reads the ledger export and git history; writes neither.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.postmortem.evidence_join import build_evidence, closes_from_evidence  # noqa: E402
from cfb_edge_finder.postmortem.git_snapshots import GitSnapshotSource  # noqa: E402


def load_records(directory: Path | None) -> list[dict]:
    if directory is None:
        return []
    out = []
    for path in sorted(Path(directory).rglob("*.json")):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if isinstance(doc, dict) and str(doc.get("schema_version", "")).startswith("cfb_decision_record"):
            out.append(doc)
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--wagers", type=Path, required=True)
    ap.add_argument("--from", dest="start", required=True)
    ap.add_argument("--to", dest="end", required=True)
    ap.add_argument("--history-repo", type=Path, default=None)
    ap.add_argument("--decision-records", type=Path, default=None)
    ap.add_argument("--summary-out", type=Path, required=True)
    ap.add_argument("--rows-out", type=Path, default=None)
    ap.add_argument("--closes-out", type=Path, default=None,
                    help="{ticker: {yes_bid, yes_ask, captured_at}} of NEAR-CLOSE two-sided proxies, for "
                    "cfb_loss_attribution --closing-quotes (stale proxies are excluded on purpose)")
    args = ap.parse_args()

    items = json.loads(args.wagers.read_text(encoding="utf-8")).get("items") or []
    window = [w for w in items if args.start <= str(w.get("placed_at", ""))[:10] <= args.end]
    source = GitSnapshotSource(args.history_repo, since=f"{args.start}T00:00Z") if args.history_repo else None
    result = build_evidence(window, source=source, decision_records=load_records(args.decision_records))
    summary = {k: v for k, v in result.items() if k != "rows"}
    summary["window"] = {"from": args.start, "to": args.end}
    summary["snapshots_available"] = len(source.snapshots()) if source else 0
    args.summary_out.parent.mkdir(parents=True, exist_ok=True)
    args.summary_out.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if args.rows_out:
        args.rows_out.parent.mkdir(parents=True, exist_ok=True)
        lines = (json.dumps(r, sort_keys=True) for r in result["rows"])
        args.rows_out.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if args.closes_out:
        args.closes_out.parent.mkdir(parents=True, exist_ok=True)
        args.closes_out.write_text(json.dumps(closes_from_evidence(result["rows"]), indent=1, sort_keys=True) + "\n")
    print(json.dumps(summary["coverage"], indent=2, sort_keys=True))
    print("clv_vs_mid:", summary["clv_vs_mid"], "close_lag_seconds:", summary["close_lag_seconds"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
