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

from cfb_edge_finder.accounting import report, store  # noqa: E402


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

    print(report.render(report.summarize(store.read_rows(path), args.season)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
