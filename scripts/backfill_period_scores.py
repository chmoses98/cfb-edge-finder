#!/usr/bin/env python3
"""Backfill per-period scoring into the append-only period store, with provenance.

Offline (default for tests / local): ``--summaries-dir DIR`` reads saved ESPN summary JSON files (one per game).
Online: ``--fetch`` pulls summaries from ESPN for the games in ``--team-games`` that lack period_points (the live
host is not reachable from every sandbox; the GitHub Actions workflow period-score-backfill.yml does this).

Never writes team_games.jsonl and never estimates a period. ``--dry-run`` writes nothing and prints counts.
Exit 0 on a clean run (including quarantined records, which are reported); 2 on unreadable input.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from cfb_edge_finder.research.period_backfill import PeriodStore, record_from_summary
from cfb_edge_finder.scripting.gamelog import TeamGame


def _load_rows(path: Path) -> list[TeamGame]:
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(TeamGame.from_dict(json.loads(line)))
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--team-games", type=Path, required=True)
    ap.add_argument("--store-dir", type=Path, required=True)
    ap.add_argument("--summaries-dir", type=Path)
    ap.add_argument("--fetch", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)
    try:
        rows = _load_rows(args.team_games)
    except (OSError, ValueError, KeyError) as exc:
        print(f"cannot read team games: {exc}", file=sys.stderr)
        return 2
    expected: dict[str, tuple[str, str]] = {}
    need: dict[str, None] = {}
    for r in rows:
        if r.site in ("home", "neutral") and r.opponent_id:
            expected.setdefault(r.game_id, (r.team_id, r.opponent_id))
        if r.period_points is None:
            need[r.game_id] = None
    store = PeriodStore(args.store_dir if not args.dry_run else args.store_dir)
    counts: dict[str, int] = {"games_needing_periods": len(need), "unavailable": 0, "no_record": 0}
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids = list(need)[: args.limit or None]
    for gid in ids:
        summary = None
        if args.summaries_dir is not None:
            p = args.summaries_dir / f"{gid}.json"
            if p.exists():
                summary = json.loads(p.read_text(encoding="utf-8"))
        elif args.fetch:
            from collect_football_gamelog import fetch_summary  # type: ignore[import-not-found]

            summary, _err = fetch_summary(gid)
        if summary is None:
            counts["unavailable"] += 1
            continue
        rec = record_from_summary(summary, fetched_at=now, expected_teams=expected.get(gid))
        if rec is None:
            counts["no_record"] += 1
            continue
        if args.dry_run:
            outcome = "VALID_WOULD_WRITE" if rec.valid else "INVALID_WOULD_QUARANTINE"
        else:
            outcome = store.ingest(rec)
        counts[outcome] = counts.get(outcome, 0) + 1
    print(json.dumps({"dry_run": args.dry_run, **counts}, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    raise SystemExit(main())
