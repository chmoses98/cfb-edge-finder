#!/usr/bin/env python3
"""How did we do. Read from the canonical ledger; no screenshot, no retyping.

    python scripts/cfb_postmortem.py --base-dir /path/to/accounting-data --season 2026
    python scripts/cfb_postmortem.py --base-dir . --season 2026 \
        --candidates data/execution/latest/candidates --unit 70

*** WHERE THE MONEY COMES FROM ***
`wagers/<season>.jsonl` and `settlements/<season>.jsonl`, and nowhere else.
Those rows came from the venue's own fill evidence through the destination's
own importer, which is the only thing in this system that knows what actually
happened.

*** WHAT THE CANDIDATE ARTIFACTS ADD ***
Cuts, and only cuts. Pointing `--candidates` at the artifacts produced for a
slate lets the same realised profit and loss be read by robustness tier, by
handicap confidence, by factual data quality, by recommended edge bucket, and
by whether the fill respected the price the analysis said to stop at. Not one
monetary figure changes: run it with and without, and every total is the same
number. `tests/test_postmortem.py` asserts exactly that.

*** WHAT IS PRINTED ***
This is the OWNER'S report about the owner's money, so unlike the delivery
workflows it prints tickers, stakes and payouts -- that is the whole point of
it. It is therefore a LOCAL command and is not run by any workflow that writes
to a public log. `--json` writes the same content to a file for a tool to
read.

Exit codes:
    0  a report was produced (FINAL or PARTIAL; both are real answers)
    2  the ledger could not be read
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "src"))

from cfb_edge_finder.accounting import postmortem as postmortem_module  # noqa: E402
from cfb_edge_finder.accounting.recommendation_link import (  # noqa: E402
    match_executions,
    recommendations_from_artifact,
)
from cfb_edge_finder.accounting.store import (  # noqa: E402
    ledger_path,
    read_rows,
    settlement_ledger_path,
)

EXIT_OK = 0
EXIT_UNREADABLE = 2


def load_recommendations(candidates_dir: Path | None) -> list:
    """Every candidate artifact in a directory, as recommendation records.

    A directory that does not exist is an EMPTY set, not an error. The
    postmortem's money does not depend on it, so a missing artifact costs the
    attribution cuts and nothing else -- and the report says which cuts it
    could not make rather than quietly showing fewer rows.
    """
    if candidates_dir is None or not candidates_dir.exists():
        return []
    out = []
    for path in sorted(candidates_dir.glob("*.candidates.json")):
        try:
            artifact = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            print(f"  (skipping unreadable artifact {path.name})", file=sys.stderr)
            continue
        out.extend(recommendations_from_artifact(artifact))
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-dir", required=True, help="a checkout of accounting-data")
    parser.add_argument("--season", required=True, type=int)
    parser.add_argument(
        "--candidates",
        default=None,
        help=(
            "a directory of <batch>.candidates.json artifacts. Adds ATTRIBUTION CUTS only; "
            "no monetary figure in the report depends on it."
        ),
    )
    parser.add_argument(
        "--unit",
        type=float,
        default=None,
        help=(
            "the operator's unit, for a unit result. Absent, no unit figure is stated: a unit "
            "inferred from the average stake would be a number that never existed."
        ),
    )
    parser.add_argument("--json", default=None, help="also write the report as JSON")
    args = parser.parse_args(argv)

    base = Path(args.base_dir)
    wagers_path = ledger_path(base, args.season)
    if not wagers_path.exists():
        print(f"no wager ledger at {wagers_path}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        wagers = read_rows(wagers_path)
        settlements = read_rows(settlement_ledger_path(base, args.season))
    except ValueError as exc:
        # An unreadable ledger LINE is fatal here, deliberately: a report that
        # silently skipped a corrupt row would understate the season by
        # exactly that row and look complete doing it.
        print(f"the ledger could not be read: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    recommendations = load_recommendations(
        Path(args.candidates) if args.candidates else None
    )
    matches = match_executions(wagers, recommendations) if recommendations else None

    report = postmortem_module.build(
        wagers,
        settlements,
        args.season,
        matches=matches,
        recommendations=recommendations,
        unit=args.unit,
    )
    print(postmortem_module.render(report))

    if not recommendations:
        print(
            "\n  (no candidate artifacts supplied, so the robustness, confidence, "
            "data-quality and edge-bucket cuts are all `not_recommended`. Every monetary "
            "figure above is unaffected.)"
        )

    if args.json:
        path = Path(args.json)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(report.as_dict(), indent=1, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )
        print(f"\n  written to {path}")
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
