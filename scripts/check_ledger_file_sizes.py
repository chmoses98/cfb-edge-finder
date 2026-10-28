"""Refuse to commit a ledger tree that holds a file the git host would reject.

    python scripts/check_ledger_file_sizes.py <dir> [<dir> ...]

GitHub refuses any push containing a file of 100 MiB or more, and a refused push of the `script-ledger` branch
loses every prospective row written by that run (rows cannot be re-recorded after kickoff). The ledger streams
are written as segments of at most `SEGMENT_MAX_BYTES` (50 MB), so a file near the limit means a writer bypassed
the segmented helpers. This check fails the job BEFORE the commit, naming the file, instead of letting the
push fail afterwards. The one historical first segment written before segmentation (96.5 MiB) is within the
limit and never grows; so is the 2026 `realized.jsonl` (72.9 MiB when segmentation reached it).
"""

from __future__ import annotations

import sys
from pathlib import Path

HOST_LIMIT_BYTES = 100 * 1024 * 1024  # GitHub's per-file push limit
GUARD_BYTES = 99 * 1024 * 1024  # fail with a margin below it
WARN_BYTES = 60 * 1024 * 1024  # larger than a segment can be: a writer that is not segmenting


def oversized(roots: list[Path]) -> tuple[list[tuple[Path, int]], list[tuple[Path, int]]]:
    bad, warn = [], []
    for root in roots:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            size = path.stat().st_size
            if size >= GUARD_BYTES:
                bad.append((path, size))
            elif size >= WARN_BYTES:
                warn.append((path, size))
    return bad, warn


def main(argv: list[str]) -> int:
    roots = [Path(a) for a in argv] or [Path("data/scripting")]
    bad, warn = oversized([r for r in roots if r.exists()])
    for path, size in warn:
        print(f"::warning::{path} is {size / 2**20:.1f} MiB, more than one 50 MB segment: only a stream's first file "
              f"written before segmentation may be this large, and it must not grow")
    for path, size in bad:
        limit = HOST_LIMIT_BYTES / 2**20
        print(f"::error::{path} is {size / 2**20:.1f} MiB; the git host refuses files of {limit:.0f} MiB, so this "
              f"ledger commit would be rejected. Append through ledger.append_stream_line.")
    if bad:
        return 1
    print(f"ledger file sizes OK ({len(warn)} file(s) above {WARN_BYTES / 2**20:.0f} MiB, none near the limit)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
