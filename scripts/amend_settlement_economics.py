#!/usr/bin/env python3
"""File economics-v2 AMENDMENTS for the settlements already on the ledger. COUNTS ONLY.

    python scripts/amend_settlement_economics.py --base-dir <accounting-data checkout> --season 2026
    python scripts/amend_settlement_economics.py --base-dir . --season 2026 --dry-run

*** WHAT THIS DOES ***
Every CFB settlement filed before 2026-09-28 is on the router's economics v1
contract, which subtracts the entry fee twice (see accounting/economics.py).
This walks the season's settlement rows and, for each one whose corrected
economics can be established FROM THE FILED ROW ITSELF, appends one
deterministic amendment to `settlement_amendments/<season>.jsonl`.

*** WHAT IT NEVER DOES ***
It rewrites no settlement row and no wager row. It guesses nothing: a row whose
corrected figure cannot be uniquely established stays unamended and is counted,
with its reason, under `unresolved`.

*** HOW A CORRECTION IS ESTABLISHED WITHOUT THE EXCHANGE ***
A v1 net is `gross - stake - fee_cost`, so the fee_cost the router used is
recoverable exactly from the filed row and its wager:

    implied_fee_cost = gross - stake - filed_net

The v2 contract states `net = gross - stake` ONLY when the exchange's fee_cost
equals the entry fees of the owner's orders on that market. Where the implied
fee_cost equals the wager's own `fees_paid` to the cent, that condition is
proven by the filed row: the fee the router subtracted a second time is the
entry fee, and the correction is `gross - stake`. Where it does not -- a
shared-position row whose net was refused, or a fee the fills do not explain --
nothing is established and nothing is written.

*** IDEMPOTENT, AND CONSISTENT WITH THE ROUTER ***
The amendment id is minted from the wager's key and the contract, and the
router's own v2 settlement run derives the same id and the same figures from
live fee evidence. The store treats an identical repeat as a no-op and a
differing repeat as a refusal, so running this twice writes nothing the second
time, and the router landing later lands on the same row.

Prints counts and reasons. Never a ticker, a stake or a payout: this may be
run where the output is seen.
"""

from __future__ import annotations

import argparse
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cfb_edge_finder.accounting.economics import (  # noqa: E402
    AmendmentRefused,
    build_amendment,
    economics_version_of,
    mint_amendment_id,
    same_correction,
)
from cfb_edge_finder.accounting.settlement import ECONOMICS_V1, ECONOMICS_V2  # noqa: E402
from cfb_edge_finder.accounting.store import (  # noqa: E402
    amendments_ledger_path,
    append_amendments,
    ledger_path,
    read_rows,
    settlement_ledger_path,
)

EXIT_OK = 0
EXIT_UNREADABLE = 2

#: The fee the filed v1 row subtracted must equal the wager's entry fee to
#: within half a hundredth of a cent, allowing only for the ledger's own
#: 4-decimal rounding. Anything larger is a different fee and is not guessed.
FEE_TOLERANCE = 0.00051

PROVENANCE = "cfb-edge-finder amend_settlement_economics (derived from the filed v1 row)"


def derive_v2_row(settlement: dict, wager: dict) -> tuple[dict | None, str | None]:
    """The v2 settlement row implied by a filed v1 row, or (None, reason)."""
    if economics_version_of(settlement) != ECONOMICS_V1:
        return None, "already on a later contract"
    if settlement.get("settlement_status") != "SETTLED":
        return None, "not a settled row"
    gross = settlement.get("gross_return")
    net = settlement.get("net_profit_loss")
    if not isinstance(gross, (int, float)) or isinstance(gross, bool):
        return None, "gross_return unestablished on the filed row"
    if not isinstance(net, (int, float)) or isinstance(net, bool):
        reasons = ", ".join(str(r) for r in settlement.get("refusals") or []) or "unstated"
        return None, f"net unestablished on the filed row ({reasons}); no fee evidence to reconcile"
    stake = wager.get("stake")
    fees = wager.get("fees_paid")
    if not isinstance(stake, (int, float)) or not isinstance(fees, (int, float)):
        return None, "the wager carries no stake or no fees_paid"
    implied_fee_cost = float(gross) - float(stake) - float(net)
    if abs(implied_fee_cost - float(fees)) > FEE_TOLERANCE:
        return None, "the fee the filed row subtracted is not the wager's entry fee; not reconciled"
    corrected = {
        **settlement,
        "economics_version": ECONOMICS_V2,
        "net_profit_loss": round(float(gross) - float(stake), 4),
        "refusals": [],
    }
    evidence = {
        "derivation": "filed v1 net implies a fee_cost equal to the wager's entry fee",
        "implied_fee_cost": round(implied_fee_cost, 4),
        "entry_fees": round(float(fees), 4),
        "fee_tolerance": FEE_TOLERANCE,
    }
    return corrected, evidence  # type: ignore[return-value]


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", required=True, help="a checkout of accounting-data")
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument("--dry-run", action="store_true", help="derive and count; write nothing")
    args = parser.parse_args(argv)

    base = args.base_dir
    try:
        wagers = {w["source_bet_key"]: w for w in read_rows(ledger_path(base, args.season))
                  if isinstance(w.get("source_bet_key"), str)}
        settlements = read_rows(settlement_ledger_path(base, args.season))
        filed = {a["amendment_id"]: a for a in read_rows(amendments_ledger_path(base, args.season))
                 if isinstance(a.get("amendment_id"), str)}
    except (OSError, ValueError) as exc:
        print(f"the ledger could not be read: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    to_write: list[dict] = []
    already = 0
    contradicted = 0
    unresolved: Counter = Counter()
    eligible = 0
    for settlement in settlements:
        key = settlement.get("source_bet_key")
        wager = wagers.get(key) if isinstance(key, str) else None
        if wager is None:
            unresolved["no wager for this settlement"] += 1
            continue
        if economics_version_of(settlement) != ECONOMICS_V1:
            unresolved["already on a later contract"] += 1
            continue
        eligible += 1
        derived = derive_v2_row(settlement, wager)
        if derived[0] is None:
            unresolved[str(derived[1])] += 1
            continue
        corrected, evidence = derived
        try:
            amendment = build_amendment(settlement, corrected, provenance=PROVENANCE, evidence=evidence)
        except AmendmentRefused as exc:
            unresolved[f"refused: {exc}"] += 1
            continue
        existing = filed.get(amendment["amendment_id"])
        if existing is not None:
            if same_correction(existing, amendment):
                already += 1
            else:
                contradicted += 1
                unresolved["a DIFFERENT correction is already filed under this id"] += 1
            continue
        to_write.append(amendment)

    print(f"season ledger:                 {args.season}")
    print(f"settlement rows:               {len(settlements)}")
    print(f"eligible (filed under v1):     {eligible}")
    print(f"amendments already filed:      {already}")
    print(f"amendments to write:           {len(to_write)}")
    print(f"unresolved (left unamended):   {sum(unresolved.values())}")
    for reason, count in sorted(unresolved.items(), key=lambda kv: (-kv[1], kv[0])):
        print(f"    {count:4}  {reason}")
    if contradicted:
        print("  a filed amendment disagrees with this derivation; nothing was written for it "
              "and a person has to look", file=sys.stderr)

    if args.dry_run:
        print("DRY RUN: nothing written.")
        return EXIT_OK
    if to_write:
        result = append_amendments(base, args.season, to_write)
        print(f"written:                       {result.written}")
        # Deterministic ids are safe to print: they carry no money and no ticker.
        assert all(
            mint_amendment_id(a["source_bet_key"], a["economics_version"]) == a["amendment_id"]
            for a in to_write
        )
    else:
        print("written:                       0 (nothing to do)")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
