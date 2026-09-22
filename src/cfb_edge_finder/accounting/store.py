"""Append-only JSONL storage for wagers that already happened.

DELIBERATELY SELF-CONTAINED
---------------------------
`research/persistence.py` has a good generic append-with-dedup primitive and
this module does not call it. That is not an oversight and not duplication for
its own sake: `research` is one of the packages
`tests/test_no_recommendation_surface.py` scans precisely because it must never
learn about staked money. Importing it here would create an edge between the
accounting ledger and the predictive corpus in the dependency graph, and the
invariant this package carries ("no predictive package may import accounting")
is far easier to state, test and trust when the graph between them is empty in
both directions. Eighty lines is a cheap price for that.

WHAT IS COPIED, ON PURPOSE
--------------------------
Two properties of the research store are load-bearing and reproduced exactly:

  * append-only. Rows are appended; nothing is rewritten or overwritten. A
    wager record is a claim about money that already moved, so correcting one
    by editing it in place would destroy the evidence of what was believed
    before.
  * deterministic-key dedup, computed against the WHOLE file. `source_bet_key`
    is derived from the venue's own order identity, so re-running the backfill
    re-derives the same key and writes nothing. This is what makes
    "rerunning produces zero new wagers" a property of the store rather than a
    promise about the caller.

NOT copied: date sharding. That exists in the research corpus because a
season's observations crossed GitHub's 100 MiB blob limit. A season of actual
wagers is tens of rows. One file per season stays diffable and reviewable, and
a ledger you can read end to end in one screen is worth more here than
headroom nobody will use.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

WAGERS_SUBDIR = "wagers"

#: Settlements live beside the wagers, not inside them. A settlement is a LATER
#: and SEPARATE observation than the wager it settles, and this store's whole
#: guarantee is that a row written is a row that stays written.
SETTLEMENTS_SUBDIR = "settlements"

#: Never `main`. Bot-written data stays out of reviewed code history, matching
#: the `research-data` convention this repo already established.
DATA_BRANCH = "accounting-data"


@dataclass(frozen=True)
class AppendResult:
    written: int
    skipped_duplicate: int
    keys_written: tuple[str, ...] = ()


def ledger_path(base_dir: Path, season: int) -> Path:
    return Path(base_dir) / WAGERS_SUBDIR / f"{season}.jsonl"


def settlement_ledger_path(base_dir: Path, season: int) -> Path:
    return Path(base_dir) / SETTLEMENTS_SUBDIR / f"{season}.jsonl"


def source_bet_key_of(obj: dict) -> str | None:
    key = obj.get("source_bet_key")
    return key if isinstance(key, str) and key.strip() else None


def read_rows(path: Path) -> list[dict]:
    """Raw dicts, not validated models.

    A row written under an older schema must never be able to break a NEW
    run's dedup -- the same reason `research.persistence` decodes rather than
    re-validates. A line that is not decodable JSON is surfaced, not skipped:
    silently ignoring an unreadable ledger line would let a corrupted row read
    as an absent wager, and an absent wager is exactly what invites writing a
    duplicate.
    """
    path = Path(path)
    if not path.exists():
        return []
    rows: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number} is not decodable JSON: {exc}") from exc
    return rows


def existing_keys(path: Path) -> set[str]:
    return {k for k in (source_bet_key_of(row) for row in read_rows(path)) if k is not None}


#: What must agree between two observations of the same settlement before the
#: second is treated as a harmless repeat. `settlement_id` is deliberately
#: absent: it is minted from `source_bet_key` alone, so it cannot differ when
#: the key matches and comparing it would prove nothing.
SETTLEMENT_IDENTITY_FIELDS = (
    "market_ticker",
    "side",
    "settlement_status",
    "settled_at",
    "result",
    "gross_return",
    "net_profit_loss",
    "venue",
)


def _settlement_conflict(existing: dict, incoming: dict) -> list[str]:
    """Field NAMES on which two settlements for one wager disagree.

    Names only, never values: this runs inside a public repository's Actions
    logs, and the whole point of the surrounding design is that a payout never
    reaches one.
    """
    return [
        name for name in SETTLEMENT_IDENTITY_FIELDS
        if existing.get(name) != incoming.get(name)
    ]


def append_settlements(base_dir: Path, season: int, rows: list[dict]) -> AppendResult:
    """Append every settlement whose wager is not already settled on disk.

    KEYED ON ``source_bet_key``, THE WAGER'S KEY, BECAUSE A MARKET SETTLES ONCE.
    A second observation of the same settlement is therefore a duplicate rather
    than a correction, and re-running the settlement pass is a no-op for the
    same structural reason re-running the import is.

    That also means an unsettled wager is recorded by having NO ROW HERE.
    `settlement.validate` refuses a PENDING row outright: a row that has to be
    superseded later has no place in a store whose guarantee is that rows are
    not superseded.
    """
    from .settlement import validate

    path = settlement_ledger_path(base_dir, season)
    path.parent.mkdir(parents=True, exist_ok=True)

    settled_rows = {
        key: row for row in read_rows(path)
        if (key := source_bet_key_of(row)) is not None
    }
    seen: dict[str, dict] = {}
    to_write: list[tuple[str, dict]] = []
    skipped = 0

    for row in rows:
        problems = validate(row)
        if problems:
            raise ValueError(
                f"refusing to write an invalid settlement row: {'; '.join(problems)}"
            )
        key = source_bet_key_of(row)
        if key is None:
            skipped += 1
            continue
        already = settled_rows.get(key) or seen.get(key)
        if already is not None:
            # A SECOND OBSERVATION OF THE SAME SETTLEMENT IS A DUPLICATE.
            # An IDENTICAL one is the no-op that makes re-running safe, and it
            # is skipped in silence.
            #
            # A DIFFERING one is not a duplicate at all -- it is a restatement
            # of money that is already recorded, and this store's guarantee is
            # that a row is never superseded. Dropping it quietly would leave
            # the ledger holding the first figure while the router believes the
            # second, with nothing anywhere saying they disagreed. So it is
            # REFUSED, loudly, for a person to resolve.
            if _settlement_conflict(already, row):
                raise ValueError(
                    "refusing to write a settlement that CONTRADICTS one already "
                    f"recorded for the same wager (fields: {_settlement_conflict(already, row)}). "
                    "A market settles once; this store never supersedes a row, so a "
                    "restated payout is a person's decision, not an append"
                )
            skipped += 1
            continue
        seen[key] = row
        to_write.append((key, row))

    if to_write:
        with path.open("a", encoding="utf-8") as handle:
            for _key, row in to_write:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    return AppendResult(
        written=len(to_write),
        skipped_duplicate=skipped,
        keys_written=tuple(k for k, _ in to_write),
    )


def append_wagers(base_dir: Path, season: int, rows: list[dict]) -> AppendResult:
    """Append every row whose key is not already on disk or earlier in this batch.

    Validation happens HERE rather than being left to the caller. A store that
    accepts anything and trusts its callers to have checked is a store where
    the first careless caller writes a model-provenance field into the ledger.
    """
    from .wager import validate

    path = ledger_path(base_dir, season)
    path.parent.mkdir(parents=True, exist_ok=True)

    on_disk = existing_keys(path)
    seen: set[str] = set()
    to_write: list[tuple[str, dict]] = []
    skipped = 0

    for row in rows:
        problems = validate(row)
        if problems:
            raise ValueError(
                f"refusing to write an invalid wager row: {'; '.join(problems)}"
            )
        key = source_bet_key_of(row)
        if key is None or key in on_disk or key in seen:
            skipped += 1
            continue
        seen.add(key)
        to_write.append((key, row))

    if to_write:
        with path.open("a", encoding="utf-8") as handle:
            for _key, row in to_write:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    return AppendResult(
        written=len(to_write),
        skipped_duplicate=skipped,
        keys_written=tuple(k for k, _ in to_write),
    )
