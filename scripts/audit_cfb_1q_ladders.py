#!/usr/bin/env python3
"""python scripts/audit_cfb_1q_ladders.py --ladder-archive DIR --espn-archive DIR --schedule data/football/2026/schedule.json --out report.json"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

import collect_live_context as transport  # noqa: E402

from cfb_edge_finder.ladder_archive import espn_store, ladder_audit, ladder_store  # noqa: E402


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ladder-archive", required=True)
    ap.add_argument("--espn-archive", default=None)
    ap.add_argument("--schedule", required=True)
    ap.add_argument("--out", required=True)
    a = ap.parse_args(argv)
    la = Path(a.ladder_archive)
    v = ladder_store.verify(la)
    ea = Path(a.espn_archive) if a.espn_archive else None
    ev = espn_store.verify(ea) if ea else None
    sched = json.loads(Path(a.schedule).read_text())["events"]
    rep = ladder_audit.audit(ladder_audit.load_rows(la), ladder_audit.load_manifests(la), sched, ladder_audit.espn_period1(ea), transport.name_variants)
    rep["archive_verification"] = {"ladder": v, "espn": ev}
    Path(a.out).write_text(json.dumps(rep, indent=1, sort_keys=True) + "\n")
    print(json.dumps({k: rep[k] for k in ("runs", "rows", "cohort", "join_status", "rows_by_lead_bucket", "baseline_status", "unjoined_events", "unjoined_events_sample", "archive_verification")}, indent=1))
    return 0 if v["ok"] and (ev is None or ev["ok"]) else 2


if __name__ == "__main__":
    raise SystemExit(main())
