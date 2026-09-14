"""Deterministic UTC-date sharding for the durable research JSONL store.

*** THE INCIDENT THIS EXISTS FOR ***
Every research family was a single season monolith --
`data/research/{family}/{season}.jsonl`. On 2026-09-14 the collector
stopped being able to persist anything at all:

    remote: error: File data/research/observations/2026.jsonl is 105.20 MB;
            this exceeds GitHub's file size limit of 100.00 MB
    remote: error: GH001: Large files detected.
    ! [remote rejected] HEAD -> research-data (pre-receive hook declined)

with `data/research/shadow/2026.jsonl` (66.5 MiB) already past GitHub's
50 MB advisory warning and `attributions/2026.jsonl` (81.2 MiB) close
behind. A season monolith grows without bound by construction, so the
only question was ever WHEN the hard limit would be hit -- and when it
was, every prospective capture (including unrecoverable CLOSING lines)
started failing.

*** THE FIX: ONE SHARD PER UTC CAPTURE DATE ***
A row's shard is a PURE FUNCTION OF THE ROW: the UTC calendar date of
that family's own canonical timestamp field (see `FAMILY_DATE_FIELDS`).
The same row always lands in the same shard, on any machine, in any
order, on a retry or a re-migration -- which is what makes migration
provable and makes a concurrent-writer retry converge exactly as it did
when there was one file.

    data/research/observations/2026/2026-09-12.part001.jsonl

`{season}` stays in the path because a CFB season spans two calendar
years (August 2026 through January 2027), so the date year is NOT the
season and a loader must still be able to select one season's rows.

*** DEDUP IS STILL GLOBAL, NEVER PER-SHARD ***
`research.identity.observation_key` deliberately does NOT include a
capture timestamp, so the SAME logical observation re-derived on a later
run carries the same key but a later date. If dedup were scoped to the
target shard, that row would be written again into a new date file --
silently duplicating an observation, which is the one thing the
append-only corpus may never do. Every index/keys loader in
`research.persistence` therefore reads EVERY shard (plus any legacy
monolith) before deciding what is new. Sharding changes where a row is
stored; it changes nothing about whether it is stored.

*** THE SIZE GUARD ***
`SHARD_TARGET_BYTES` (45 MB) is the size at which the writer rolls a
date's shard over to a new `.partNNN` file, and `PUSH_REFUSAL_BYTES`
(90 MB) is the size at which `research.git_durable_store` refuses to
push at all rather than letting GitHub reject the whole branch with
GH001. Both are comfortably below GitHub's 100 MiB hard limit, and the
rollover means a single very busy capture day cannot re-create the
monolith problem one date at a time.

*** LEGACY MONOLITH READS ***
`source_paths` lists the legacy `{season}.jsonl` FIRST (when it still
exists), then the date shards in chronological order. That is what lets
code on this branch read a not-yet-migrated corpus, a corpus a workflow
materialised with `git show` from an older commit, or a half-migrated
tree, without a flag and without a second code path. Writes never go to
the monolith again.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from pathlib import Path

SHARD_TARGET_BYTES = 45_000_000
"""Roll a date's shard over to a new part once appending would take it
past this. 45 MB is deliberately well under GitHub's 50 MB advisory
warning as well as its 100 MiB hard limit, so a shard never even
generates the 'larger than recommended' warning line."""

PUSH_REFUSAL_BYTES = 90_000_000
"""A blob at or above this is refused locally by the durable store
before the push is attempted. GitHub's hard limit is 100 MiB
(104,857,600 bytes); refusing at 90 MB leaves ~14 MB of headroom so the
operator gets a precise local error naming the file instead of a GH001
pre-receive rejection after a wasted push."""

SHARD_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})\.part(\d{3,})\.jsonl$")
"""Shard file names are uniform -- part 1 is `.part001.jsonl`, never a
bare `{date}.jsonl`. A single shape means one regex, one glob, and a
plain lexicographic sort that is already (date, part) order."""

UNDATED_SHARD_DATE = "0000-00-00"
"""Where a row whose timestamp is missing or unparseable goes. It is a
real shard, not a discard: a row that cannot be dated is still evidence
and must survive migration byte-for-byte. Sorting first (all zeroes)
keeps it deterministic. The 2026 corpus has none of these -- every one
of its 204,271 rows carries a parseable UTC timestamp -- but a future
schema change must not be able to lose a row."""

OBSERVATIONS_SUBDIR = "observations"
SETTLEMENTS_SUBDIR = "settlements"
CAPTURE_STATE_SUBDIR = "capture_state"
ATTRIBUTIONS_SUBDIR = "attributions"
SHADOW_SUBDIR = "shadow"
V2_SHADOW_SUBDIR = "v2_shadow"
HEARTBEATS_SUBDIR = "heartbeats"

FAMILY_DATE_FIELDS: dict[str, tuple[tuple[str, ...], ...]] = {
    OBSERVATIONS_SUBDIR: (("observation", "captured_at"),),
    ATTRIBUTIONS_SUBDIR: (("captured_at",),),
    SHADOW_SUBDIR: (("captured_at",),),
    V2_SHADOW_SUBDIR: (("captured_at",),),
    CAPTURE_STATE_SUBDIR: (("observed_at",),),
    SETTLEMENTS_SUBDIR: (("settled_at",),),
    HEARTBEATS_SUBDIR: (("invoked_at",), ("started_at",)),
}
"""THE single definition of which timestamp decides each family's shard,
in fallback order. Migration and the live write path both read it, so a
migrated row and a freshly captured one with the same content can never
disagree about where they belong -- which is precisely what makes the
migration's equivalence proof meaningful."""

SHARDED_FAMILIES: tuple[str, ...] = tuple(FAMILY_DATE_FIELDS)


# --- shard identity ----------------------------------------------------


def _dig(obj: dict, path: Sequence[str]):
    cur = obj
    for part in path:
        if not isinstance(cur, dict):
            return None
        cur = cur.get(part)
    return cur


def utc_date_of(value) -> str | None:
    """The UTC calendar date of an ISO-8601 instant, or None when the
    value is absent or unparseable. Naive timestamps are read as UTC --
    every producer in this repo writes `datetime.now(UTC).isoformat()`,
    and reading a naive stamp as local time would make the shard depend
    on the machine, destroying determinism."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC).strftime("%Y-%m-%d")


def shard_date_for(obj: dict, subdir: str) -> str:
    """The shard date of one decoded row of `subdir`. Total: a row that
    carries no usable timestamp is dated `UNDATED_SHARD_DATE` rather
    than rejected, because this function decides where a row is SAVED
    and it must never be able to answer 'nowhere'."""
    for field_path in FAMILY_DATE_FIELDS.get(subdir, ()):
        date = utc_date_of(_dig(obj, field_path))
        if date is not None:
            return date
    return UNDATED_SHARD_DATE


# --- paths -------------------------------------------------------------


def shard_dir(base_dir: Path, subdir: str, season: int) -> Path:
    """The directory holding one family-season's date shards. A sibling
    of the legacy `{season}.jsonl` FILE, never a replacement for that
    name, so both can coexist during the transition."""
    return base_dir / subdir / str(season)


def legacy_monolith_path(base_dir: Path, subdir: str, season: int) -> Path:
    """The pre-sharding season monolith. Still READ when present (see
    `source_paths`); never written again."""
    return base_dir / subdir / f"{season}.jsonl"


def shard_name(date: str, part: int) -> str:
    return f"{date}.part{part:03d}.jsonl"


def shard_path(base_dir: Path, subdir: str, season: int, date: str, part: int) -> Path:
    return shard_dir(base_dir, subdir, season) / shard_name(date, part)


def parse_shard_name(name: str) -> tuple[str, int] | None:
    match = SHARD_FILENAME_RE.match(name)
    if match is None:
        return None
    return match.group(1), int(match.group(2))


def shard_paths(base_dir: Path, subdir: str, season: int) -> list[Path]:
    """Every date shard for one family-season, in (date, part) order.
    Sorted explicitly on the PARSED key rather than on the raw filename
    so the order stays correct if a part number ever needs more than
    three digits."""
    directory = shard_dir(base_dir, subdir, season)
    if not directory.is_dir():
        return []
    found: list[tuple[str, int, Path]] = []
    for entry in directory.iterdir():
        if not entry.is_file():
            continue
        parsed = parse_shard_name(entry.name)
        if parsed is None:
            continue
        found.append((parsed[0], parsed[1], entry))
    found.sort(key=lambda item: (item[0], item[1]))
    return [path for _date, _part, path in found]


def source_paths(base_dir: Path, subdir: str, season: int) -> list[Path]:
    """Everything a READER must consult for one family-season: the
    legacy monolith first (oldest rows, when it is still there), then
    the date shards chronologically. Files that do not exist are simply
    absent from the list, so a caller never needs its own `.exists()`
    dance."""
    paths: list[Path] = []
    legacy = legacy_monolith_path(base_dir, subdir, season)
    if legacy.is_file():
        paths.append(legacy)
    paths.extend(shard_paths(base_dir, subdir, season))
    return paths


def as_source_paths(source: Path | Iterable[Path] | None) -> list[Path]:
    """Normalise the many shapes a corpus location arrives in into an
    ordered list of existing files.

    A DIRECTORY is expanded to the shards inside it, a FILE is itself,
    a missing path contributes nothing, and an iterable is flattened in
    order with duplicates removed. This is what lets every historical
    `(path: Path)` reader keep working unchanged against a legacy
    monolith, a `git show`-materialised single file, or a full shard
    set, without each one growing its own branch."""
    if source is None:
        return []
    candidates = [source] if isinstance(source, (str, Path)) else list(source)
    resolved: list[Path] = []
    seen: set[Path] = set()
    for candidate in candidates:
        path = Path(candidate)
        if path.is_dir():
            found: list[tuple[str, int, Path]] = []
            for entry in path.iterdir():
                parsed = parse_shard_name(entry.name) if entry.is_file() else None
                if parsed is not None:
                    found.append((parsed[0], parsed[1], entry))
            found.sort(key=lambda item: (item[0], item[1]))
            expanded = [p for _d, _n, p in found]
        elif path.is_file():
            expanded = [path]
        else:
            expanded = []
        for item in expanded:
            if item not in seen:
                seen.add(item)
                resolved.append(item)
    return resolved


# --- reading -----------------------------------------------------------


def iter_raw_lines(source: Path | Iterable[Path] | None) -> Iterable[tuple[Path, str]]:
    """Yield `(path, line)` for every non-blank line across `source`, in
    file order within each file and in `as_source_paths` order across
    files."""
    for path in as_source_paths(source):
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                stripped = line.strip()
                if stripped:
                    yield path, stripped


# --- writing -----------------------------------------------------------


def _next_part_for(directory: Path, date: str) -> tuple[int, int]:
    """The part number a new row for `date` should go in, and the
    current size of that part's file. Picks the HIGHEST existing part so
    appends stay append-only: an earlier part is never reopened once a
    rollover has happened, which keeps already-pushed blobs byte-stable
    and their git history diff-friendly."""
    highest = 0
    if directory.is_dir():
        for entry in directory.iterdir():
            parsed = parse_shard_name(entry.name) if entry.is_file() else None
            if parsed is not None and parsed[0] == date:
                highest = max(highest, parsed[1])
    if highest == 0:
        return 1, 0
    existing = directory / shard_name(date, highest)
    return highest, existing.stat().st_size if existing.exists() else 0


def append_lines(
    base_dir: Path,
    subdir: str,
    season: int,
    dated_lines: Sequence[tuple[str, str]],
    *,
    target_bytes: int = SHARD_TARGET_BYTES,
) -> dict[Path, int]:
    """THE single rollover-aware append. `dated_lines` is an ordered
    sequence of `(shard_date, serialized_line_without_newline)`; every
    family's writer funnels through here so there is exactly one
    implementation of "when does a shard roll over".

    Rows are grouped by date (dates processed in sorted order, input
    order preserved WITHIN a date) and appended to the highest existing
    part for that date. When adding a line would take that part past
    `target_bytes` the next part is started. A single line larger than
    the target is still written -- to a part of its own -- because
    losing a row to a size policy would be far worse than one oversize
    shard, and `oversize_blobs` reports it loudly either way.

    Returns rows written per file. Each file is opened once, written,
    flushed and fsync'd, matching the durability discipline the
    monolithic writer had."""
    if not dated_lines:
        return {}
    directory = shard_dir(base_dir, subdir, season)
    directory.mkdir(parents=True, exist_ok=True)

    by_date: dict[str, list[str]] = {}
    for date, line in dated_lines:
        by_date.setdefault(date, []).append(line)

    # Plan every (file -> lines) assignment first, then do the I/O. The
    # split keeps the rollover arithmetic free of file handles and means
    # each target file is opened, written, flushed and fsync'd exactly
    # once -- the same durability discipline the monolithic writer had.
    batches: list[tuple[Path, list[str]]] = []
    for date in sorted(by_date):
        part, size = _next_part_for(directory, date)
        current: list[str] = []
        for line in by_date[date]:
            encoded = len(line.encode("utf-8")) + 1
            if size > 0 and size + encoded > target_bytes:
                if current:
                    batches.append((directory / shard_name(date, part), current))
                current = []
                part += 1
                size = 0
            current.append(line)
            size += encoded
        if current:
            batches.append((directory / shard_name(date, part), current))

    written: dict[Path, int] = {}
    for path, lines in batches:
        with path.open("a", encoding="utf-8") as handle:
            for item in lines:
                handle.write(item + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        written[path] = written.get(path, 0) + len(lines)
    return written


def append_rows(
    base_dir: Path,
    subdir: str,
    season: int,
    rows: Sequence[dict],
    *,
    serialize=None,
    target_bytes: int = SHARD_TARGET_BYTES,
) -> dict[Path, int]:
    """`append_lines` for decoded rows, dating each by the family's own
    canonical field. `serialize` defaults to the corpus's long-standing
    `json.dumps(..., sort_keys=True, default=str)` form so a sharded row
    is byte-identical to what the monolith would have held."""
    if serialize is None:

        def serialize(row: dict) -> str:  # noqa: E306
            return json.dumps(row, sort_keys=True, default=str)

    dated = [(shard_date_for(row, subdir), serialize(row)) for row in rows]
    return append_lines(base_dir, subdir, season, dated, target_bytes=target_bytes)


# --- the size guard ----------------------------------------------------


def oversize_blobs(
    root: Path,
    *,
    limit_bytes: int = SHARD_TARGET_BYTES,
    only: Iterable[Path] | None = None,
) -> list[tuple[Path, int]]:
    """Every file at or under `root` whose size is strictly above
    `limit_bytes`, largest first.

    `only` restricts the scan to a given set of paths -- what the
    durable store uses so an UNCHANGED oversize blob already on the
    remote (which creates no new blob and so cannot trip GH001) does not
    block a push that has nothing to do with it."""
    if only is not None:
        candidates = [Path(p) for p in only]
    elif root.is_dir():
        candidates = [p for p in root.rglob("*") if p.is_file()]
    elif root.is_file():
        candidates = [root]
    else:
        candidates = []

    found: list[tuple[Path, int]] = []
    for path in candidates:
        try:
            size = path.stat().st_size
        except OSError:
            continue
        if size > limit_bytes:
            found.append((path, size))
    found.sort(key=lambda item: item[1], reverse=True)
    return found


def describe_blobs(blobs: Sequence[tuple[Path, int]]) -> str:
    return "; ".join(f"{path} is {size / 1_000_000:.2f} MB" for path, size in blobs)
