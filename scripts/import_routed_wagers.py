#!/usr/bin/env python3
"""Import a kalshi-bet-router payload into the accounting ledger. COUNTS ONLY.

This is the entry point the router's delivery workflow runs. It decides
nothing: ``cfb_edge_finder.accounting.import_routed_wagers`` owns identity and
every refusal, and the store owns dedup. This owns reading a file and reporting
what happened without printing a wager.

WHAT NEVER REACHES STDOUT
-------------------------
This repository is public and its Actions logs are public. A payload row
carries market, side, contracts, price, stake and fees. So this prints counts
and minted ids -- never a ticker, a price or a stake. A refusal prints its
REASON and the row's position in the batch, which is enough to act on and
carries nothing about the bet.

RECORDING IS NOT ENDORSING
--------------------------
Nothing here touches the CFB model, which stays research-only. Writing a row
says the owner placed a College Football bet and says nothing about whether
anything in this repository recommended it.

THE SEASON IS NAMED, NOT DERIVED
--------------------------------
The ledger is one file per season, so nothing can be written without naming the
file. ``--season`` is required and has no default: a College Football season
spans two calendar years, so inferring one from a game date would be a guess
that lands real wagers in the wrong year's accounting.
"""

from __future__ import annotations

import argparse
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cfb_edge_finder.accounting.import_routed_wagers import import_rows  # noqa: E402

EXIT_OK = 0
EXIT_REFUSED = 1
EXIT_BAD_INPUT = 2


def read_payload(path: str) -> tuple[str, list]:
    """The router's envelope, checked rather than trusted.

    The batch label appears TWICE -- once on the envelope and once on every row
    -- because MLB's importer reads the envelope and this ledger reads the row.
    Two copies of a value can disagree, so the disagreement is made impossible
    to miss instead of being hoped against: a row whose label differs from its
    envelope's is refused here, before anything is written.
    """
    with open(path, encoding="utf-8") as handle:
        payload = json.load(handle)

    if not isinstance(payload, dict):
        raise ValueError("payload is not an object")
    batch = payload.get("importBatchId")
    if not isinstance(batch, str) or not batch.strip():
        raise ValueError("payload carries no importBatchId")
    rows = payload.get("rows")
    if not isinstance(rows, list):
        raise ValueError("payload carries no rows list")

    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row {index} is not an object")
        if row.get("import_batch_id") != batch:
            raise ValueError(
                f"row {index} says it belongs to a different import batch than "
                "its own envelope; one of the two is wrong and neither may be "
                "assumed"
            )
    return batch, rows


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True,
                        help="the router's payload file (an envelope with rows)")
    parser.add_argument("--base-dir", required=True,
                        help="a checkout of the accounting-data branch; the ledger lives under it")
    parser.add_argument("--season", required=True, type=int,
                        help="which season's ledger these wagers belong to (never inferred)")
    parser.add_argument("--receipts-out", default=None,
                        help="write the run's outcome (counts and ids, never economics)")
    args = parser.parse_args(argv)

    try:
        batch, rows = read_payload(args.payload)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"unreadable payload: {exc}", file=sys.stderr)
        return EXIT_BAD_INPUT

    print(f"import batch: {batch}")
    print(f"season ledger: {args.season}")
    print(f"rows in payload: {len(rows)}")

    result = import_rows(args.base_dir, rows, season=args.season)

    print(f"  written:         {result['written']}")
    print(f"  already present: {result['already_present']}")
    print(f"  refused:         {result['refused']}")
    for index, reason in result["refusals"]:
        # The reason and the row's POSITION. Never the row.
        print(f"    row {index}: {reason}")

    if args.receipts_out:
        with open(args.receipts_out, "w", encoding="utf-8") as handle:
            json.dump({
                "importBatchId": batch,
                "season": args.season,
                "written": result["written"],
                "alreadyPresent": result["already_present"],
                "refused": result["refused"],
                "refusals": [{"row": i, "reason": r} for i, r in result["refusals"]],
                "keysWritten": result["keys_written"],
                # PER-ROW, alongside the counts. The router's merge gate reads
                # these to prove that the same fill delivered twice lands on
                # the same canonical row -- a property this ledger has had
                # since it was written and previously had no way to show.
                # Counts alone cannot demonstrate it.
                "rows": result["rows"],
            }, handle, indent=2, sort_keys=True)
            handle.write("\n")

    # A REFUSAL IS A FAILURE HERE. A payload that reached this script has
    # already been classified, reconciled and judged importable by the router,
    # so a row this ledger will not take means the two repositories disagree
    # about what is valid.
    return EXIT_REFUSED if result["refused"] else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
