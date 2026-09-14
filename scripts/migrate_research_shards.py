#!/usr/bin/env python3
"""Migrate the season-monolith research JSONL store to UTC-date shards.

    python scripts/migrate_research_shards.py \
        --data-repo-dir <checkout of research-data> --season 2026 --report out.json

*** WHY THIS EXISTS ***
`data/research/observations/2026.jsonl` reached 99.72 MiB and the next
append pushed it past GitHub's 100 MiB hard blob limit, so every durable
push -- and therefore every prospective capture, including unrecoverable
CLOSING lines -- began failing with GH001. `research/shards.py` fixes the
write path; this moves the rows that are already there.

*** WHAT IT IS NOT ALLOWED TO DO ***
This is a RELOCATION, not a rewrite. Every line is moved BYTE-FOR-BYTE:
lines are never re-serialised, re-ordered within a shard, re-validated
against the current schema, regenerated, semantically rewritten, or
backfilled. Nothing is dropped, sampled, compacted or deduplicated --
the pre-migration corpus already has zero duplicate keys and this must
not be the run that changes that in either direction. There is no Git
LFS anywhere in this path.

*** THE EQUIVALENCE PROOF ***
The monolith is deleted only after ALL of these hold, per family:

  1. row count out == row count in
  2. the MULTISET of raw lines out == the multiset in (byte equality,
     which subsumes any weaker notion of "same data")
  3. the multiset of dedup KEYS out == the multiset in, so zero keys
     went missing and zero keys gained a second row
  4. the original relative order of rows is preserved within every shard
  5. no resulting blob exceeds the shard target size

Any failure leaves the monolith exactly where it was and exits non-zero;
the shards written so far are reported so the operator can inspect them.
`--dry-run` performs steps 1-5 against a temporary directory and never
touches the real tree at all.

Re-running after a successful migration is a no-op: with the monolith
gone there is nothing to move, and the shard dedup would reject the rows
anyway.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.research import shards  # noqa: E402

DEDUP_KEY_FIELDS: dict[str, tuple[str, ...]] = {
    shards.OBSERVATIONS_SUBDIR: ("observation_key",),
    shards.ATTRIBUTIONS_SUBDIR: ("attribution_key",),
    shards.SHADOW_SUBDIR: ("shadow_key",),
    shards.CAPTURE_STATE_SUBDIR: ("game_id", "kalshi_market_ticker", "timing_label", "state"),
    shards.SETTLEMENTS_SUBDIR: (
        "game_id",
        "kalshi_market_ticker",
        "status",
        "derived_contract_settlement",
        "official_kalshi_settlement",
    ),
    shards.V2_SHADOW_SUBDIR: ("observation_key", "v2_model_version"),
}
"""The dedup identity the LIVE append path enforces for each family,
restated here so the proof checks the same notion of "a key" that
production does. `heartbeats` is absent on purpose: it has no dedup key
(every invocation is its own row), so for it the byte-multiset and count
checks are the whole proof."""


def dedup_key_of(row: dict, subdir: str) -> str | None:
    fields = DEDUP_KEY_FIELDS.get(subdir)
    if not fields:
        return None
    values = [row.get(field) for field in fields]
    if any(value is None for value in values):
        return None
    return "|".join(str(value) for value in values)


class MigrationError(RuntimeError):
    pass


def _read_lines(path: Path) -> list[str]:
    """Raw, unparsed, in file order. Blank lines are dropped because the
    JSONL readers already ignore them and a blank line carries no row."""
    if not path.is_file():
        return []
    with path.open("r", encoding="utf-8") as handle:
        return [line.strip() for line in handle if line.strip()]


def _key_counter(lines: list[str], subdir: str) -> tuple[Counter, int, int]:
    """(key multiset, rows with no derivable key, unparseable rows)."""
    keys: Counter = Counter()
    keyless = 0
    malformed = 0
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            malformed += 1
            continue
        key = dedup_key_of(row, subdir)
        if key is None:
            keyless += 1
        else:
            keys[key] += 1
    return keys, keyless, malformed


def migrate_family(
    base_dir: Path,
    subdir: str,
    season: int,
    *,
    target_bytes: int = shards.SHARD_TARGET_BYTES,
) -> dict:
    """Move one family-season's monolith into date shards and prove the
    move. Returns the report; raises MigrationError if the proof fails,
    having left the monolith untouched."""
    legacy = shards.legacy_monolith_path(base_dir, subdir, season)
    report: dict = {
        "family": subdir,
        "season": season,
        "legacy_monolith": str(legacy),
        "legacy_present": legacy.is_file(),
        "legacy_bytes": legacy.stat().st_size if legacy.is_file() else 0,
    }
    if not legacy.is_file():
        report.update(
            status="NOTHING_TO_MIGRATE",
            rows_in=0,
            rows_out=0,
            shards_written=0,
            duplicate_keys=0,
            missing_keys=0,
            largest_shard_bytes=0,
        )
        return report

    before_lines = _read_lines(legacy)
    before_keys, before_keyless, before_malformed = _key_counter(before_lines, subdir)
    before_line_counts = Counter(before_lines)

    existing_shards = shards.shard_paths(base_dir, subdir, season)
    if existing_shards:
        raise MigrationError(
            f"{subdir}: refusing to migrate -- {len(existing_shards)} shard(s) already exist "
            f"alongside the monolith ({existing_shards[0].parent}). Migrating into a populated "
            f"shard set could duplicate rows; inspect and resolve by hand."
        )

    # Date every line by the SAME function the live write path uses, so a
    # migrated row and a freshly captured one can never disagree about
    # where they belong. Input order is preserved within each date.
    dated: list[tuple[str, str]] = []
    for line in before_lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # An unparseable line still has to survive; it simply cannot
            # be dated, so it goes to the deterministic undated shard.
            dated.append((shards.UNDATED_SHARD_DATE, line))
            continue
        dated.append((shards.shard_date_for(row, subdir), line))

    written = shards.append_lines(base_dir, subdir, season, dated, target_bytes=target_bytes)

    # --- the proof -----------------------------------------------------
    shard_files = shards.shard_paths(base_dir, subdir, season)
    after_lines: list[str] = []
    per_shard: dict[str, int] = {}
    for path in shard_files:
        lines = _read_lines(path)
        per_shard[path.name] = len(lines)
        after_lines.extend(lines)
    after_keys, after_keyless, after_malformed = _key_counter(after_lines, subdir)
    after_line_counts = Counter(after_lines)

    largest = max((path.stat().st_size for path in shard_files), default=0)
    duplicate_keys = sum(count - 1 for count in after_keys.values() if count > 1)
    missing_keys = sum(
        max(count - after_keys.get(key, 0), 0) for key, count in before_keys.items()
    )
    gained_keys = sum(
        max(count - before_keys.get(key, 0), 0) for key, count in after_keys.items()
    )

    # Order preservation: the original index of each line, read back in
    # (date, part, file-order), must be non-decreasing WITHIN each shard.
    order_violations = 0
    original_index: dict[str, list[int]] = {}
    for position, line in enumerate(before_lines):
        original_index.setdefault(line, []).append(position)
    for path in shard_files:
        previous = -1
        for line in _read_lines(path):
            # Identical lines are interchangeable by definition, so take
            # the earliest original position that keeps the sequence
            # monotonic if one exists.
            candidates = original_index.get(line, [])
            nxt = next((i for i in candidates if i > previous), None)
            if nxt is None:
                order_violations += 1
                continue
            previous = nxt

    failures: list[str] = []
    if len(after_lines) != len(before_lines):
        failures.append(f"row count {len(before_lines)} in vs {len(after_lines)} out")
    if after_line_counts != before_line_counts:
        failures.append("raw line multiset differs (byte-level inequality)")
    if after_keys != before_keys:
        failures.append(f"key multiset differs (missing={missing_keys} gained={gained_keys})")
    if duplicate_keys:
        failures.append(f"{duplicate_keys} duplicate dedup key(s) in the shard set")
    if after_keyless != before_keyless:
        failures.append(f"keyless rows {before_keyless} in vs {after_keyless} out")
    if after_malformed != before_malformed:
        failures.append(f"malformed rows {before_malformed} in vs {after_malformed} out")
    if order_violations:
        failures.append(f"{order_violations} row(s) out of original order within a shard")
    if largest > target_bytes:
        failures.append(f"largest shard {largest} B exceeds the {target_bytes} B target")

    report.update(
        rows_in=len(before_lines),
        rows_out=len(after_lines),
        distinct_keys_in=len(before_keys),
        distinct_keys_out=len(after_keys),
        keyless_rows=after_keyless,
        malformed_rows=after_malformed,
        duplicate_keys=duplicate_keys,
        missing_keys=missing_keys,
        gained_keys=gained_keys,
        order_violations=order_violations,
        shards_written=len(shard_files),
        rows_per_shard=per_shard,
        largest_shard_bytes=largest,
        files_touched=sorted(str(path) for path in written),
        byte_identical=after_line_counts == before_line_counts,
    )

    if failures:
        report.update(status="FAILED", failures=failures)
        raise MigrationError(f"{subdir}: equivalence proof FAILED -- " + "; ".join(failures))

    legacy.unlink()
    report.update(status="MIGRATED", legacy_removed=True)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-repo-dir", type=Path, required=True)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument(
        "--families",
        nargs="*",
        default=list(shards.SHARDED_FAMILIES),
        help="corpus families to migrate (default: every sharded family)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="prove the migration against a COPY; the real tree is never touched",
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--target-bytes",
        type=int,
        default=shards.SHARD_TARGET_BYTES,
        help="roll a date's shard over past this size",
    )
    args = parser.parse_args()

    base_dir = args.data_repo_dir / "data" / "research"
    if not base_dir.is_dir():
        print(f"ERROR: {base_dir} is not a directory", file=sys.stderr)
        return 2

    workspace = base_dir
    temp_root: Path | None = None
    if args.dry_run:
        temp_root = Path(tempfile.mkdtemp(prefix="shard-migration-dry-run-"))
        workspace = temp_root / "research"
        workspace.mkdir(parents=True)
        for family in args.families:
            legacy = shards.legacy_monolith_path(base_dir, family, args.season)
            if legacy.is_file():
                (workspace / family).mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, shards.legacy_monolith_path(workspace, family, args.season))

    reports: list[dict] = []
    failed = False
    try:
        for family in args.families:
            try:
                reports.append(
                    migrate_family(workspace, family, args.season, target_bytes=args.target_bytes)
                )
            except MigrationError as exc:
                failed = True
                print(f"FAILED: {exc}", file=sys.stderr)
                reports.append({"family": family, "season": args.season, "status": "FAILED", "error": str(exc)})
    finally:
        summary = {
            "mode": "dry-run" if args.dry_run else "apply",
            "season": args.season,
            "data_repo_dir": str(args.data_repo_dir),
            "target_bytes": args.target_bytes,
            "families": reports,
            "totals": {
                "rows_in": sum(r.get("rows_in", 0) for r in reports),
                "rows_out": sum(r.get("rows_out", 0) for r in reports),
                "duplicate_keys": sum(r.get("duplicate_keys", 0) for r in reports),
                "missing_keys": sum(r.get("missing_keys", 0) for r in reports),
                "shards_written": sum(r.get("shards_written", 0) for r in reports),
                "largest_shard_bytes": max((r.get("largest_shard_bytes", 0) for r in reports), default=0),
            },
        }
        if args.report is not None:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")

        header = (
            f"{'family':<15} {'status':<18} {'rows_in':>9} {'rows_out':>9} "
            f"{'shards':>7} {'dupe':>5} {'missing':>8} {'largest_MB':>11}"
        )
        print(header)
        print("-" * len(header))
        for report in reports:
            print(
                f"{report.get('family',''):<15} {report.get('status',''):<18} "
                f"{report.get('rows_in',0):>9} {report.get('rows_out',0):>9} "
                f"{report.get('shards_written',0):>7} {report.get('duplicate_keys',0):>5} "
                f"{report.get('missing_keys',0):>8} "
                f"{report.get('largest_shard_bytes',0) / 1_000_000:>11.2f}"
            )
        totals = summary["totals"]
        print("-" * len(header))
        print(
            f"{'TOTAL':<15} {'':<18} {totals['rows_in']:>9} {totals['rows_out']:>9} "
            f"{totals['shards_written']:>7} {totals['duplicate_keys']:>5} {totals['missing_keys']:>8} "
            f"{totals['largest_shard_bytes'] / 1_000_000:>11.2f}"
        )
        if temp_root is not None:
            shutil.rmtree(temp_root, ignore_errors=True)

    if failed:
        print("\nSTATUS: MIGRATION FAILED -- no monolith was removed.", file=sys.stderr)
        return 1
    print(
        "\nSTATUS: "
        + ("DRY RUN complete; nothing on disk was changed." if args.dry_run else "MIGRATION APPLIED.")
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
