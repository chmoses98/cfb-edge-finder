"""Import settlements the router attributed, for wagers already in the ledger.

THE ONE RULE THAT MATTERS HERE
-------------------------------
A settlement may only be written for a wager this ledger already holds. A row
whose ``source_bet_key`` matches nothing is REFUSED, not written -- an orphan
settlement is a payout attributed to a bet this repository has no record of,
and it would show up in every total while belonging to nothing.

WHAT THIS DOES NOT DECIDE
--------------------------
Nothing here computes a return. The router owns attributing a position's
settlement to an order, and owns refusing where it cannot; this reads what it
sent, checks it against the wagers on disk, and writes.

RE-RUNNING IS A NO-OP
----------------------
``store.append_settlements`` keys on ``source_bet_key`` because a market settles
once, so a second pass over the same settlements writes nothing.

A second pass carrying DIFFERENT money for a wager already settled is not a
repeat, though, and is refused rather than skipped. See ``_settlement_conflict``.

WHY THERE IS NO ``import_batch_id`` ON A SETTLEMENT ROW
--------------------------------------------------------
A wager row carries one: it is that row's provenance, and ``wager.validate``
requires it. A settlement row does not, and that is a contract rather than an
oversight.

A settlement is not identified by the delivery that carried it. It is
identified by THE WAGER IT SETTLES: ``source_bet_key`` is the join, the
``settlement_id`` is minted from that key alone, and the row may not be written
at all unless that wager is already in this ledger. A batch id would add a
field that is not part of that identity, that this store would never key on,
and that would be a claim about which router run happened to deliver a payout
the exchange decided.

``build_record`` therefore REFUSES a row carrying one, along with any other
field this schema does not model. That refusal is load-bearing: it is what
stops "just add the batch id" being a silent fix somewhere upstream, and
`tests/test_accounting_settlements.py` pins it.
"""

from __future__ import annotations

import hashlib
from dataclasses import fields as dataclass_fields
from pathlib import Path

from .settlement import SCHEMA_VERSION, WagerSettlement, validate
from .store import (
    _settlement_conflict,
    append_settlements,
    existing_keys,
    ledger_path,
    read_rows,
    settlement_ledger_path,
    source_bet_key_of,
)

#: Prefix on every minted id, so a routed settlement is identifiable as one.
ID_PREFIX = "stl"


class SettlementRefused(Exception):
    """This settlement cannot be filed, and filing it anyway would be worse."""


def mint_settlement_id(source_bet_key: str) -> str:
    """Derived from the WAGER's key alone, for the same reason the wager's id is.

    Not from the result, the payout or the settlement time: a correction to any
    of those must land on the same row rather than beside it.
    """
    if not isinstance(source_bet_key, str) or not source_bet_key.strip():
        raise SettlementRefused("source_bet_key is required to mint a settlement id")
    digest = hashlib.sha256(source_bet_key.encode("utf-8")).hexdigest()[:24]
    return f"{ID_PREFIX}-{digest}"


def build_record(row: dict) -> WagerSettlement:
    """One router settlement row, as a typed record this ledger will accept."""
    known = {f.name for f in dataclass_fields(WagerSettlement)}
    unknown = sorted(set(row) - known)
    if unknown:
        raise SettlementRefused(f"router row carried unknown field(s): {unknown}")

    record = WagerSettlement(
        settlement_id=mint_settlement_id(row.get("source_bet_key")),
        schema_version=SCHEMA_VERSION,
        source_bet_key=row.get("source_bet_key"),
        market_ticker=row.get("market_ticker"),
        side=row.get("side"),
        settlement_status=row.get("settlement_status"),
        settled_at=row.get("settled_at"),
        result=row.get("result"),
        gross_return=row.get("gross_return"),
        net_profit_loss=row.get("net_profit_loss"),
        refusals=list(row.get("refusals") or []),
        venue=row.get("venue") or "kalshi",
    )

    problems = validate(record.to_dict())
    if problems:
        raise SettlementRefused("; ".join(problems))
    return record


def import_rows(base_dir: Path, rows: list, *, season: int) -> dict:
    """Write every settlement whose wager is on disk. Returns counts and reasons.

    A settlement for a wager this ledger does not hold is refused per row and
    does not stop the others.
    """
    if not isinstance(season, int) or isinstance(season, bool):
        raise SettlementRefused(f"season must be an int naming the ledger; got {season!r}")

    # The wagers this ledger actually holds, read once.
    known_wagers = existing_keys(ledger_path(base_dir, season))
    # And the settlements it already holds, so a SECOND, DIFFERENT observation
    # of one is refused per row rather than aborting the batch at the store.
    # Identical repeats are not looked at here at all -- they are the no-op
    # that makes re-running safe, and the store reports them as duplicates.
    settled_rows = {
        key: row for row in read_rows(settlement_ledger_path(base_dir, season))
        if (key := source_bet_key_of(row)) is not None
    }

    built: list[dict] = []
    built_by_key: dict[str, dict] = {}
    refusals: list[tuple[int, str]] = []
    # Per-row receipts, for the same reason the wager importer emits them: a
    # refusal that names only a row index cannot be acted on without the
    # payload, and the payload may not be printed.
    receipts: list[dict] = []

    for index, row in enumerate(rows):
        try:
            record = build_record(row)
            if record.source_bet_key not in known_wagers:
                raise SettlementRefused(
                    "no wager with this source_bet_key is in the "
                    f"{season} ledger; a settlement attributed to a bet this "
                    "repository has no record of would count in every total "
                    "while belonging to nothing"
                )
            # Already on disk, OR already built earlier in this same payload.
            # Without the second, two contradicting rows in one batch reach the
            # store together and abort all 41 with a ValueError instead of
            # refusing the one row that is wrong.
            recorded = settled_rows.get(record.source_bet_key) or built_by_key.get(
                record.source_bet_key
            )
            if recorded is not None:
                # FIELD NAMES, NEVER VALUES. This reason is printed, and this
                # repository's Actions logs are public.
                disagreements = _settlement_conflict(recorded, record.to_dict())
                if disagreements:
                    raise SettlementRefused(
                        "a settlement for this wager is already recorded and this one "
                        f"CONTRADICTS it on: {', '.join(disagreements)}. A market settles "
                        "once and this ledger never supersedes a row, so a restated "
                        "payout is a person's decision rather than an append"
                    )
        except SettlementRefused as exc:
            refusals.append((index, str(exc)))
            receipts.append(
                {
                    "row": index,
                    "source_bet_key": row.get("source_bet_key") if isinstance(row, dict) else None,
                    "settlement_id": None,
                    "duplicate_status": "REFUSED",
                    "success": False,
                    "reason": str(exc),
                }
            )
            continue
        built.append(record.to_dict())
        built_by_key[record.source_bet_key] = record.to_dict()
        receipts.append(
            {
                "row": index,
                "source_bet_key": record.source_bet_key,
                "settlement_id": record.settlement_id,
                "duplicate_status": "NEW",
                "success": True,
            }
        )

    result = append_settlements(base_dir, season, built) if built else None

    written_keys = set(result.keys_written) if result else set()
    for receipt in receipts:
        if receipt["duplicate_status"] == "NEW" and receipt["source_bet_key"] not in written_keys:
            # A market settles once, so a second observation is a duplicate
            # rather than a correction -- the store's own rule, reported here
            # rather than re-decided.
            receipt["duplicate_status"] = "DUPLICATE_NOOP"

    return {
        "written": result.written if result else 0,
        "already_present": result.skipped_duplicate if result else 0,
        "refused": len(refusals),
        "refusals": refusals,
        "keys_written": list(result.keys_written) if result else [],
        "rows": receipts,
    }
