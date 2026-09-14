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

*** THE COEXISTENCE CASE, AND WHY IT IS NO LONGER A DEAD END ***
By default this refuses to run when shards already exist next to the
monolith, because migrating blindly into a populated shard set is how
rows get duplicated. That refusal used to be terminal, and it made one
specific sequence unrecoverable without hand surgery:

    merge -> a scheduled capture runs -> it writes the first shard
          -> monolith and shards now coexist -> migration refuses
          -> stuck, with the corpus split across two layouts

`--allow-existing-shards` makes that state RESOLVABLE. It does NOT relax
the proof; it strengthens it, because there is now a third thing that
could go wrong (a monolith row and a shard row claiming the same
identity). In this mode:

  * a monolith row whose dedup key is already in the shards is NOT
    appended again -- it is already present, and appending would create
    the duplicate this whole tool exists to prevent;
  * unless its bytes DIFFER from the shard row holding that key, which
    is a genuine conflict about what an observation says. That is not a
    thing to resolve automatically, so it fails with both lines named;
  * every other monolith row is appended exactly as in the normal path,
    byte-for-byte, in its original relative order;
  * the proof then requires that every pre-existing shard line still be
    present untouched, that every monolith key be present afterwards,
    and that the whole shard set still contain zero duplicate keys.

Nothing is ever deleted to make this converge. `research/maintenance.py`
exists so that the coexistence state should not arise in the first
place; this flag is what makes it survivable if it does.
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

from cfb_edge_finder.research import persistence, shards  # noqa: E402


def dedup_key_of(row: dict, subdir: str) -> str | None:
    """THE dedup identity, taken straight from the module that enforces
    it on the write path.

    Deliberately NOT restated here. An earlier draft did restate it, and
    required every field of the settlement fingerprint to be non-null --
    but production stringifies the outcome fields, and every real
    settlement row carries a null `official_kalshi_settlement`. The proof
    therefore counted all 9,544 settlement rows as "keyless" and its
    duplicate/missing check silently proved nothing about that family.
    One source of truth removes the whole class of bug."""
    key_fn = persistence.dedup_key_fn_for(subdir)
    return None if key_fn is None else key_fn(row)


KEYED_FAMILIES = tuple(f for f in shards.SHARDED_FAMILIES if persistence.dedup_key_fn_for(f))
"""`heartbeats` is absent on purpose: it has no dedup key (every
invocation is its own row), so for it the byte-multiset and count checks
are the whole proof."""


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
    allow_existing_shards: bool = False,
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
    # Only the KEY multiset of the monolith is still compared directly:
    # it is the one thing that must hold whatever else is in the shards
    # (no monolith key may go missing). The line/keyless/malformed
    # checks now run against `expected_*` below, which equals the
    # monolith's own counts whenever the shard set starts empty.
    before_keys, _before_keyless, _before_malformed = _key_counter(before_lines, subdir)

    existing_shards = shards.shard_paths(base_dir, subdir, season)
    if existing_shards and not allow_existing_shards:
        raise MigrationError(
            f"{subdir}: refusing to migrate -- {len(existing_shards)} shard(s) already exist "
            f"alongside the monolith ({existing_shards[0].parent}). Migrating blindly into a "
            f"populated shard set could duplicate rows. This is NOT a dead end: re-run with "
            f"--allow-existing-shards, which appends only the monolith rows whose dedup key is "
            f"not already in the shards and fails loudly on any genuine conflict. See "
            f"docs/CUTOVER_SHARDING.md."
        )

    # Everything already in the shards before this run. In the normal
    # (empty-shard) path these are all empty and every check below
    # degenerates to the original one.
    preexisting_lines: list[str] = []
    for path in existing_shards:
        preexisting_lines.extend(_read_lines(path))
    preexisting_counts = Counter(preexisting_lines)
    preexisting_keys, preexisting_keyless, preexisting_malformed = _key_counter(
        preexisting_lines, subdir
    )
    # Which exact line currently holds each key -- needed to tell a true
    # duplicate (same key, same bytes: skip) from a conflict (same key,
    # different bytes: refuse).
    line_for_key: dict[str, str] = {}
    for line in preexisting_lines:
        try:
            key = dedup_key_of(json.loads(line), subdir)
        except json.JSONDecodeError:
            continue
        if key is not None:
            line_for_key.setdefault(key, line)

    # Date every line by the SAME function the live write path uses, so a
    # migrated row and a freshly captured one can never disagree about
    # where they belong. Input order is preserved within each date.
    dated: list[tuple[str, str]] = []
    skipped_already_present = 0
    conflicts: list[str] = []
    keyless_budget = Counter(preexisting_counts)
    for line in before_lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            # An unparseable line still has to survive; it simply cannot
            # be dated, so it goes to the deterministic undated shard.
            dated.append((shards.UNDATED_SHARD_DATE, line))
            continue

        if existing_shards:
            key = dedup_key_of(row, subdir)
            if key is not None and key in preexisting_keys:
                held = line_for_key.get(key)
                if held == line:
                    # Already there, byte-for-byte. Appending it again is
                    # the duplicate this tool exists to prevent.
                    skipped_already_present += 1
                    continue
                conflicts.append(
                    f"key {key!r} is in the shards as {held!r} but the monolith has {line!r}"
                )
                continue
            if key is None and keyless_budget.get(line, 0) > 0:
                # A keyless family (heartbeats) can only be compared by
                # bytes. An identical line already present is the same
                # row, so consume one and skip it.
                keyless_budget[line] -= 1
                skipped_already_present += 1
                continue

        dated.append((shards.shard_date_for(row, subdir), line))

    if conflicts:
        report.update(status="FAILED", failures=conflicts, conflicts=conflicts)
        raise MigrationError(
            f"{subdir}: refusing to migrate -- {len(conflicts)} row(s) exist in BOTH the "
            f"monolith and the shards under the same dedup key with different content. "
            f"Neither copy may be discarded automatically. First: {conflicts[0]}"
        )

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

    # *** WHAT THE SHARD SET MUST CONTAIN AFTERWARDS ***
    # Everything that was already in the shards, plus exactly the
    # monolith lines this run decided to append. With no pre-existing
    # shards (the normal cutover) `preexisting_*` are empty and
    # `appended_lines` is `before_lines`, so every check below is
    # identical to the original monolith-only proof.
    appended_lines = [line for _, line in dated]
    expected_line_counts = preexisting_counts + Counter(appended_lines)
    expected_keys, expected_keyless, expected_malformed = _key_counter(
        preexisting_lines + appended_lines, subdir
    )

    largest = max((path.stat().st_size for path in shard_files), default=0)
    duplicate_keys = sum(count - 1 for count in after_keys.values() if count > 1)
    # The load-bearing one: no key that was in the monolith may be
    # absent from the shards, however it got there.
    missing_keys = sum(
        max(count - after_keys.get(key, 0), 0) for key, count in before_keys.items()
    )
    gained_keys = sum(
        max(count - expected_keys.get(key, 0), 0) for key, count in after_keys.items()
    )
    # No pre-existing shard line may have been disturbed.
    preexisting_lost = sum(
        max(count - after_line_counts.get(line, 0), 0)
        for line, count in preexisting_counts.items()
    )

    # Order preservation: the original index of each MONOLITH line, read
    # back in (date, part, file-order), must be non-decreasing WITHIN
    # each shard. Lines that were already in the shards before this run
    # are skipped -- they have no position in the monolith to preserve.
    order_violations = 0
    original_index: dict[str, list[int]] = {}
    for position, line in enumerate(before_lines):
        original_index.setdefault(line, []).append(position)
    for path in shard_files:
        previous = -1
        budget = Counter(preexisting_counts)
        for line in _read_lines(path):
            if budget.get(line, 0) > 0:
                budget[line] -= 1
                continue
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
    if len(after_lines) != len(preexisting_lines) + len(appended_lines):
        failures.append(
            f"row count {len(preexisting_lines)} existing + {len(appended_lines)} appended "
            f"vs {len(after_lines)} out"
        )
    if after_line_counts != expected_line_counts:
        failures.append("raw line multiset differs (byte-level inequality)")
    if preexisting_lost:
        failures.append(f"{preexisting_lost} pre-existing shard line(s) lost")
    if after_keys != expected_keys:
        failures.append(f"key multiset differs (missing={missing_keys} gained={gained_keys})")
    if missing_keys:
        failures.append(f"{missing_keys} monolith dedup key(s) absent from the shard set")
    if duplicate_keys:
        failures.append(f"{duplicate_keys} duplicate dedup key(s) in the shard set")
    if after_keyless != expected_keyless:
        failures.append(f"keyless rows {expected_keyless} expected vs {after_keyless} out")
    if after_malformed != expected_malformed:
        failures.append(f"malformed rows {expected_malformed} expected vs {after_malformed} out")
    if order_violations:
        failures.append(f"{order_violations} row(s) out of original order within a shard")
    if largest > target_bytes:
        failures.append(f"largest shard {largest} B exceeds the {target_bytes} B target")

    report.update(
        rows_in=len(before_lines),
        rows_out=len(after_lines),
        preexisting_shard_rows=len(preexisting_lines),
        preexisting_shards=len(existing_shards),
        rows_appended=len(appended_lines),
        rows_already_present=skipped_already_present,
        preexisting_rows_lost=preexisting_lost,
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
        byte_identical=after_line_counts == expected_line_counts,
    )

    if failures:
        report.update(status="FAILED", failures=failures)
        raise MigrationError(f"{subdir}: equivalence proof FAILED -- " + "; ".join(failures))

    legacy.unlink()
    report.update(status="MIGRATED", legacy_removed=True)
    return report


def main_with_args(argv: list[str] | None = None) -> int:
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
    parser.add_argument(
        "--allow-existing-shards",
        action="store_true",
        help=(
            "migrate even though shards already exist beside the monolith: append only the "
            "monolith rows whose dedup key is not already in the shards, and fail on any "
            "same-key/different-bytes conflict. The documented recovery for a writer landing "
            "mid-cutover (docs/CUTOVER_SHARDING.md)."
        ),
    )
    parser.add_argument("--report", type=Path, default=None)
    parser.add_argument(
        "--target-bytes",
        type=int,
        default=shards.SHARD_TARGET_BYTES,
        help="roll a date's shard over past this size",
    )
    args = parser.parse_args(argv)

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
            # Any shards that already exist come along too. Without them
            # a dry run of the coexistence path would rehearse against an
            # empty shard set -- i.e. prove something other than what the
            # real run is about to do.
            for shard in shards.shard_paths(base_dir, family, args.season):
                destination = shards.shard_dir(workspace, family, args.season) / shard.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(shard, destination)

    reports: list[dict] = []
    failed = False
    try:
        for family in args.families:
            try:
                reports.append(
                    migrate_family(
                        workspace,
                        family,
                        args.season,
                        target_bytes=args.target_bytes,
                        allow_existing_shards=args.allow_existing_shards,
                    )
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


def main() -> int:
    return main_with_args()


if __name__ == "__main__":
    raise SystemExit(main())
