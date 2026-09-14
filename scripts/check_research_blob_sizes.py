#!/usr/bin/env python3
"""Fail loudly on any durable-store blob approaching GitHub's size limit.

    python scripts/check_research_blob_sizes.py --data-repo-dir <checkout>

The automated half of the post-GH001 size guard. `research/shards.py`
rolls a shard over at `SHARD_TARGET_BYTES` on the write path and
`research/git_durable_store.py` refuses a push above
`PUSH_REFUSAL_BYTES`; this is the check that can be pointed at a corpus
on demand or from CI, so an artifact that is NOT sharded (the
football-state document, a v2 parquet) cannot drift toward the limit
unnoticed between pushes.

Two thresholds, on purpose:

  --target (default 45 MB)  every sharded JSONL file is expected to stay
                            under this; exceeding it is a WARNING by
                            default because it means a rollover did not
                            happen or an unsharded artifact is growing.
  --limit  (default 90 MB)  exceeding this FAILS: GitHub rejects at
                            100 MiB with GH001 and the whole branch push
                            goes with it.

`--strict` promotes target warnings to failures, which is what a CI job
guarding the storage layout wants.

`--allow-legacy-monolith` reports a PRE-SHARDING `{season}.jsonl` as
MIGRATION_PENDING rather than a failure. That distinction matters: an
unmigrated corpus is a known condition with a named remedy
(scripts/migrate_research_shards.py), and failing on it would make CI
red on every pull request until the migration ran -- including the pull
request that introduces the migration. It retires itself: once the
corpus is sharded no legacy monolith exists, and any oversize blob after
that is a genuine regression in the write path that fails normally.

The BLOCKING guard against a GH001 outage is not this script -- it is
`research/git_durable_store.py`, which refuses to push a changed blob
above the hard limit. This is the early-warning report for artifacts
that are NOT sharded and could drift between pushes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.research import shards  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--data-repo-dir", type=Path, required=True)
    parser.add_argument("--target", type=int, default=shards.SHARD_TARGET_BYTES)
    parser.add_argument("--limit", type=int, default=shards.PUSH_REFUSAL_BYTES)
    parser.add_argument("--strict", action="store_true", help="treat target warnings as failures")
    parser.add_argument(
        "--allow-legacy-monolith",
        action="store_true",
        help="report an unmigrated {season}.jsonl as MIGRATION_PENDING rather than a failure",
    )
    parser.add_argument("--json", type=Path, default=None)
    args = parser.parse_args()

    root = args.data_repo_dir / "data" / "research"
    if not root.is_dir():
        print(f"(no corpus at {root}; nothing to check)")
        return 0

    over_limit = shards.oversize_blobs(root, limit_bytes=args.limit)
    over_target = shards.oversize_blobs(root, limit_bytes=args.target)
    files = [p for p in root.rglob("*") if p.is_file()]
    largest = max((p.stat().st_size for p in files), default=0)

    def pending(path: Path) -> bool:
        return args.allow_legacy_monolith and shards.is_legacy_monolith(path, root)

    blocking_limit = [(p, s) for p, s in over_limit if not pending(p)]
    blocking_target = [(p, s) for p, s in over_target if not pending(p)]
    unmigrated = [(p, s) for p, s in over_target if pending(p)]

    print(f"corpus root        : {root}")
    print(f"files scanned      : {len(files)}")
    print(f"largest blob       : {largest / 1_000_000:.2f} MB")
    print(f"target  (<= {args.target / 1_000_000:>5.0f} MB): {len(over_target)} over")
    print(f"hard    (<= {args.limit / 1_000_000:>5.0f} MB): {len(over_limit)} over")

    for path, size in over_target:
        if pending(path):
            severity = "MIGRATION_PENDING"
        elif (path, size) in over_limit or args.strict:
            severity = "FAIL"
        else:
            severity = "WARN"
        print(f"  [{severity}] {path.relative_to(root)} is {size / 1_000_000:.2f} MB")

    if unmigrated:
        print(
            "\nNOTE: the file(s) above are PRE-SHARDING season monoliths. Not a regression -- "
            "run `python scripts/migrate_research_shards.py --data-repo-dir <checkout> "
            "--season <season>` to relocate them into UTC-date shards. Until then the durable "
            "store will refuse to push them (research/git_durable_store.py), which is what "
            "GitHub was already doing with GH001."
        )

    if args.json is not None:
        args.json.write_text(
            json.dumps(
                {
                    "root": str(root),
                    "files_scanned": len(files),
                    "largest_blob_bytes": largest,
                    "target_bytes": args.target,
                    "limit_bytes": args.limit,
                    "over_target": [[str(p), s] for p, s in over_target],
                    "over_limit": [[str(p), s] for p, s in over_limit],
                    "migration_pending": [[str(p), s] for p, s in unmigrated],
                },
                indent=2,
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )

    if blocking_limit:
        print(
            "\nFAIL: a blob is above the durable-store hard limit; a push carrying it "
            "would be rejected by GitHub with GH001.",
            file=sys.stderr,
        )
        return 1
    if blocking_target and args.strict:
        print("\nFAIL (--strict): a blob is above the shard target size.", file=sys.stderr)
        return 1
    if unmigrated:
        print(f"\nOK with {len(unmigrated)} migration(s) pending; no shard is over its limit.")
        return 0
    print("\nOK: every durable-store blob is within the configured limits.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
