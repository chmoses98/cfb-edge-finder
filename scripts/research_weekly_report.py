#!/usr/bin/env python3
"""Milestone E, Part I: builds and durably persists one weekly research
report. Research-only -- no recommendation/stake output.

    python scripts/research_weekly_report.py --season 2026 --week-label wk01 \\
        --data-repo-dir /path/to/checkout
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.research import git_durable_store, persistence, reporting  # noqa: E402


def _apply_report(repo_dir: Path, *, season: int, week_label: str, now: datetime) -> persistence.AppendResult:
    base_dir = repo_dir / "data" / "research"
    obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, season)
    settle_path = persistence.canonical_path(base_dir, persistence.SETTLEMENTS_SUBDIR, season)

    rows = persistence.read_observation_rows(obs_path)
    week_rows = [r for r in rows if r.observation.game_id is not None and f"-{week_label}-" in r.observation.game_id]
    settlement_rows = persistence.read_settlement_rows(settle_path) if settle_path.exists() else []

    report = reporting.build_weekly_report(
        season=season, week_label=week_label, rows=week_rows, settlement_rows=settlement_rows, generated_at=now
    )
    reports_dir = base_dir / "reports" / "weekly"
    reports_dir.mkdir(parents=True, exist_ok=True)
    out_path = reports_dir / f"{season}-{week_label}.json"
    out_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")
    return persistence.AppendResult(written=1, skipped_duplicate=0, keys_written=(str(out_path),))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week-label", required=True)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-branch", default="research-data")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    now = datetime.now(UTC)
    if not args.no_push:
        git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return _apply_report(repo_dir, season=args.season, week_label=args.week_label, now=now)

    if args.no_push:
        apply_fn(args.data_repo_dir)
    else:
        git_durable_store.commit_and_push_with_retry(
            args.data_repo_dir, args.data_branch, apply_fn,
            commit_message=f"research weekly report: {args.season}-{args.week_label} at={now.isoformat()}",
        )
    print(f"Weekly report written for season={args.season} week={args.week_label}.")
    print("\nSTATUS: RESEARCH-ONLY report. No bet recommendation or stake sizing anywhere in this output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
