#!/usr/bin/env python3
"""Validate the accounting ledger. THE DESTINATION'S OWN CHECK ON ITS OWN DATA.

    python scripts/validate_accounting_ledger.py --base-dir /path/to/accounting-data
    python scripts/validate_accounting_ledger.py --base-dir . --against origin/accounting-data

*** WHY THIS EXISTS ***
`accounting-data` is an ORPHAN BRANCH. GitHub runs a `pull_request` workflow
only if that workflow file exists on the pull request's BASE branch, and this
branch holds `wagers/`, `settlements/` and a README -- no `.github/`, no
`pyproject.toml`, no `src/`. So a pull request into it gets NO CHECKS AT ALL.

That was invisible while the two backfill pull requests were merged by hand. It
stops being invisible the moment delivery is meant to be hands-off: the
router's auto-merge gate requires a green check, no check ever reports, and
every automatic CFB delivery would wait forever for a signal that cannot
arrive.

So the check runs where the data is, as part of the delivery, from THIS
repository's own code -- which is the same principle as the importer: the
destination validates its own ledger, and the router only reports what it said.

*** WHAT IT CHECKS ***
  1. every line is decodable JSON;
  2. every wager passes `accounting.wager.validate` -- which is what refuses a
     model-provenance field, and refusing those is the whole reason this ledger
     is allowed to exist alongside a research repository;
  3. every settlement passes `accounting.settlement.validate`;
  4. `source_bet_key` is unique within each file -- the dedup key, so a
     duplicate is a wager counted twice in every total;
  5. every settlement's wager is in the same season's wager ledger -- an orphan
     settlement is a payout attributed to a bet nobody recorded;
  6. with `--against`, the diff is APPEND-ONLY. A row is a claim about money
     that already moved; correcting one by editing it in place destroys the
     evidence of what was believed before.

*** WHAT IT PRINTS ***
Counts and failure reasons. Never a ticker, a stake, a price or a payout: this
runs in a public Actions log. A failing row is named by its FILE and LINE.

Exit codes:
    0  every check passed
    1  at least one check failed
    2  the ledger could not be read at all
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cfb_edge_finder.accounting.settlement import validate as validate_settlement  # noqa: E402
from cfb_edge_finder.accounting.store import (  # noqa: E402
    SETTLEMENTS_SUBDIR,
    WAGERS_SUBDIR,
)
from cfb_edge_finder.accounting.wager import validate as validate_wager  # noqa: E402

EXIT_OK = 0
EXIT_INVALID = 1
EXIT_UNREADABLE = 2

_SEASON_FILE = re.compile(r"^(\d{4})\.jsonl$")


def read_lines(path: Path) -> list[tuple[int, dict | None, str | None]]:
    """(line number, row, error) for every non-blank line.

    A line that will not decode is returned as an ERROR rather than skipped.
    Silently ignoring an unreadable ledger line would let a corrupted row read
    as an absent wager, and an absent wager is exactly what invites writing a
    duplicate.
    """
    out: list[tuple[int, dict | None, str | None]] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            out.append((number, None, f"not decodable JSON: {exc}"))
            continue
        if not isinstance(row, dict):
            out.append((number, None, "not a JSON object"))
            continue
        out.append((number, row, None))
    return out


def check_file(path: Path, validator) -> tuple[int, list[str], set[str]]:
    """(rows, problems, source keys) for one ledger file."""
    problems: list[str] = []
    keys: set[str] = set()
    rows = read_lines(path)
    for number, row, error in rows:
        label = f"{path.name}:{number}"
        if error:
            problems.append(f"{label} {error}")
            continue
        issues = validator(row)
        if issues:
            problems.append(f"{label} {'; '.join(issues)}")
        key = row.get("source_bet_key")
        if not isinstance(key, str) or not key.strip():
            problems.append(f"{label} has no source_bet_key")
            continue
        if key in keys:
            # Named by LINE, never by key: the key is the venue's order
            # identity and this log is public.
            problems.append(f"{label} duplicates an earlier row's source_bet_key")
        keys.add(key)
    return len(rows), problems, keys


def git(base_dir: Path, *args: str) -> tuple[int, str]:
    result = subprocess.run(
        ["git", "-C", str(base_dir), *args], capture_output=True, text=True, check=False
    )
    return result.returncode, result.stdout


def append_only_problems(base_dir: Path, against: str) -> list[str]:
    """Rows the diff REMOVES or REWRITES. There must be none.

    The importer appends; it never rewrites or deletes a canonical row. So a
    diff that removes one is not a delivery at all, whatever produced it.
    """
    status, out = git(
        base_dir, "diff", "--unified=0", against, "--", WAGERS_SUBDIR, SETTLEMENTS_SUBDIR
    )
    if status != 0:
        return [
            f"could not diff against {against!r}; the append-only check did not run. "
            "That is a failure, not a pass: an unverifiable diff is the case this check exists for"
        ]
    removed = [
        line
        for line in out.splitlines()
        if line.startswith("-") and not line.startswith("---") and line[1:].strip()
    ]
    if removed:
        return [
            f"the diff REMOVES OR REWRITES {len(removed)} existing ledger row(s); "
            "a wager record is a claim about money that already moved and is never edited"
        ]
    return []


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", required=True, help="a checkout of accounting-data")
    parser.add_argument(
        "--against",
        default=None,
        help=(
            "a git ref to check the diff against. With it, the ledger must be APPEND-ONLY "
            "relative to that ref. Without it, only the rows themselves are checked."
        ),
    )
    parser.add_argument(
        "--result-out",
        default=None,
        help="write the verdict as JSON (counts and reasons; never a row)",
    )
    args = parser.parse_args(argv)

    base = Path(args.base_dir)
    wagers_dir = base / WAGERS_SUBDIR
    settlements_dir = base / SETTLEMENTS_SUBDIR
    if not wagers_dir.is_dir():
        print(f"no {WAGERS_SUBDIR}/ under {base}", file=sys.stderr)
        return EXIT_UNREADABLE

    problems: list[str] = []
    wager_rows = 0
    settlement_rows = 0
    wager_keys_by_season: dict[str, set[str]] = {}

    for path in sorted(wagers_dir.glob("*.jsonl")):
        if not _SEASON_FILE.match(path.name):
            problems.append(f"{path.name} is not a <season>.jsonl ledger file")
            continue
        rows, issues, keys = check_file(path, validate_wager)
        wager_rows += rows
        problems.extend(issues)
        wager_keys_by_season[path.stem] = keys

    if settlements_dir.is_dir():
        for path in sorted(settlements_dir.glob("*.jsonl")):
            if not _SEASON_FILE.match(path.name):
                problems.append(f"{path.name} is not a <season>.jsonl ledger file")
                continue
            rows, issues, keys = check_file(path, validate_settlement)
            settlement_rows += rows
            problems.extend(issues)
            known = wager_keys_by_season.get(path.stem, set())
            orphans = keys - known
            if orphans:
                problems.append(
                    f"{path.name}: {len(orphans)} settlement(s) refer to a wager the "
                    f"{path.stem} ledger has no record of; a payout attributed to a bet "
                    "nobody recorded would count in every total while belonging to nothing"
                )

    if args.against:
        problems.extend(append_only_problems(base, args.against))

    print(f"wager rows:      {wager_rows}")
    print(f"settlement rows: {settlement_rows}")
    print(f"problems:        {len(problems)}")
    for problem in problems[:50]:
        print(f"  {problem}")
    if len(problems) > 50:
        print(f"  ... and {len(problems) - 50} more")

    if args.result_out:
        with open(args.result_out, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "passed": not problems,
                    "wager_rows": wager_rows,
                    "settlement_rows": settlement_rows,
                    "problems": problems,
                    "append_only_checked": bool(args.against),
                },
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")

    return EXIT_INVALID if problems else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
