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

*** ONE SHARD PER UTC DATE, NOT ONE FILE PER SEASON ***
This module used to address one file per family-season
(`data/research/observations/2026.jsonl`). That monolith grew without
bound and on 2026-09-14 crossed GitHub's 100 MiB hard blob limit, so
every durable push -- and therefore every prospective capture -- started
failing with GH001. Storage is now sharded by the UTC date of the row's
own canonical timestamp (research.shards), and every function here that
used to take THE path now takes `(base_dir, season)` and consults
`shards.source_paths`: the legacy monolith (while it still exists) plus
every date shard.

Two properties are load-bearing and must not be weakened:

  * DEDUP STAYS GLOBAL. `observation_key` carries no timestamp, so the
    same logical observation re-derived tomorrow keys identically but
    dates differently. Every key set / index below is therefore built
    from ALL shards, never from the one shard a row is about to land in;
    otherwise sharding would silently start duplicating observations.
  * READS STILL SEE THE LEGACY FILE. `source_paths` lists the monolith
    first, so a not-yet-migrated corpus (or one a workflow materialised
    from an older commit) reads exactly as it always did. Writes never
    go back to it.

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

*** THE ONE-LOAD-PER-RUN CONTRACT (performance hardening) ***
`ObservationIndex` below exists because the scanner used to re-derive
"what is already in the corpus?" from disk once per MARKET TICKER --
`read_observation_rows` (a FULL pydantic re-validation of every historical
row) inside the per-ticker loop, plus another full JSON pass inside every
`append_observation_rows` call. With T tickers and H history rows that is
O(T x H) work per run, so runtime grew with the corpus even on runs that
captured nothing at all. `load_observation_index` does ONE pass over the
file and derives BOTH lookups the scanner needs (the canonical
`observation_key` set for dedup, and captured timing labels per market
ticker for scheduling), giving O(H + T). The index is a plain exact set /
dict -- deterministic, no probabilistic structure, no weakening of the
canonical key -- so dedup semantics are bit-for-bit what they were.

Like `_load_existing_keys` (and for the same schema-drift reason stated on
`append_json_rows`), the index is derived from the JSON-decoded dict, not
from a re-validated typed model: a corpus row written under an older
schema must never be able to break a NEW run's dedup. Lines that are not
decodable JSON are counted in `malformed_rows` and reported by run
telemetry rather than silently ignored.
"""

from __future__ import annotations

import json
import os
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from pathlib import Path
from typing import TypeVar

from cfb_edge_finder.research import shards
from cfb_edge_finder.schemas.attribution import ObservationAttribution
from cfb_edge_finder.schemas.capture_state import CaptureStateRecord
from cfb_edge_finder.schemas.corpus_row import ResearchCorpusRow
from cfb_edge_finder.schemas.settlement import MarketSettlement

T = TypeVar("T")

OBSERVATIONS_SUBDIR = shards.OBSERVATIONS_SUBDIR
SETTLEMENTS_SUBDIR = shards.SETTLEMENTS_SUBDIR
CAPTURE_STATE_SUBDIR = shards.CAPTURE_STATE_SUBDIR
ATTRIBUTIONS_SUBDIR = shards.ATTRIBUTIONS_SUBDIR
SHADOW_SUBDIR = shards.SHADOW_SUBDIR
"""Linked talent-shadow research records. A SEPARATE directory from
`observations` on purpose: canonical prospective observations stay
byte-identical and a reader that knows nothing about shadows never sees
them. Append-only, keyed by observation_key|shadow_model_version, so a
retry dedupes and a future candidate version coexists rather than
overwriting the evidence this one is collecting."""


def legacy_monolith_path(base_dir: Path, subdir: str, season: int) -> Path:
    """The PRE-SHARDING season file. Kept only so migration and the
    legacy-read path can name it; nothing writes here any more."""
    return shards.legacy_monolith_path(base_dir, subdir, season)


def corpus_sources(base_dir: Path, subdir: str, season: int) -> list[Path]:
    """Every file a reader of this family-season must consult -- legacy
    monolith first, then date shards in chronological order. THE
    replacement for the old `canonical_path`, which named the single
    file that both readers and writers used."""
    return shards.source_paths(base_dir, subdir, season)


PathSource = Path | Iterable[Path] | None
"""What every reader below accepts: a single file (a legacy monolith, or
a corpus a workflow materialised with `git show`), a shard DIRECTORY, or
an explicit ordered list from `corpus_sources`. Normalised by
`shards.as_source_paths`."""


@dataclass(frozen=True)
class AppendResult:
    written: int
    skipped_duplicate: int
    keys_written: tuple[str, ...] = field(default_factory=tuple)


def _load_existing_keys(source: PathSource, key_fn: Callable[[dict], str | None]) -> set[str]:
    """The key set across EVERY file of a family-season. Reading all
    shards (and any legacy monolith) here is what keeps dedup global
    rather than per-shard -- see this module's docstring."""
    keys: set[str] = set()
    for _path, line in shards.iter_raw_lines(source):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        key = key_fn(obj)
        if key is not None:
            keys.add(key)
    return keys


def append_json_rows(
    path: Path,
    rows: list[dict],
    key_fn: Callable[[dict], str | None],
    *,
    existing_keys: set[str] | None = None,
) -> AppendResult:
    """The single, generic append-only-with-dedup primitive. Every typed
    wrapper below (`append_observation_rows`, `append_settlement_rows`,
    `append_capture_state_rows`) reduces to this. `key_fn` is applied to
    the JSON-decoded dict, not the typed model, so re-reading an existing
    file never requires re-validating every historical row against the
    CURRENT schema (mission section 27: schema drift must never corrupt
    older rows -- a schema-version bump on new rows does not require
    rewriting or re-validating old ones just to compute dedup keys).

    `existing_keys`, when supplied, is the ALREADY-LOADED set of keys
    currently on disk for this file -- the caller promises it was derived
    from this same file with this same `key_fn` (see
    `load_observation_index`). It is a pure read-cache: passing it changes
    only HOW MANY TIMES the file is read, never which rows are considered
    duplicates. Omit it and the set is loaded from disk exactly as before,
    which is what every non-scanner caller still does.

    This single-file form is retained for callers that genuinely own one
    file (the shard writer itself, and tests of the primitive). Durable
    corpus writes go through `append_sharded_json_rows`."""
    existing = _load_existing_keys(path, key_fn) if existing_keys is None else existing_keys
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


def append_sharded_json_rows(
    base_dir: Path,
    subdir: str,
    season: int,
    rows: list[dict],
    key_fn: Callable[[dict], str | None],
    *,
    existing_keys: set[str] | None = None,
) -> AppendResult:
    """THE durable append. Identical dedup semantics to
    `append_json_rows` -- a row is written only when its key is neither
    already on disk (across EVERY shard plus any legacy monolith) nor
    earlier in this same batch -- but the surviving rows are routed to
    the UTC-date shard their own timestamp names, rolling over to a new
    part before any shard can approach GitHub's blob limit.

    Dedup is computed before dating on purpose: which shard a row lands
    in must never influence whether it is considered new, or the same
    observation re-derived on a later date would be written twice."""
    existing = (
        _load_existing_keys(shards.source_paths(base_dir, subdir, season), key_fn)
        if existing_keys is None
        else existing_keys
    )
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
        shards.append_rows(base_dir, subdir, season, [row for _key, row in to_write])

    return AppendResult(
        written=len(to_write),
        skipped_duplicate=skipped,
        keys_written=tuple(k for k, _ in to_write),
    )


def _read_all(source: PathSource) -> list[dict]:
    rows: list[dict] = []
    for _path, line in shards.iter_raw_lines(source):
        rows.append(json.loads(line))
    return rows


# --- Observations -----------------------------------------------------


def observation_key_of(obj: dict) -> str | None:
    """THE canonical dedup identity for an observation row -- the single
    definition `append_observation_rows`, `read_observation_keys` and
    `load_observation_index` all share, so an index can never drift from
    the key the append path actually enforces."""
    return obj.get("observation_key")


def _observation_ticker_and_label(obj: dict) -> tuple[str, str] | None:
    """The (market ticker, captured timing label) pair the SCHEDULER needs
    from a historical row -- read straight off the decoded dict, never via
    a typed re-validation (see this module's docstring)."""
    observation = obj.get("observation")
    if not isinstance(observation, dict):
        return None
    ticker = observation.get("kalshi_market_ticker")
    timing = observation.get("snapshot_timing")
    if not isinstance(ticker, str) or not isinstance(timing, dict):
        return None
    label = timing.get("label")
    if not isinstance(label, str):
        return None
    return ticker, label


@dataclass
class ObservationIndex:
    """Everything one scanner run needs to know about the EXISTING corpus,
    derived in a single pass (see this module's docstring for the O(T x H)
    -> O(H + T) reason this type exists).

    Deliberately NOT frozen: `labels_by_ticker` is a LIVE view that the
    scanner updates as it buffers new rows, exactly reproducing the old
    read-it-back-from-disk behaviour for the (pathological but possible)
    case of one ticker being visited twice in a single run. `keys` stays
    the ON-DISK key set so it can be handed to `append_json_rows` as its
    `existing_keys` without the batch deduplicating itself away."""

    keys: set[str] = field(default_factory=set)
    labels_by_ticker: dict[str, set[str]] = field(default_factory=dict)
    ticker_game_ids: dict[str, str] = field(default_factory=dict)
    """ticker -> game_id, from the same single pass. Consumed by
    checkpoint reconciliation so it never needs a second file read."""
    ticker_kickoffs: dict[str, tuple[str, str]] = field(default_factory=dict)
    """ticker -> (captured_at, kickoff_utc_at_capture) of the LATEST row
    that stated a kickoff -- later captures supersede earlier ones, so a
    recorded reschedule wins. Strings, parsed only by the consumer."""
    row_count: int = 0
    malformed_rows: int = 0
    load_count: int = 0
    load_seconds: float = 0.0

    def captured_labels_for(self, ticker: str) -> set[str]:
        """The timing labels already captured for `ticker` -- the exact
        set the old per-ticker full-file read computed."""
        return self.labels_by_ticker.get(ticker, set())

    def register_pending(self, row: dict) -> None:
        """Record a row the caller has BUFFERED but not yet written, so a
        later lookup in the same run sees it just as it would have seen it
        by re-reading the file mid-run. Deliberately does not touch
        `keys`: dedup against disk is still decided by the append path."""
        pair = _observation_ticker_and_label(row)
        if pair is not None:
            ticker, label = pair
            self.labels_by_ticker.setdefault(ticker, set()).add(label)


def load_observation_index(source: PathSource) -> ObservationIndex:
    """Read the observation corpus EXACTLY ONCE -- one streaming pass
    over every shard (and the legacy monolith, while it exists) -- and
    derive every lookup a scanner run needs from that single pass.
    `load_count`/`load_seconds` are carried on the result specifically so
    a test (and run telemetry) can assert the once-per-run property
    rather than merely hoping for it.

    `load_count` counts CORPUS loads, not files: the whole point of the
    property is that the scanner re-derives history once per attempt, and
    that is unchanged by how many files the history happens to live in."""
    index = ObservationIndex()
    started = time.perf_counter()
    index.load_count = 1

    for _path, line in shards.iter_raw_lines(source):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            # Same tolerance `_load_existing_keys` has always had, but
            # counted here instead of invisible -- run telemetry
            # reports it (mission section 12's "malformed rows").
            index.malformed_rows += 1
            continue
        index.row_count += 1
        key = observation_key_of(obj)
        if key is not None:
            index.keys.add(key)
        pair = _observation_ticker_and_label(obj)
        if pair is not None:
            ticker, label = pair
            index.labels_by_ticker.setdefault(ticker, set()).add(label)
            observation = obj.get("observation") or {}
            game_id = observation.get("game_id")
            if isinstance(game_id, str) and game_id:
                index.ticker_game_ids.setdefault(ticker, game_id)
            kickoff = obj.get("kickoff_utc_at_capture")
            captured_at = str(observation.get("captured_at") or "")
            if isinstance(kickoff, str) and kickoff:
                prior = index.ticker_kickoffs.get(ticker)
                if prior is None or captured_at >= prior[0]:
                    index.ticker_kickoffs[ticker] = (captured_at, kickoff)

    index.load_seconds = time.perf_counter() - started
    return index


def load_observation_index_for(base_dir: Path, season: int) -> ObservationIndex:
    """`load_observation_index` over the canonical source list for one
    season -- the form every production caller uses."""
    return load_observation_index(corpus_sources(base_dir, OBSERVATIONS_SUBDIR, season))


def append_observation_rows(
    base_dir: Path,
    season: int,
    rows: Iterable[ResearchCorpusRow],
    *,
    index: ObservationIndex | None = None,
) -> AppendResult:
    """Appends with the usual exact-key dedup, into the UTC-date shard
    each row's own `captured_at` names. When `index` is supplied, its
    already-loaded `keys` are used instead of re-reading the corpus, and
    the index is updated with whatever was actually written so it stays a
    faithful picture of the corpus for the rest of the run."""
    dicts = [r.model_dump(mode="json") for r in rows]
    result = append_sharded_json_rows(
        base_dir,
        OBSERVATIONS_SUBDIR,
        season,
        dicts,
        key_fn=observation_key_of,
        existing_keys=None if index is None else index.keys,
    )
    if index is not None:
        written_keys = set(result.keys_written)
        index.keys |= written_keys
        index.row_count += result.written
        for row in dicts:
            if observation_key_of(row) in written_keys:
                index.register_pending(row)
    return result


def read_observation_rows(source: PathSource) -> list[ResearchCorpusRow]:
    return [ResearchCorpusRow.model_validate(obj) for obj in _read_all(source)]


def read_observation_keys(source: PathSource) -> set[str]:
    return _load_existing_keys(source, key_fn=observation_key_of)


# --- Talent-shadow sidecar ----------------------------------------------


def shadow_key_of(obj: dict) -> str | None:
    return obj.get("shadow_key")


def append_shadow_rows(base_dir: Path, season: int, rows: list[dict]) -> AppendResult:
    """The talent-shadow sidecar, sharded on the same rule as everything
    else. Keyed on `shadow_key` (observation_key|shadow_model_version),
    so a push retry still writes zero duplicates."""
    return append_sharded_json_rows(
        base_dir, SHADOW_SUBDIR, season, rows, key_fn=shadow_key_of
    )


def read_shadow_rows(base_dir: Path, season: int) -> list[dict]:
    return _read_all(corpus_sources(base_dir, SHADOW_SUBDIR, season))


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


def append_settlement_rows(
    base_dir: Path, season: int, rows: Iterable[MarketSettlement]
) -> AppendResult:
    dicts = [r.model_dump(mode="json") for r in rows]
    return append_sharded_json_rows(
        base_dir, SETTLEMENTS_SUBDIR, season, dicts, key_fn=_settlement_fact_key
    )


def read_settlement_rows(source: PathSource) -> list[MarketSettlement]:
    return [MarketSettlement.model_validate(obj) for obj in _read_all(source)]


def latest_settlements(rows: Iterable[MarketSettlement]) -> dict[tuple[str, str], MarketSettlement]:
    """Folds the append-only settlement log to "current settlement per
    (game_id, kalshi_market_ticker)" -- file order is append order, so the
    last row for a key wins."""
    latest: dict[tuple[str, str], MarketSettlement] = {}
    for row in rows:
        latest[(row.game_id, row.kalshi_market_ticker)] = row
    return latest


# --- Observation attributions -------------------------------------------
# One row per CAPTURED OBSERVATION (not per market -- see
# schemas/attribution.py for why collapsing checkpoints would destroy the
# timing dimension). Dedup is on `attribution_key`, which embeds the
# settlement code version, so re-running unchanged code is a no-op while a
# genuine settlement-logic revision appends a new conclusion alongside the
# old one instead of silently overwriting it.


def attribution_key_of(obj: dict) -> str | None:
    return obj.get("attribution_key")


@dataclass
class AttributionIndex:
    """What one settlement run needs to know about work already done,
    derived in a single pass over the attributions file.

    Exists for the same reason `ObservationIndex` does: settlement must
    not become another O(observations x settlements) nested scan. Loading
    this once per run makes "which observations still need attributing?"
    an O(1) set-membership test per observation instead of a re-read."""

    keys: set[str] = field(default_factory=set)
    settled_observation_keys: set[str] = field(default_factory=set)
    row_count: int = 0
    malformed_rows: int = 0
    load_count: int = 0
    load_seconds: float = 0.0

    def already_attributed(self, observation_key: str, code_version: str) -> bool:
        return f"{observation_key}|{code_version}" in self.keys


def load_attribution_index(source: PathSource) -> AttributionIndex:
    """Read the attribution corpus EXACTLY ONCE -- one streaming pass
    across every shard and any legacy monolith -- and derive every lookup
    a settlement run needs. `load_count` is carried on the result so a
    test can assert the once-per-run property rather than hoping for
    it."""
    index = AttributionIndex()
    started = time.perf_counter()
    index.load_count = 1

    for _path, line in shards.iter_raw_lines(source):
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            index.malformed_rows += 1
            continue
        index.row_count += 1
        key = attribution_key_of(obj)
        if key is not None:
            index.keys.add(key)
        observation_key = obj.get("observation_key")
        if isinstance(observation_key, str):
            index.settled_observation_keys.add(observation_key)

    index.load_seconds = time.perf_counter() - started
    return index


def load_attribution_index_for(base_dir: Path, season: int) -> AttributionIndex:
    return load_attribution_index(corpus_sources(base_dir, ATTRIBUTIONS_SUBDIR, season))


def append_attribution_rows(
    base_dir: Path,
    season: int,
    rows: Iterable[ObservationAttribution],
    *,
    index: AttributionIndex | None = None,
) -> AppendResult:
    dicts = [r.model_dump(mode="json") for r in rows]
    result = append_sharded_json_rows(
        base_dir,
        ATTRIBUTIONS_SUBDIR,
        season,
        dicts,
        key_fn=attribution_key_of,
        existing_keys=None if index is None else index.keys,
    )
    if index is not None:
        written = set(result.keys_written)
        index.keys |= written
        index.row_count += result.written
        for row in dicts:
            if attribution_key_of(row) in written:
                observation_key = row.get("observation_key")
                if isinstance(observation_key, str):
                    index.settled_observation_keys.add(observation_key)
    return result


def read_attribution_rows(source: PathSource) -> list[ObservationAttribution]:
    return [ObservationAttribution.model_validate(obj) for obj in _read_all(source)]


# --- Capture-state log ---------------------------------------------------


def _capture_state_fact_key(obj: dict) -> str | None:
    required = ("game_id", "kalshi_market_ticker", "timing_label", "state")
    if any(obj.get(f) is None for f in required):
        return None
    return "|".join(str(obj[f]) for f in required)


def append_capture_state_rows(
    base_dir: Path, season: int, rows: Iterable[CaptureStateRecord]
) -> AppendResult:
    """Dedup key deliberately excludes `observed_at`/`run_id`: once a
    checkpoint reaches CAPTURED (or MISSED_WINDOW) for a given
    (game, market, label), re-observing the SAME state on a later scan is
    a no-op, not a new history row -- only a genuine state TRANSITION
    (e.g. NOT_YET_DUE -> CAPTURED) appends. That key carries no date, so
    dedup must (and does) span every shard: a checkpoint that reached
    CAPTURED last week must not re-append today just because today is a
    different shard."""
    dicts = [r.model_dump(mode="json") for r in rows]
    return append_sharded_json_rows(
        base_dir, CAPTURE_STATE_SUBDIR, season, dicts, key_fn=_capture_state_fact_key
    )


def read_capture_state_rows(source: PathSource) -> list[CaptureStateRecord]:
    return [CaptureStateRecord.model_validate(obj) for obj in _read_all(source)]


def latest_capture_states(rows: Iterable[CaptureStateRecord]) -> dict[tuple[str, str, str], CaptureStateRecord]:
    latest: dict[tuple[str, str, str], CaptureStateRecord] = {}
    for row in sorted(rows, key=lambda r: r.observed_at):
        latest[(row.game_id, row.kalshi_market_ticker, row.timing_label)] = row
    return latest
