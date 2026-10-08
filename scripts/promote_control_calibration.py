#!/usr/bin/env python3
"""Promote the Wave 2 frozen CONTROL distributions into the production calibration artifact.

The reviewed promotion step of docs/SCRIPT_ENGINE_V2_MIGRATION.md section 5.
It COPIES the frozen development values (2021-2025) from the research file,
verifies that file against its SHA-256 (683d075d...), attaches the 2014-2020
validation results as a separate block, and writes
data/scripting/calibration/control_margin_v2.json. Nothing is refitted.

    python scripts/promote_control_calibration.py            # write
    python scripts/promote_control_calibration.py --check    # verify the committed file is exactly this
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.scripting.calibration import (  # noqa: E402
    CALIBRATION_PATH,
    SOURCE_HOLDOUT_REPORT_PATH,
    SOURCE_PARAMETERS_PATH,
    promote,
    write_calibration,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    parameters = json.loads((args.root / SOURCE_PARAMETERS_PATH).read_text(encoding="utf-8"))
    report = json.loads((args.root / SOURCE_HOLDOUT_REPORT_PATH).read_text(encoding="utf-8"))
    artifact = promote(parameters, report)
    target = args.root / CALIBRATION_PATH
    if args.check:
        committed = json.loads(target.read_text(encoding="utf-8"))
        if committed != artifact:
            print(f"{target} differs from the promotion of {SOURCE_PARAMETERS_PATH}")
            return 1
        print(f"{target} matches the promotion (sha256 {artifact['sha256']})")
        return 0
    print(f"wrote {target} sha256 {write_calibration(artifact, target)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
