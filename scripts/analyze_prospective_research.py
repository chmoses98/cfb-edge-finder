#!/usr/bin/env python3
"""Research analytics over the settled prospective corpus.

    python scripts/analyze_prospective_research.py --season 2026
    python scripts/analyze_prospective_research.py --season 2026 --family spread --timing T_24H
    python scripts/analyze_prospective_research.py --season 2026 --side no --min-sample 30

*** RESEARCH-ONLY, DESCRIPTIVE ONLY ***
Reports what the captured data says, sliced several ways, with sample
sizes and cluster-aware uncertainty attached. It produces no bet
recommendation, no qualification tier, no stake, and no threshold. It
cannot: nothing in `cfb_edge_finder.analytics` returns a "best" anything,
and tests/test_analytics_safety.py fails if such a surface appears.

*** READ-ONLY OVER IMMUTABLE LEDGERS ***
Reads the observation and attribution ledgers and never writes to either.
Analytics artifacts are written to a separate `analytics/` path, so a
regenerated report can never disturb the research record it describes.

Every filter is a flag. Changing what is analyzed never requires a code
edit (mission section 23); the default run produces the canonical full
report.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.analytics.dataset import build_dataset  # noqa: E402
from cfb_edge_finder.analytics.metrics import ANALYTICS_CODE_VERSION  # noqa: E402
from cfb_edge_finder.analytics.report import (  # noqa: E402
    build_report,
    render_markdown,
    report_to_dict,
    slices_to_csv_rows,
)
from cfb_edge_finder.analytics.uncertainty import (  # noqa: E402
    CAUTION_SAMPLE_THRESHOLD,
    LOW_SAMPLE_THRESHOLD,
)
from cfb_edge_finder.research import persistence  # noqa: E402

ANALYTICS_SUBDIR = "analytics"


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--season", type=int, default=2026)
    p.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    p.add_argument("--family", action="append", choices=["moneyline", "spread", "total"],
                   help="Restrict to one or more market families (repeatable). Default: all.")
    p.add_argument("--timing", action="append", help="Restrict to one or more timing labels (repeatable).")
    p.add_argument("--model-version", action="append", help="Restrict to one or more model versions (repeatable).")
    p.add_argument("--side", choices=["yes", "no"], default="yes",
                   help="Which executable side to analyze. YES and NO are independent quotes and are never mixed.")
    p.add_argument("--min-sample", type=int, default=LOW_SAMPLE_THRESHOLD,
                   help=f"Rows below this are labelled LOW_SAMPLE (default {LOW_SAMPLE_THRESHOLD}); "
                        f"below {CAUTION_SAMPLE_THRESHOLD} are labelled CAUTION. Never suppresses data.")
    p.add_argument("--captured-from", help="ISO date/datetime lower bound on captured_at (inclusive).")
    p.add_argument("--captured-to", help="ISO date/datetime upper bound on captured_at (exclusive).")
    p.add_argument("--out-dir", type=Path, default=None,
                   help="Directory for artifacts. Default: <data-repo-dir>/data/research/analytics/<season>/")
    p.add_argument("--no-write", action="store_true", help="Print the report but write no artifacts.")
    p.add_argument("--quiet", action="store_true", help="Suppress the Markdown summary on stdout.")
    return p.parse_args()


def _apply_filters(dataset, args) -> tuple[list, dict]:
    rows = dataset.rows
    applied: dict[str, object] = {"side": args.side, "min_sample": args.min_sample}

    if args.family:
        rows = [r for r in rows if r.family in set(args.family)]
        applied["family"] = sorted(set(args.family))
    if args.timing:
        rows = [r for r in rows if r.timing_label in set(args.timing)]
        applied["timing"] = sorted(set(args.timing))
    if args.model_version:
        rows = [r for r in rows if r.model_version in set(args.model_version)]
        applied["model_version"] = sorted(set(args.model_version))
    if args.captured_from:
        rows = [r for r in rows if r.captured_at >= args.captured_from]
        applied["captured_from"] = args.captured_from
    if args.captured_to:
        rows = [r for r in rows if r.captured_at < args.captured_to]
        applied["captured_to"] = args.captured_to
    return rows, applied


def main() -> int:
    args = _parse_args()
    started = time.perf_counter()

    base = args.data_repo_dir / "data" / "research"
    obs_path = persistence.canonical_path(base, persistence.OBSERVATIONS_SUBDIR, args.season)
    attr_path = persistence.canonical_path(base, persistence.ATTRIBUTIONS_SUBDIR, args.season)

    dataset = build_dataset(obs_path, attr_path)
    filtered_rows, applied_filters = _apply_filters(dataset, args)
    dataset.rows = filtered_rows

    report = build_report(dataset, filters=applied_filters, side=args.side)
    payload = report_to_dict(report)
    payload["source"] = {
        "observations_path": str(obs_path),
        "attributions_path": str(attr_path),
        "season": args.season,
        "generated_at": datetime.now(UTC).isoformat(),
        "analytics_code_version": ANALYTICS_CODE_VERSION,
        "runtime_seconds": round(time.perf_counter() - started, 4),
    }

    if not args.quiet:
        print(render_markdown(report))

    if not args.no_write:
        out_dir = args.out_dir or (base / ANALYTICS_SUBDIR / str(args.season))
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "analytics_summary.json").write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str), encoding="utf-8"
        )
        (out_dir / "analytics_report.md").write_text(render_markdown(report), encoding="utf-8")
        csv_rows = slices_to_csv_rows(report)
        if csv_rows:
            with (out_dir / "analytics_slices.csv").open("w", encoding="utf-8", newline="") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(csv_rows[0].keys()))
                writer.writeheader()
                writer.writerows(csv_rows)
        print(f"\nArtifacts written to {out_dir}")

    print(
        "\nSTATUS: RESEARCH-ONLY analytics. Descriptive measurement only -- no bet recommendation, "
        "qualification tier, stake, or threshold anywhere in this output."
    )
    # A fatal data-integrity condition fails the run loudly; an empty
    # settled sample does NOT -- that is a legitimate corpus state, and
    # exiting non-zero for it would train everyone to ignore the exit code.
    return 1 if dataset.health.has_fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
