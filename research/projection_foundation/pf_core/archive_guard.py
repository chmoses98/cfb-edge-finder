"""Append-only guard for the `projection-data` archive.

Run inside the checked-out archive before committing:  python -m pf_core.archive_guard <archive_dir>
Fails (exit 1) if any file under an immutable directory was modified, deleted or renamed. Only additions are allowed
there; scorecards/ and projections/ (the "latest" views) may be rewritten.
"""
from __future__ import annotations

import subprocess
import sys

IMMUTABLE = ("forecasts/", "outcomes/", "settlements/", "corrections/", "availability/")


def violations(archive_dir: str) -> list[str]:
    out = subprocess.check_output(["git", "-C", archive_dir, "status", "--porcelain", "--untracked-files=all"], text=True)
    bad = []
    for line in out.splitlines():
        code, path = line[:2], line[3:]
        path = path.split(" -> ")[-1]
        if any(path.startswith(p) for p in IMMUTABLE) and code.strip() not in ("??", "A"):
            bad.append(f"{code.strip()} {path}")
    return bad


def main(argv=None) -> int:
    d = (argv or sys.argv[1:] or ["."])[0]
    bad = violations(d)
    if bad:
        print("archive_guard: immutable archive files changed (only additions are allowed):")
        print("\n".join("  " + b for b in bad))
        return 1
    print("archive_guard: OK (additions only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
