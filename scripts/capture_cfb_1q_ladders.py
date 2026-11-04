#!/usr/bin/env python3
"""Read-only prospective capture of the Kalshi CFB 1Q total ladder into an append-only archive directory.

    python scripts/capture_cfb_1q_ladders.py --archive-dir /path/to/archive-checkout

No credentials, no order/portfolio endpoints. Exit 0 whenever the archive was written (a partial capture is labelled
capture_complete=false in its manifest); a failed run is archived too (the workflow fails after publishing it); exit 1 only if the archive could not be written."""
from __future__ import annotations

import argparse
import json
import sys
import uuid
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from cfb_edge_finder.ladder_archive import ladder_1q as l1  # noqa: E402
from cfb_edge_finder.ladder_archive import ladder_store as store  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--archive-dir", required=True)
    ap.add_argument("--no-orderbooks", action="store_true")
    ap.add_argument("--run-id", default=None)
    a = ap.parse_args(argv)
    archive = Path(a.archive_dir)
    run_id = a.run_id or uuid.uuid4().hex[:12]
    getter = l1.RawKalshiGetter()
    res = l1.capture(getter, run_id=run_id, prev_state=store.load_state(archive), orderbooks=not a.no_orderbooks)
    m = res.manifest
    if m["markets_seen"] == 0 and not any(s["complete"] for s in m["sweeps"].values()):
        print("capture produced nothing usable (archived as a failed run):", m["errors"][:3], file=sys.stderr)
        store.write_run(archive, res)          # the failed run is itself evidence and is published; the workflow turns red AFTER the push
        return 0
    out = store.write_run(archive, res)
    print(json.dumps({k: out[k] for k in ("run_id", "board", "capture_complete", "markets_seen", "events_seen", "market_status_counts", "rows_written", "quoted_rows", "no_quote_rows",
                                          "eligible_pregame_rows", "orderbooks_requested", "errors")}, indent=1))
    print("events without a milestone start:", len(m["events_without_milestone_start"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
