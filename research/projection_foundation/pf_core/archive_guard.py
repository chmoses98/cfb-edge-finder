"""Append-only guard for the `projection-data` archive.

Run inside the checked-out archive before committing:  python -m pf_core.archive_guard <archive_dir> [<base_ref>]
Fails (exit 1) if any file under an immutable directory was modified, deleted or renamed. Only additions are allowed
there and for dated scorecard evaluations; the current views (scorecards/<sport>/<tag>.json|.md, projections/) may be
rewritten.

Without a base ref only uncommitted changes are checked. With one (e.g. the projection-data commit the run started
from), committed changes since that ref are checked too, so a commit made earlier in the same run cannot hide an edit.
"""
from __future__ import annotations

import re
import subprocess
import sys

IMMUTABLE = ("forecasts/", "outcomes/", "settlements/", "corrections/", "availability/")
# Dated scorecard evaluations are history too (`<tag>_<YYYYMMDD>[...].json`); only `<tag>.json`/`.md` are rewritable views.
_DATED_SCORECARD = re.compile(r"^scorecards/[^/]+/[A-Za-z0-9-]+_\d{8}(_[^/]*)?\.json$")


def is_immutable(path: str) -> bool:
    return path.startswith(IMMUTABLE) or bool(_DATED_SCORECARD.match(path))


def violations(archive_dir: str) -> list[str]:
    out = subprocess.check_output(["git", "-C", archive_dir, "status", "--porcelain", "--untracked-files=all"], text=True)
    bad = []
    for line in out.splitlines():
        code, path = line[:2], line[3:]
        path = path.split(" -> ")[-1]
        if is_immutable(path) and code.strip() not in ("??", "A"):
            bad.append(f"{code.strip()} {path}")
    return bad


def violations_since(archive_dir: str, base_ref: str) -> list[str]:
    """Immutable files modified, deleted or renamed between `base_ref` and HEAD (committed history)."""
    out = subprocess.check_output(["git", "-C", archive_dir, "diff", "--name-status", "--no-renames", f"{base_ref}..HEAD"],
                                  text=True)
    bad = []
    for line in out.splitlines():
        code, _, path = line.partition("\t")
        if is_immutable(path) and code.strip() != "A":
            bad.append(f"{code.strip()} {path} (since {base_ref})")
    return bad


def main(argv=None) -> int:
    args = argv if argv is not None else sys.argv[1:]
    d = args[0] if args else "."
    bad = violations(d)
    if len(args) > 1 and args[1]:
        bad += violations_since(d, args[1])
    if bad:
        print("archive_guard: immutable archive files changed (only additions are allowed):")
        print("\n".join("  " + b for b in bad))
        return 1
    print("archive_guard: OK (additions only)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
