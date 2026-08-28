#!/usr/bin/env python3
"""Render the Research Decision Report from the observation ledger.

Read-only. Loads the corpus, runs the shadow decision pipeline, and
writes a diagnostic record of where every candidate stopped.

This produces no ranking, no sizing, and nothing actionable -- see
`decision/report.py`, which refuses to render text containing betting-card
framing.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.decision.artifact import load_artifact  # noqa: E402
from cfb_edge_finder.decision.portfolio import build_portfolio_view  # noqa: E402
from cfb_edge_finder.decision.report import (  # noqa: E402
    CorpusSummary,
    render_report,
    report_payload,
)
from cfb_edge_finder.decision.shadow import run_shadow_pipeline  # noqa: E402
from cfb_edge_finder.expression.corpus import load_contract_snapshots  # noqa: E402


def summarize_corpus(path: Path) -> CorpusSummary:
    """Counts straight off the ledger. Rows are read once."""
    total = prospective = non_prospective = 0
    versions: Counter[str] = Counter()
    if not path.exists():
        return CorpusSummary(corpus_identifier=str(path))
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                continue
            total += 1
            versions[str(row.get("schema_version"))] += 1
            if row.get("capture_mode") == "PROSPECTIVE":
                prospective += 1
            else:
                non_prospective += 1
    return CorpusSummary(
        total_rows=total,
        prospective_rows=prospective,
        non_prospective_rows=non_prospective,
        settled_games=0,
        schema_versions=dict(versions),
        corpus_identifier=str(path),
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-repo-dir", type=Path, default=REPO_ROOT)
    parser.add_argument("--season", type=int, default=2026)
    parser.add_argument(
        "--threshold-artifact",
        type=Path,
        default=None,
        help="Path to a shadow threshold artifact. Absent by design; supplying a "
        "path does not approve it -- an unapproved artifact is still refused.",
    )
    parser.add_argument("--settled-games", type=int, default=0)
    parser.add_argument("--json-out", type=Path, default=None)
    parser.add_argument("--now", type=str, default=None)
    args = parser.parse_args()

    now = datetime.fromisoformat(args.now) if args.now else datetime.now(UTC)
    observations = (
        args.data_repo_dir / "data" / "research" / "observations" / f"{args.season}.jsonl"
    )

    corpus = summarize_corpus(observations)
    corpus = CorpusSummary(
        total_rows=corpus.total_rows,
        prospective_rows=corpus.prospective_rows,
        non_prospective_rows=corpus.non_prospective_rows,
        settled_games=args.settled_games,
        schema_versions=corpus.schema_versions,
        corpus_identifier=corpus.corpus_identifier,
    )

    load = load_contract_snapshots(observations)
    resolution = load_artifact(args.threshold_artifact)
    run = run_shadow_pipeline(
        load.snapshots,
        resolution=resolution,
        available_settled_games=args.settled_games,
        now=now,
    )
    portfolio = build_portfolio_view([s.semantics for s in load.snapshots])
    evidence_state = run.decisions[0].evidence_state if run.decisions else "NO_CANDIDATES"

    text = render_report(
        run,
        portfolio=portfolio,
        evidence_state=evidence_state,
        corpus=corpus,
        generated_at=now,
    )
    print(text, end="")

    if args.json_out:
        payload = report_payload(
            run,
            portfolio=portfolio,
            evidence_state=evidence_state,
            corpus=corpus,
            generated_at=now,
        )
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
        print(f"wrote {args.json_out}")

    # A non-zero shadow-qualified count means a lock opened. Exit non-zero
    # so a scheduled run cannot pass silently.
    return 1 if run.shadow_qualified_count else 0


if __name__ == "__main__":
    raise SystemExit(main())
