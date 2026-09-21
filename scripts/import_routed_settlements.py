#!/usr/bin/env python3
"""Import a kalshi-bet-router settlement payload. COUNTS ONLY.

A settlement may only be written for a wager this ledger already holds, so this
runs against the same checkout the wagers live in. A settlement for an unknown
wager is refused, never written: a payout attributed to a bet this repository
has no record of would count in every total while belonging to nothing.

Nothing here computes a return. The router owns attributing a position's
settlement to an order and owns refusing where it cannot.

This repository is public and so are its Actions logs, so this prints counts
and refusal reasons -- never a ticker, a payout or a stake.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cfb_edge_finder.accounting.import_settlements import import_rows  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_BAD_INPUT = 2


def read_payload(path: str) -> list:
    """The router's settlement envelope, checked rather than trusted."""
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("payload is not an object")
    rows = payload.get("settlements")
    if not isinstance(rows, list):
        raise ValueError("payload carries no settlements list")
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"settlement {index} is not an object")
    return rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True)
    parser.add_argument("--base-dir", required=True,
                        help="a checkout of the accounting-data branch")
    parser.add_argument("--season", required=True, type=int,
                        help="which season's ledger these settle (never inferred)")
    parser.add_argument("--receipts-out", default=None,
                        help="write the run's outcome (counts, ids and reasons; never economics)")
    args = parser.parse_args(argv)

    try:
        rows = read_payload(args.payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"unreadable payload: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    print(f"season ledger: {args.season}")
    print(f"settlements in payload: {len(rows)}")

    result = import_rows(args.base_dir, rows, season=args.season)

    print(f"  written:         {result['written']}")
    print(f"  already present: {result['already_present']}")
    print(f"  refused:         {result['refused']}")
    for index, reason in result["refusals"]:
        print(f"    row {index}: {reason}")

    if args.receipts_out:
        # Counts, ids and reasons. NEVER a payout, a ticker or a P&L: this
        # file is read by a workflow whose log is public, and the router's
        # own reporter prints only what is in here.
        with open(args.receipts_out, "w", encoding="utf-8") as handle:
            json.dump({
                "season": args.season,
                "written": result["written"],
                "alreadyPresent": result["already_present"],
                "refused": result["refused"],
                "refusals": [{"row": i, "reason": r} for i, r in result["refusals"]],
                "keysWritten": result["keys_written"],
                "rows": result["rows"],
            }, handle, indent=2, sort_keys=True)
            handle.write("\n")

    return EXIT_REFUSED if result["refused"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
