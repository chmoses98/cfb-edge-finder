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

from cfb_edge_finder.accounting import decision_attribution  # noqa: E402
from cfb_edge_finder.accounting import postmortem as postmortem_module  # noqa: E402
from cfb_edge_finder.accounting.recommendation_link import (  # noqa: E402
    match_executions,
    recommendations_from_artifact,
)
from cfb_edge_finder.accounting.store import (  # noqa: E402
    amendments_ledger_path,
    ledger_path,
    read_rows,
    settlement_ledger_path,
)
from cfb_edge_finder.decisions import (  # noqa: E402
    ENV_STORE,
    DecisionStore,
    DecisionStoreUnavailable,
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
    parser.add_argument(
        "--decisions",
        default=None,
        metavar="DIR",
        help=(
            "the PRIVATE decision store (default: $CFB_DECISION_STORE when set). Its records "
            "supply the attribution: each wager is matched to the decision candidate that "
            "preceded it (exact market and side, nearest earlier run), and the report shows the "
            "thesis, bet-up-to, decision-time price and alternatives beside the fill. No "
            "monetary figure depends on it. Takes precedence over --candidates for the tier cuts."
        ),
    )
    parser.add_argument(
        "--game-date",
        action="append",
        default=None,
        metavar="YYYY-MM-DD",
        help=(
            "report only wagers whose contest date is one of these (repeatable). A slate "
            "postmortem is one day's card, and the whole-season file cannot answer for it. "
            "Selection is by the row's own game_date, never by execution time: a Friday-night "
            "game bet at 02:24Z belongs to Friday."
        ),
    )
    args = parser.parse_args(argv)

    base = Path(args.base_dir)
    wagers_path = ledger_path(base, args.season)
    if not wagers_path.exists():
        print(f"no wager ledger at {wagers_path}", file=sys.stderr)
        return EXIT_UNREADABLE

    try:
        wagers = read_rows(wagers_path)
        settlements = read_rows(settlement_ledger_path(base, args.season))
        # The ledger's own corrections. Read from the same checkout, applied
        # inside `build`, and reported beside the as-filed total.
        amendments = read_rows(amendments_ledger_path(base, args.season))
    except ValueError as exc:
        # An unreadable ledger LINE is fatal here, deliberately: a report that
        # silently skipped a corrupt row would understate the season by
        # exactly that row and look complete doing it.
        print(f"the ledger could not be read: {exc}", file=sys.stderr)
        return EXIT_UNREADABLE

    if args.game_date:
        # Wagers are selected; settlements are joined by key inside `build`, so
        # a settlement for a wager outside the slate cannot contribute money.
        wanted = set(args.game_date)
        total = len(wagers)
        wagers = [w for w in wagers if w.get("game_date") in wanted]
        print(
            f"  slate filter: game_date in {sorted(wanted)} -- "
            f"{len(wagers)} of {total} wagers in the ledger"
        )
        if not wagers:
            # Not an error: a date with no wagers is a fact about the card.
            # Printed rather than rendered as a report of zeros, because zero
            # wagers is not a result with an ROI.
            print("  no wagers in the ledger carry that contest date; nothing to report.")
            return EXIT_OK

    recommendations = load_recommendations(
        Path(args.candidates) if args.candidates else None
    )
    matches = match_executions(wagers, recommendations) if recommendations else None

    attribution = None
    decisions_root = args.decisions or os.environ.get(ENV_STORE)
    if decisions_root:
        try:
            decision_store = DecisionStore.resolve(decisions_root)
        except DecisionStoreUnavailable as exc:
            # A store that cannot be read is not "no records". Refusing is the
            # only answer that does not print an unattributed report as if it
            # were the attributed one.
            print(f"the decision store could not be used: {exc}", file=sys.stderr)
            return EXIT_UNREADABLE
        records = list(decision_store.records())
        attribution = decision_attribution.attribute(wagers, records)
        # The decision records ARE the recommendations for the tier cuts. They
        # carry the same fields the candidate artifacts did, plus the run that
        # produced them, and one identity rule decides both views.
        recommendations = [
            c.as_recommendation() for c in decision_attribution.candidates_from_records(records)
        ]
        matches = attribution.matches
        for problem in decision_store.problems()[:20]:
            print(f"  (decision store: {problem})", file=sys.stderr)

    report = postmortem_module.build(
        wagers,
        settlements,
        args.season,
        matches=matches,
        recommendations=recommendations,
        unit=args.unit,
        amendments=amendments,
        decision_attribution=attribution.as_dict() if attribution else None,
    )
    print(postmortem_module.render(report))
    if attribution is not None:
        print("\n".join(decision_attribution.render(attribution)))

    if not recommendations:
        print(
            "\n  (no candidate artifacts supplied and no decision records read, so the "
            "robustness, confidence, data-quality and edge-bucket cuts are all "
            "`not_recommended`. Every monetary figure above is unaffected.)"
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
