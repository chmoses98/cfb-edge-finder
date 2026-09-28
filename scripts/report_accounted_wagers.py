#!/usr/bin/env python3
"""Print the CFB wager ledger's accounting summary. NOT a model result.

Every wager in that ledger was placed by the owner and recommended by nothing
in this repository, so the won-lost record below is a fact about a bankroll.
The CFB model remains research-only and nothing here changes that.

Reads only. This script cannot write a wager, and the module it calls cannot
size, rank, price or recommend one.
"""

from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cfb_edge_finder.accounting import economics, report, store  # noqa: E402


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", required=True,
                        help="a checkout of the accounting-data branch")
    parser.add_argument("--season", required=True, type=int,
                        help="which season's ledger to summarize")
    args = parser.parse_args(argv)

    path = store.ledger_path(args.base_dir, args.season)
    if not path.exists():
        # Not an error and not an empty summary either: "no ledger for that
        # season" and "a season in which nothing was wagered" are different
        # facts, and printing zeros would state the second one.
        print(f"no ledger for season {args.season} at {path}", file=sys.stderr)
        return 1

    # Settlements live in their own append-only file. Absent is the record for
    # "not settled yet", so a missing file is a legitimate empty list rather
    # than a reason to fail.
    settlement_path = store.settlement_ledger_path(args.base_dir, args.season)
    settlements = store.read_rows(settlement_path) if settlement_path.exists() else []

    # Amendments are the ledger's own append-only corrections of a settlement's
    # economics. The summary is stated under the CANONICAL view; the as-filed
    # figure is printed beside it so the correction is visible, not implied.
    amendment_path = store.amendments_ledger_path(args.base_dir, args.season)
    amendments = store.read_rows(amendment_path) if amendment_path.exists() else []
    wagers = store.read_rows(path)
    canonical = economics.apply_amendments(settlements, amendments) if amendments else settlements

    print(report.render(report.summarize(wagers, args.season, settlements=canonical)))
    if amendments:
        filed = report.summarize(wagers, args.season, settlements=settlements)
        amended = sum(1 for row in canonical if row.get("canonical_amendment_id"))
        print("")
        print(f"  economics: {amended} settlement(s) stated at amended (v2) figures; "
              f"{len(amendments)} amendment row(s) on file")
        if filed.profit_loss_is_complete:
            print(f"  as filed (v1): {filed.realized_profit_loss:+.2f}")
        else:
            print(f"  as filed (v1): unestablished for {filed.profit_loss_unestablished} wager(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
