#!/usr/bin/env python3
"""Run the strictly-separated multi-season key-number calibration (research only; promotes nothing).

    python scripts/eval_multiseason_calibration.py --out docs/reports/multiseason_calibration_2026-11-02.json

Reads data/scripting/validation/records_{2024,2025,2026}.jsonl.gz. The acceptance rule is declared in
src/cfb_edge_finder/research/multiseason_calibration.py and was committed BEFORE this script produced a result.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.research.multiseason_calibration import run  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=str(ROOT / "data" / "scripting" / "validation"))
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    report = run(Path(args.root))
    Path(args.out).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({k: report.get(k) for k in ("status", "counts")}, indent=1))
    if report.get("status") == "OK":
        print(
            "verdict:", json.dumps(report["verdict"]["verdict"]), "selected:", report["verdict"]["selected_candidate"]
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
