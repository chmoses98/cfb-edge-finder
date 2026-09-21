"""Import wagers the owner actually placed, delivered by kalshi-bet-router.

WHAT THE ROUTER SENDS
---------------------
The router reconstructs one ORDER from its Kalshi fills and sends the exchange
evidence: market, side, contracts, the quantity-weighted fill price, the fee the
exchange itself reported, the cash consumed, and the contest's own date.

It deliberately withholds ``wager_id``. A router has no business naming records
in this ledger, and a name it chose would be a name this repository could not
reproduce. It is minted here instead, DETERMINISTICALLY from ``source_bet_key``,
so the same order delivered twice lands on the same row rather than a second one
under a new name.

It also sends no ``season`` and no ``week``, because both are optional here and
optional is not an invitation to fill something in. ``week`` therefore stays
absent. ``season`` is the one thing a caller must state, and for a structural
reason rather than a modelling one: the ledger is one file per season, so
nothing can be written at all without naming the file. It is taken as an
argument and never derived from ``game_date`` -- a College Football season
spans two calendar years, so a January bowl game's date says nothing reliable
about which season it belongs to.

RECORDING IS NOT ENDORSING
--------------------------
The CFB model is research-only and this module does not move that line by a
millimetre. A row written here says the owner placed a College Football bet. It
says nothing about whether anything in this repository recommended it, was
consulted, or was right -- and the record refuses every field that could imply
otherwise, including a ``model_supported=False`` that still asserts the model
had an opinion.

IDENTITY IS NOT ECONOMICS
-------------------------
``wager_id`` derives from ``source_bet_key`` alone -- not the stake, the price,
the fee or the import batch. Correcting an economics field on an already-
imported wager must land on the SAME row, and the same order noticed by a second
backfill is the same wager rather than a second one.
"""

from __future__ import annotations

import hashlib
from dataclasses import fields as dataclass_fields
from pathlib import Path

from .store import append_wagers
from .wager import (
    ENTRY_METHOD_IMPORTED_RECEIPT,
    SCHEMA_VERSION,
    AccountedWager,
    validate,
)

#: Prefix on every minted id, so a routed wager is identifiable as one at a
#: glance in a ledger somebody is reading by eye.
ID_PREFIX = "routed"


class ImportRefused(Exception):
    """This wager cannot be filed, and filing it anyway would be worse."""


def mint_wager_id(source_bet_key: str) -> str:
    """A stable id for one order, derived only from the venue's own identity.

    Deliberately NOT a function of the economics or the import batch: a
    correction to a fee must land on the same row, and the same order noticed by
    a second backfill is the same wager.
    """
    if not isinstance(source_bet_key, str) or not source_bet_key.strip():
        raise ImportRefused("source_bet_key is required to mint a wager id")
    digest = hashlib.sha256(source_bet_key.encode("utf-8")).hexdigest()[:24]
    return f"{ID_PREFIX}-{digest}"


def build_record(row: dict, *, season: int) -> AccountedWager:
    """One router row, as a typed record this ledger will accept.

    Built through the DATACLASS rather than as a dict on purpose. A hand-built
    dict would pass ``validate()`` while missing fields the dataclass declares,
    and the store writes whatever dict it is given once validation is happy. The
    dataclass is the only place that knows the full shape, so it is the only
    honest place to assemble one.
    """
    known = {f.name for f in dataclass_fields(AccountedWager)}
    unknown = sorted(set(row) - known)
    if unknown:
        # A router row carrying a field this ledger does not model is a contract
        # break, not something to drop quietly: the sender believes a field it
        # sent was recorded, and silently discarding one is how a ledger ends up
        # disagreeing with its source while both sides report success.
        raise ImportRefused(f"router row carried unknown field(s): {unknown}")

    record = AccountedWager(
        wager_id=mint_wager_id(row.get("source_bet_key")),
        schema_version=SCHEMA_VERSION,
        source_bet_key=row.get("source_bet_key"),
        import_batch_id=row.get("import_batch_id"),
        entry_method=row.get("entry_method") or ENTRY_METHOD_IMPORTED_RECEIPT,
        game_date=row.get("game_date"),
        market_ticker=row.get("market_ticker"),
        side=row.get("side"),
        executed_at=row.get("executed_at"),
        contracts=row.get("contracts"),
        execution_price=row.get("execution_price"),
        stake=row.get("stake"),
        fees_paid=row.get("fees_paid"),
        fees_are_estimated=bool(row.get("fees_are_estimated", False)),
        venue=row.get("venue") or "kalshi",
        # The caller's declaration of which ledger file this batch belongs to,
        # written onto the row as well so a reader holding one line does not
        # have to recover it from a filename. `week` stays absent: nothing in
        # the payload establishes it, and optional is not an invitation.
        season=season,
    )

    problems = validate(record.to_dict())
    if problems:
        raise ImportRefused("; ".join(problems))
    return record


def import_rows(base_dir: Path, rows: list, *, season: int) -> dict:
    """Write every row that is not already in the season's ledger.

    Dedup is NOT reimplemented here. ``store.append_wagers`` already keys on
    ``source_bet_key`` against the whole file, so re-running a backfill is a
    no-op because of a property of the store rather than a promise made by this
    caller.

    A row this module cannot build is reported with its REASON and does not stop
    the others -- one unresolvable wager in a batch of thirty should cost one
    wager, not thirty. Refusals are a count and a reason, never silence.
    """
    if not isinstance(season, int) or isinstance(season, bool):
        raise ImportRefused(f"season must be an int naming the ledger; got {season!r}")

    built: list[dict] = []
    refusals: list[tuple[int, str]] = []
    # PER-ROW RECEIPTS, not just counts.
    #
    # A refusal that names only "row 4" is a refusal nobody can act on without
    # the payload -- and the payload is the one artifact that may not be
    # printed. Naming the SOURCE KEY and the minted id per row is what lets the
    # caller's merge gate prove that the same fill delivered twice lands on the
    # same canonical row, which is a property this ledger has and previously
    # had no way to demonstrate.
    receipts: list[dict] = []

    for index, row in enumerate(rows):
        try:
            record = build_record(row, season=season)
        except ImportRefused as exc:
            refusals.append((index, str(exc)))
            receipts.append(
                {
                    "row": index,
                    "source_bet_key": row.get("source_bet_key") if isinstance(row, dict) else None,
                    "wager_id": None,
                    "duplicate_status": "REFUSED",
                    "success": False,
                    "reason": str(exc),
                }
            )
            continue
        built.append(record.to_dict())
        receipts.append(
            {
                "row": index,
                "source_bet_key": record.source_bet_key,
                "wager_id": record.wager_id,
                # Provisional: the store decides. Corrected below from the keys
                # it actually wrote, because the store owns dedup and this
                # module must not answer for it.
                "duplicate_status": "NEW",
                "success": True,
            }
        )

    result = append_wagers(base_dir, season, built) if built else None

    written_keys = set(result.keys_written) if result else set()
    for receipt in receipts:
        if receipt["duplicate_status"] == "NEW" and receipt["source_bet_key"] not in written_keys:
            receipt["duplicate_status"] = "DUPLICATE_NOOP"

    return {
        "written": result.written if result else 0,
        "already_present": result.skipped_duplicate if result else 0,
        "refused": len(refusals),
        "refusals": refusals,
        "keys_written": list(result.keys_written) if result else [],
        "rows": receipts,
    }
