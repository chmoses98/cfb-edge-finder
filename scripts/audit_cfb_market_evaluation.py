#!/usr/bin/env python3
"""Market-first coverage audit for RUN CFB (read-only, research-only).

    python scripts/audit_cfb_market_evaluation.py --slate-date 2026-11-07 --window early \
        --expect-games 42 --expect-contracts 3992 \
        [--evaluations analyst_evaluations.json] [--ledger-dir OUT] [--json OUT.json] [--md OUT.md] [--require-complete]

Reconciles EVERY listed contract and BOTH sides (2 x contracts) and reports how many were genuinely evaluated by an
analyst. With no ``--evaluations`` file nothing is evaluated: every priceable side is NOT_EVALUATED and the report says
"NOT A COMPLETE EVALUATION". It never produces a probability, a bet, a stake, an approval or an order.

Exit codes: 0 report written; 1 structural defect (a contract/side is missing, duplicated or miscounted);
2 ``--require-complete`` was given and the verdict is not COMPLETE. Evaluations schema: docs/RUN_CFB_MARKET_AUDIT.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.research_packet import market_audit as ma  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(ROOT))
    ap.add_argument("--slate-date", required=True)
    ap.add_argument("--window", choices=["early", "afternoon", "evening"])
    ap.add_argument("--expect-games", type=int)
    ap.add_argument("--expect-contracts", type=int)
    ap.add_argument("--evaluations", help="analyst evaluations JSON (cfb_market_evaluations/1.0.0)")
    ap.add_argument("--wagers", default="app/latest/wagers.json")
    ap.add_argument("--evidence-join", default=None, help="evidence_join report JSON (CLV by family)")
    ap.add_argument("--as-of", help="label only; recorded in the report")
    ap.add_argument("--ledger-dir", help="write one sides ledger per game here (regenerated, not committed)")
    ap.add_argument("--json", dest="json_out")
    ap.add_argument("--md", dest="md_out")
    ap.add_argument("--require-complete", action="store_true")
    ap.add_argument("--verify-report", help="re-check a saved report JSON for consistency and exit")
    a = ap.parse_args(argv)

    if a.verify_report:
        problems = ma.verify_report(json.loads(Path(a.verify_report).read_text()))
        for p in problems:
            print("REPORT INCONSISTENT:", p, file=sys.stderr)
        print("report consistent" if not problems else f"{len(problems)} problem(s)")
        return 1 if problems else 0

    root = Path(a.root)
    inv = ma.load_inventory(root, a.slate_date, a.window)
    sides = ma.build_sides(inv)
    entries, load_defects = ma.load_evaluations(Path(a.evaluations) if a.evaluations else None)
    apply_defects = ma.apply_evaluations(sides, entries)
    defects = ma.verify(
        inv, sides, load_defects + apply_defects, expect_games=a.expect_games, expect_contracts=a.expect_contracts
    )
    evj = Path(a.evidence_join) if a.evidence_join else None
    if evj is None:
        found = sorted((root / "docs" / "reports").glob("evidence_join_*.json"))
        evj = found[-1] if found else None
    history = ma.family_history(root / a.wagers, evj)
    report = ma.build_report(inv, sides, defects, history=history, as_of=a.as_of)
    problems = ma.verify_report(report)
    if problems:  # the audit's own arithmetic must hold before anything is written
        for p in problems:
            print("INTERNAL INCONSISTENCY:", p, file=sys.stderr)
        return 1
    md = ma.render_markdown(report)
    if a.json_out:
        Path(a.json_out).write_text(json.dumps(report, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    if a.md_out:
        Path(a.md_out).write_text(md, encoding="utf-8")
    if a.ledger_dir:
        ma.write_ledger(sides, Path(a.ledger_dir))
    print(md if not (a.json_out or a.md_out) else md.split("\n## Coverage")[0])
    if report["verdict"]["structural_defects"]:
        return 1
    if a.require_complete and not report["verdict"]["evaluation_complete"]:
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
