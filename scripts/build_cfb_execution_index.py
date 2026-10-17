#!/usr/bin/env python3
"""Build and/or verify the RUN CFB execution index.

Derives data/live/cfb_execution_index.json and data/live/execution/** from
the catalog and game files already on disk -- no network, no credentials.
Every produced file is kept far below the 1 MB size at which GitHub's
Contents API (ChatGPT's GitHub connector) returns empty content.

    python scripts/build_cfb_execution_index.py --out-dir data/live            # build + verify
    python scripts/build_cfb_execution_index.py --out-dir data/live --verify-only

Exit codes: 0 built and verified; 1 build failed; 3 verification failed.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from cfb_edge_finder.catalog.execution import (
    ExecutionBuildError,
    verify_execution_artifacts,
    write_execution_artifacts,
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out-dir", default="data/live", help="directory holding cfb_market_catalog.json")
    parser.add_argument(
        "--prefix",
        default=None,
        help="repository path of --out-dir used in published pointers (default: --out-dir as given)",
    )
    parser.add_argument("--verify-only", action="store_true", help="verify what is on disk; write nothing")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    prefix = (args.prefix or args.out_dir).strip("/").removeprefix("./")

    if not args.verify_only:
        try:
            result = write_execution_artifacts(out_dir, prefix=prefix)
        except ExecutionBuildError as exc:
            print(f"::error title=Execution index build failed::{exc}")
            print(f"FATAL: execution index build failed: {exc}", file=sys.stderr)
            return 1
        print(
            f"execution index: {result.games} games / {result.markets} contracts across "
            f"{len(result.slates)} slates ({result.files_written} files written, "
            f"{result.files_unchanged} unchanged, {result.files_pruned} pruned); "
            f"largest file {result.largest_file[0]} = {result.largest_file[1]:,} bytes"
        )

    problems = verify_execution_artifacts(out_dir, prefix=prefix)
    if problems:
        for problem in problems[:50]:
            print(f"::error title=Execution index verification failed::{problem}")
        print(f"FATAL: {len(problems)} execution index problem(s)", file=sys.stderr)
        return 3
    print("execution index VERIFIED: every pointer resolves, every file is under the size limit, "
          "every contract in every game file is present in its quotes and detail shards, "
          "and all fingerprints agree")
    return 0


if __name__ == "__main__":
    sys.exit(main())
