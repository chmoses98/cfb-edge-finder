#!/usr/bin/env python3
"""Milestone E, Part E: postgame settlement.

For every CAPTURED observation whose game is now final, derive and
durably persist its MarketSettlement. Idempotent: re-running against the
same final game re-derives the identical fact and is deduped by
research.persistence's settlement fact-fingerprint -- no duplicate rows.

Game results come from research.result_provider: CFBD primary, with a
strictly-validated fail-closed ESPN fallback engaged ONLY when CFBD is
recoverably unavailable (the 2026-08-29/30 429 quota outage). Games the
fallback cannot validate beyond doubt simply do not settle this run.

    python scripts/research_settle.py --season 2026 --data-repo-dir /path/to/checkout

*** ORDER OF OPERATIONS ***
The durable-data branch is checked out BEFORE the provider runs, because
the fallback path needs two things only that branch holds: which games
actually have observations to settle, and the durable CFBD-derived
schedule identity it validates ESPN events against. With --no-push the
checkout is skipped, so --data-repo-dir must already contain the durable
data (the workflow passes a separate research-data checkout).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.research import git_durable_store, persistence, result_provider  # noqa: E402
from cfb_edge_finder.research.settlement import settle_market  # noqa: E402
from cfb_edge_finder.schemas.settlement import GameResult  # noqa: E402


def _settleable_game_ids(repo_dir: Path, season: int) -> set[str]:
    """The games whose observations this run could settle -- what the
    fallback provider needs results for. Same eligibility filter
    `_apply_settle` uses (family + model_probability present)."""
    obs_path = persistence.canonical_path(repo_dir / "data" / "research", persistence.OBSERVATIONS_SUBDIR, season)
    if not obs_path.exists():
        return set()
    return {
        row.observation.game_id
        for row in persistence.read_observation_rows(obs_path)
        if row.observation.game_id
        and row.observation.family is not None
        and row.observation.model_probability is not None
    }


def _apply_settle(
    repo_dir: Path, *, season: int, results_by_game_id: dict[str, GameResult], now: datetime
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
        # results_by_game_id is keyed by OUR OWN canonical game_id (the provider builds it via the same
        # normalize_cfbd_game path Milestone B already uses) -- a direct lookup, no cross-reference step.
        # A game the provider could not resolve (CFBD row absent, or a fail-closed fallback outcome) is
        # simply skipped: it keeps its existing settlement state untouched.
        result = results_by_game_id.get(game_id)
        if result is None:
            continue
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

    # Durable data first: the fallback needs the observation ledger and
    # the durable schedule identity BEFORE any network result fetch.
    if not args.no_push:
        git_durable_store.ensure_branch_checked_out(args.data_repo_dir, args.data_branch)

    needed_game_ids = _settleable_game_ids(args.data_repo_dir, args.season)

    try:
        outcome = result_provider.resolve_game_results(
            season=args.season,
            now=now,
            cfbd_client=cfbd_client,
            repo_dir=args.data_repo_dir,
            needed_game_ids=needed_game_ids,
        )
    except CFBDAuthError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    except result_provider.ResultProviderUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    def apply_fn(repo_dir: Path) -> persistence.AppendResult:
        return _apply_settle(
            repo_dir, season=args.season, results_by_game_id=outcome.results_by_game_id, now=now
        )

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

    summary = {
        "settleable_games_in_ledger": len(needed_game_ids),
        **outcome.summary_dict(),
        "settlement_facts_written": result.written,
        "unchanged_duplicate": result.skipped_duplicate,
    }
    print(json.dumps(summary, indent=2))
    print("\nSTATUS: RESEARCH-ONLY settlement derivation. No bet grading, staking, or payout logic anywhere.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
