#!/usr/bin/env python3
"""Milestone E, Part I: builds and durably persists the season cumulative
report. Versioned (`report_version` increments each run) -- prior
versions are never overwritten, only superseded (see docs/MILESTONE_E.md
"Reports").

    python scripts/research_season_report.py --season 2026 --data-repo-dir /path/to/checkout
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.research import git_durable_store, persistence, reporting  # noqa: E402


def _next_report_version(reports_dir: Path, season: int) -> int:
    existing = sorted(reports_dir.glob(f"{season}-v*.json"))
    if not existing:
        return 1
    versions = [int(p.stem.rsplit("-v", 1)[1]) for p in existing]
    return max(versions) + 1


def _apply_report(repo_dir: Path, *, season: int, now: datetime) -> persistence.AppendResult:
    base_dir = repo_dir / "data" / "research"
    obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, season)
    settle_path = persistence.canonical_path(base_dir, persistence.SETTLEMENTS_SUBDIR, season)

    rows = persistence.read_observation_rows(obs_path) if obs_path.exists() else []
    settlement_rows = persistence.read_settlement_rows(settle_path) if settle_path.exists() else []
    weeks = sorted(
        {
            r.observation.game_id.split("-")[2]
            for r in rows
            if r.observation.game_id and len(r.observation.game_id.split("-")) > 2
        }
    )

    reports_dir = base_dir / "reports" / "season"
    reports_dir.mkdir(parents=True, exist_ok=True)
    version = _next_report_version(reports_dir, season)

    report = reporting.build_season_report(
        season=season, report_version=version, all_rows=rows, settlement_rows=settlement_rows,
        weeks_included=weeks, generated_at=now,
    )
    out_path = reports_dir / f"{season}-v{version}.json"
    out_path.write_text(report.model_dump_json(indent=2) + "\n", encoding="utf-8")

    latest_path = reports_dir / f"{season}-latest.json"
    latest_payload = {"season": season, "latest_version": version, "path": out_path.name}
    latest_path.write_text(json.dumps(latest_payload, indent=2) + "\n", encoding="utf-8")
    return persistence.AppendResult(written=1, skipped_duplicate=0, keys_written=(str(out_path),))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-branch", default="research-data")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    now = datetime.now(UTC)
    if not args.no_push:
        git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return _apply_report(repo_dir, season=args.season, now=now)

    if args.no_push:
        apply_fn(args.data_repo_dir)
    else:
        git_durable_store.commit_and_push_with_retry(
            args.data_repo_dir, args.data_branch, apply_fn,
            commit_message=f"research season report: season={args.season} at={now.isoformat()}",
        )
    print(f"Season cumulative report written for season={args.season}.")
    print("\nSTATUS: RESEARCH-ONLY report. No bet recommendation or stake sizing anywhere in this output.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
