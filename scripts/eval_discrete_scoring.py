#!/usr/bin/env python3
"""Run the walk-forward discrete-vs-Normal scoring evaluation and write JSON (+ optional stdout summary).

    python scripts/eval_discrete_scoring.py --team-games data/football/2026/team_games.jsonl \
        --out docs/reports/discrete_scoring_eval_2026-11-02.json

Research only. Reads data, writes one new report file; touches no ledger and no live path.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.research.discrete_scoring_eval import load_games, run_evaluation  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--team-games", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--cal-fraction", type=float, default=0.6)
    args = ap.parse_args()
    report = run_evaluation(load_games(args.team_games), cal_fraction=args.cal_fraction)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, indent=2, sort_keys=True, default=str) + "\n")
    print(json.dumps({k: report[k] for k in ("status", "protocol") if k in report}, indent=2, default=str))
    return 0 if report.get("status") == "OK" else 2


if __name__ == "__main__":
    raise SystemExit(main())
