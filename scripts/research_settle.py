#!/usr/bin/env python3
"""Milestone E, Part E: postgame settlement.

For every CAPTURED observation whose game is now final (per live CFBD),
derive and durably persist its MarketSettlement. Idempotent: re-running
against the same final game re-derives the identical fact and is deduped
by research.persistence's settlement fact-fingerprint -- no duplicate rows.

    python scripts/research_settle.py --season 2026 --data-repo-dir /path/to/checkout
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.research import git_durable_store, persistence  # noqa: E402
from cfb_edge_finder.research.settlement import extract_game_result, settle_market  # noqa: E402


def _apply_settle(
    repo_dir: Path, *, season: int, raw_games_by_source_id: dict[str, dict], now: datetime
) -> persistence.AppendResult:
    base_dir = repo_dir / "data" / "research"
    obs_path = persistence.canonical_path(base_dir, persistence.OBSERVATIONS_SUBDIR, season)
    settle_path = persistence.canonical_path(base_dir, persistence.SETTLEMENTS_SUBDIR, season)

    rows = persistence.read_observation_rows(obs_path)
    by_game: dict[str, list] = {}
    for row in rows:
        if row.observation.game_id:
            by_game.setdefault(row.observation.game_id, []).append(row)

    settlements = []
    for game_id, game_rows in by_game.items():
        # raw_games_by_source_id is keyed by OUR OWN canonical game_id (main() below builds it via the
        # same normalize_cfbd_game path Milestone B already uses), not CFBD's numeric id -- so this is a
        # direct lookup, no separate cross-reference step needed.
        raw_game = raw_games_by_source_id.get(game_id)
        if raw_game is None:
            continue
        result = extract_game_result(raw_game, game_id=game_id, season=season, captured_at=now)
        for row in game_rows:
            if row.observation.family is None or row.observation.model_probability is None:
                continue
            settlements.append(settle_market(row.observation, result, settled_at=now))

    return persistence.append_settlement_rows(settle_path, settlements)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--data-repo-dir", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--data-branch", default="research-data")
    parser.add_argument("--no-push", action="store_true")
    args = parser.parse_args()

    settings = Settings.from_env()
    if not settings.cfbd_api_key:
        print("ERROR: CFBD_API_KEY not set.", file=sys.stderr)
        return 2

    now = datetime.now(UTC)
    cfbd_client = CFBDClient(api_key=settings.cfbd_api_key)
    try:
        raw_games = cfbd_client.fetch_games(season=args.season, season_type=None)
    except CFBDAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    from cfb_edge_finder.ingestion.game_normalization import GameNormalizationError, normalize_cfbd_game  # noqa: E402

    raw_games_by_game_id: dict[str, dict] = {}
    for raw in raw_games:
        try:
            game = normalize_cfbd_game(raw, observed_at=now)
        except GameNormalizationError:
            continue
        raw_games_by_game_id[game.game_id] = raw

    if not args.no_push:
        git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return _apply_settle(repo_dir, season=args.season, raw_games_by_source_id=raw_games_by_game_id, now=now)

    if args.no_push:
        result = apply_fn(args.data_repo_dir)
    else:
        push_result = git_durable_store.commit_and_push_with_retry(
            args.data_repo_dir,
            args.data_branch,
            apply_fn,
            commit_message=f"research settlement: season={args.season} at={now.isoformat()}",
        )
        result = push_result.append_result

    print(f"Settlement facts written: {result.written}, unchanged/duplicate: {result.skipped_duplicate}")
    print("\nSTATUS: RESEARCH-ONLY settlement derivation. No bet grading, staking, or payout logic anywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
