"""Second archive guard, for after a `git rebase` onto a newer remote tip (stdlib only).

    python -m cfb.live.archive_check <archive_dir> <base_ref>

pf_core.archive_guard inspects the working tree (uncommitted changes). After a rebase everything is committed, so this
compares HEAD with the base the rebase was made onto: any modified, deleted or renamed file under an immutable directory
fails the push. Only additions are allowed there; the rewritable views (projections/, summary/, ratings/, scorecards/) may change.
"""
from __future__ import annotations

import subprocess
import sys

from pf_core.archive_guard import is_immutable


def violations(archive_dir: str, base: str) -> list[str]:
    out = subprocess.check_output(["git", "-C", archive_dir, "diff", "--name-status", base, "HEAD"], text=True)
    bad = []
    for line in out.splitlines():
        parts = line.split("\t")
        code, paths = parts[0], parts[1:]
        if code.startswith("A"):
            continue
        if any(is_immutable(p) for p in paths):
            bad.append(f"{code} {' -> '.join(paths)}")
    return bad


def main(argv=None) -> int:
    d, base = (argv or sys.argv[1:])[:2]
    bad = violations(d, base)
    if bad:
        print("archive_check: immutable archive files differ from the remote base (only additions are allowed):")
        print("\n".join("  " + b for b in bad))
        return 1
    print("archive_check: OK (additions only relative to", base + ")")
    return 0


if __name__ == "__main__":
    sys.exit(main())
