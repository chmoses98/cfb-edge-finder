"""Milestone E, Part A: the durable, append-only research corpus store.

*** WHY JSONL + a dedicated git branch, not a database or object store ***
See docs/MILESTONE_E.md "Durable persistence" for the full comparison.
Short version: this repo already commits compact canonical artifacts to
git (docs/STORAGE_STRATEGY.md); a season's worth of normalized CFB
observations (thousands, not millions, of rows/week -- see
docs/MILESTONE_E.md's storage estimate) is well within what git handles
comfortably as line-oriented, diffable, append-only text, and a dedicated
`research-data` branch (never `main`) keeps bot commits out of the
reviewed code history entirely. No new infrastructure (bucket,
credentials, database) is needed for this volume.

*** THE DEDUP/APPEND MODEL ***
Each row's `observation_key` (research.identity.observation_key) is
DETERMINISTIC. `append_rows` here does the in-process half of the safety
guarantee: given a target file's CURRENT on-disk content, it appends only
rows whose key is not already present (in the file OR earlier in the same
batch) -- never overwrites, never rewrites an existing line. The
GIT-level half (surviving concurrent workflow runs without a race) lives
in research.git_durable_store, which re-reads this exact file fresh from
the latest fetched ref before every retry, so re-running this function
against updated on-disk content is always safe and convergent.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from cfb_edge_finder.schemas.capture_state import CaptureStateRecord
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.settlement import MarketSettlement

T = TypeVar("T")

OBSERVATIONS_SUBDIR = "observations"
SETTLEMENTS_SUBDIR = "settlements"
CAPTURE_STATE_SUBDIR = "capture_state"


def canonical_path(base_dir: Path, subdir: str, season: int) -> Path:
    return base_dir / subdir / f"{season}.jsonl"


@dataclass(frozen=True)
class AppendResult:
    written: int
    skipped_duplicate: int
    keys_written: tuple[str, ...] = field(default_factory=tuple)


def _load_existing_keys(path: Path, key_fn: Callable[[dict], str | None]) -> set[str]:
    if not path.exists():
        return set()
    keys: set[str] = set()
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                continue
            key = key_fn(obj)
            if key is not None:
                keys.add(key)
    return keys


def append_json_rows(path: Path, rows: list[dict], key_fn: Callable[[dict], str | None]) -> AppendResult:
    """The single, generic append-only-with-dedup primitive. Every typed
    wrapper below (`append_observation_rows`, `append_settlement_rows`,
    `append_capture_state_rows`) reduces to this. `key_fn` is applied to
    the JSON-decoded dict, not the typed model, so re-reading an existing
    file never requires re-validating every historical row against the
    CURRENT schema (mission section 27: schema drift must never corrupt
    older rows -- a schema-version bump on new rows does not require
    rewriting or re-validating old ones just to compute dedup keys)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    existing = _load_existing_keys(path, key_fn)
    seen_this_batch: set[str] = set()
    to_write: list[tuple[str, dict]] = []
    skipped = 0
    for row in rows:
        key = key_fn(row)
        if key is None or key in existing or key in seen_this_batch:
            skipped += 1
            continue
        seen_this_batch.add(key)
        to_write.append((key, row))

    if to_write:
        with path.open("a", encoding="utf-8") as handle:
            for _key, row in to_write:
                handle.write(json.dumps(row, sort_keys=True, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    return AppendResult(written=len(to_write), skipped_duplicate=skipped, keys_written=tuple(k for k, _ in to_write))


def _read_all(path: Path) -> list[dict]:
    if not path.exists():
        return []
    rows: list[dict] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            rows.append(json.loads(line))
    return rows


# --- Observations -----------------------------------------------------


def append_observation_rows(path: Path, rows: Iterable[ResearchCorpusRow]) -> AppendResult:
    dicts = [r.model_dump(mode="json") for r in rows]
    return append_json_rows(path, dicts, key_fn=lambda obj: obj.get("observation_key"))


def read_observation_rows(path: Path) -> list[ResearchCorpusRow]:
    return [ResearchCorpusRow.model_validate(obj) for obj in _read_all(path)]


def read_observation_keys(path: Path) -> set[str]:
    return _load_existing_keys(path, key_fn=lambda obj: obj.get("observation_key"))


# --- Settlements --------------------------------------------------------
# Settlement facts CAN legitimately change over time (PENDING_NOT_FINAL ->
# SETTLED, or an official Kalshi outcome arriving later) -- so the dedup
# key is a fingerprint of the FACT (key + status + both settlement
# outcomes), not just the settlement identity. Re-deriving an identical
# fact on a re-run is a no-op; a genuine state change appends a new row.
# The CURRENT settlement for a market is the latest row for its
# settlement_key -- callers fold the log (see `latest_settlements`).


def _settlement_fact_key(obj: dict) -> str | None:
    game_id = obj.get("game_id")
    ticker = obj.get("kalshi_market_ticker")
    if game_id is None or ticker is None:
        return None
    parts = [
        game_id,
        ticker,
        str(obj.get("status")),
        str(obj.get("derived_contract_settlement")),
        str(obj.get("official_kalshi_settlement")),
    ]
    return "|".join(parts)


def append_settlement_rows(path: Path, rows: Iterable[MarketSettlement]) -> AppendResult:
    dicts = [r.model_dump(mode="json") for r in rows]
    return append_json_rows(path, dicts, key_fn=_settlement_fact_key)


def read_settlement_rows(path: Path) -> list[MarketSettlement]:
    return [MarketSettlement.model_validate(obj) for obj in _read_all(path)]


def latest_settlements(rows: Iterable[MarketSettlement]) -> dict[tuple[str, str], MarketSettlement]:
    """Folds the append-only settlement log to "current settlement per
    (game_id, kalshi_market_ticker)" -- file order is append order, so the
    last row for a key wins."""
    latest: dict[tuple[str, str], MarketSettlement] = {}
    for row in rows:
        latest[(row.game_id, row.kalshi_market_ticker)] = row
    return latest


# --- Capture-state log ---------------------------------------------------


def _capture_state_fact_key(obj: dict) -> str | None:
    required = ("game_id", "kalshi_market_ticker", "timing_label", "state")
    if any(obj.get(f) is None for f in required):
        return None
    return "|".join(str(obj[f]) for f in required)


def append_capture_state_rows(path: Path, rows: Iterable[CaptureStateRecord]) -> AppendResult:
    """Dedup key deliberately excludes `observed_at`/`run_id`: once a
    checkpoint reaches CAPTURED (or MISSED_WINDOW) for a given
    (game, market, label), re-observing the SAME state on a later scan is
    a no-op, not a new history row -- only a genuine state TRANSITION
    (e.g. NOT_YET_DUE -> CAPTURED) appends."""
    dicts = [r.model_dump(mode="json") for r in rows]
    return append_json_rows(path, dicts, key_fn=_capture_state_fact_key)


def read_capture_state_rows(path: Path) -> list[CaptureStateRecord]:
    return [CaptureStateRecord.model_validate(obj) for obj in _read_all(path)]


def latest_capture_states(rows: Iterable[CaptureStateRecord]) -> dict[tuple[str, str, str], CaptureStateRecord]:
    latest: dict[tuple[str, str, str], CaptureStateRecord] = {}
    for row in sorted(rows, key=lambda r: r.observed_at):
        latest[(row.game_id, row.kalshi_market_ticker, row.timing_label)] = row
    return latest
