"""Shard-aware test helpers for the durable research corpus.

The corpus used to be one file per family-season, so tests could name
`persistence.canonical_path(base, SUBDIR, season)` and then `.exists()`,
`.read_text()` or `.write_text()` it directly. Since the GH001 incident
(see research/shards.py) a family-season is a SET of UTC-date shards,
plus possibly a legacy monolith left over from before migration.

These helpers keep the tests explicit about that rather than hiding it:
each one says whether it is talking about the whole corpus, the files it
lives in, or one specific shard. Nothing here reimplements production
logic -- every path decision is delegated to `research.shards`, so a test
can never accidentally assert against a layout production does not use.
"""

from __future__ import annotations

import contextlib
import json
import shutil
from pathlib import Path

from cfb_edge_finder.research import shards


def sources(base_dir: Path, subdir: str, season: int) -> list[Path]:
    """Exactly what a production reader would consult."""
    return shards.source_paths(base_dir, subdir, season)


def files(base_dir: Path, subdir: str, season: int) -> list[Path]:
    """The shard files only, in (date, part) order."""
    return shards.shard_paths(base_dir, subdir, season)


def exists(base_dir: Path, subdir: str, season: int) -> bool:
    return bool(sources(base_dir, subdir, season))


def lines(base_dir: Path, subdir: str, season: int) -> list[str]:
    """Every raw row, in the order a reader sees them."""
    return [line for _path, line in shards.iter_raw_lines(sources(base_dir, subdir, season))]


def rows(base_dir: Path, subdir: str, season: int) -> list[dict]:
    return [json.loads(line) for line in lines(base_dir, subdir, season)]


def text(base_dir: Path, subdir: str, season: int) -> str:
    body = lines(base_dir, subdir, season)
    return "".join(line + "\n" for line in body)


def total_bytes(base_dir: Path, subdir: str, season: int) -> int:
    return sum(path.stat().st_size for path in sources(base_dir, subdir, season))


def seed(base_dir: Path, subdir: str, season: int, raw_lines: list[str]) -> dict[Path, int]:
    """Write raw JSONL lines into the shards production would put them in
    -- the same `shards.append_lines` the live writer and the migration
    use, so a seeded fixture is laid out exactly like a real corpus."""
    dated = []
    for line in raw_lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            dated.append((shards.UNDATED_SHARD_DATE, line))
            continue
        dated.append((shards.shard_date_for(row, subdir), line))
    return shards.append_lines(base_dir, subdir, season, dated)


def seed_legacy_monolith(base_dir: Path, subdir: str, season: int, raw_lines: list[str]) -> Path:
    """Write a PRE-SHARDING `{season}.jsonl`. For tests that assert the
    transition guarantee: code on this branch must still read a corpus
    that has not been migrated yet."""
    path = shards.legacy_monolith_path(base_dir, subdir, season)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        for line in raw_lines:
            handle.write(line + "\n")
    return path


def sole_shard(base_dir: Path, subdir: str, season: int) -> Path:
    """The single shard file, asserting there is exactly one. For tests
    that need a concrete file to corrupt, truncate, or byte-compare."""
    found = files(base_dir, subdir, season)
    assert len(found) == 1, f"expected exactly one shard, found {[p.name for p in found]}"
    return found[0]


class CorpusRef:
    """A (base_dir, family, season) handle for tests.

    Replaces the old `persistence.canonical_path(...)` Path, which worked
    only while a family-season was ONE file. Deliberately not a Path
    subclass: a corpus is a set of shards, and a test that wants to
    corrupt or byte-compare a concrete file should say `sole_shard()` and
    mean it rather than get a single file by accident.

    `.base`/`.season` feed the production write API
    (`append_*(base_dir, season, rows)`); `.sources` feeds the read API.
    """

    def __init__(self, base_dir: Path, subdir: str, season: int) -> None:
        self.base = base_dir
        self.subdir = subdir
        self.season = season

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return f"CorpusRef({self.base}, {self.subdir!r}, {self.season})"

    @property
    def sources(self) -> list[Path]:
        return sources(self.base, self.subdir, self.season)

    @property
    def files(self) -> list[Path]:
        return files(self.base, self.subdir, self.season)

    @property
    def shard_dir(self) -> Path:
        return shards.shard_dir(self.base, self.subdir, self.season)

    @property
    def legacy_path(self) -> Path:
        return shards.legacy_monolith_path(self.base, self.subdir, self.season)

    def exists(self) -> bool:
        return exists(self.base, self.subdir, self.season)

    def lines(self) -> list[str]:
        return lines(self.base, self.subdir, self.season)

    def rows(self) -> list[dict]:
        return rows(self.base, self.subdir, self.season)

    def text(self) -> str:
        return text(self.base, self.subdir, self.season)

    def bytes(self) -> bytes:
        return text(self.base, self.subdir, self.season).encode("utf-8")

    def total_bytes(self) -> int:
        return total_bytes(self.base, self.subdir, self.season)

    def seed(self, raw_lines: list[str]):
        return seed(self.base, self.subdir, self.season, raw_lines)

    def seed_legacy_monolith(self, raw_lines: list[str]) -> Path:
        return seed_legacy_monolith(self.base, self.subdir, self.season, raw_lines)

    def sole_shard(self) -> Path:
        return sole_shard(self.base, self.subdir, self.season)

    def clear(self) -> None:
        """Remove every file of this family-season -- the shard set AND
        any legacy monolith. The equivalent of the truncating
        `path.write_text(...)` the pre-sharding tests used."""
        shutil.rmtree(self.shard_dir, ignore_errors=True)
        with contextlib.suppress(FileNotFoundError):
            self.legacy_path.unlink()

    def seed_text(self, body: str, *, replace: bool = True):
        """Seed from raw JSONL text, routing each line to the shard
        production would write it to."""
        if replace:
            self.clear()
        return self.seed([line for line in body.splitlines() if line.strip()])

    @contextlib.contextmanager
    def seed_writer(self, *, replace: bool = True):
        """A `handle.write(line)` sink that lands in real shards on exit.
        Keeps the shape of the old `with path.open("w") as handle:`
        fixtures while routing through the production shard writer."""
        chunks: list[str] = []

        class _Sink:
            def write(self, chunk: str) -> int:
                chunks.append(chunk)
                return len(chunk)

        sink = _Sink()
        yield sink
        self.seed_text("".join(chunks), replace=replace)


def ref(base_dir: Path, subdir: str, season: int) -> CorpusRef:
    return CorpusRef(base_dir, subdir, season)
