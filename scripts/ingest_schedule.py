#!/usr/bin/env python3
"""CFB schedule ingestion CLI (mission spec section 10).

    python scripts/ingest_schedule.py --season 2026

Fetches source games (live CFBD if CFBD_API_KEY is set, otherwise a
deterministic fixture -- see --mode), normalizes teams and games,
validates every record, detects reschedules against any prior artifact
for the same season, writes a compact canonical schedule artifact, and
prints a human-readable summary. Never hides failures: every count in the
summary has a corresponding itemized list.

IMPORTANT: fixture mode uses synthetic, illustrative game data (see
src/cfb_edge_finder/data/fixtures/cfbd_games_2026_sample.json) for
exercising the pipeline deterministically. It is NOT a real fetched 2026
schedule and must never be reported or treated as one -- see
docs/MILESTONE_B.md.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.ids import assert_unique_game_ids  # noqa: E402
from cfb_edge_finder.ingestion.game_normalization import GameNormalizationError, normalize_cfbd_game  # noqa: E402
from cfb_edge_finder.ingestion.reconciliation import detect_reschedule  # noqa: E402
from cfb_edge_finder.ingestion.summary import IngestionSummary  # noqa: E402
from cfb_edge_finder.ingestion.team_matching import TeamResolutionError  # noqa: E402
from cfb_edge_finder.ingestion.week_labels import UnclassifiablePostseasonError  # noqa: E402
from cfb_edge_finder.schemas.common import SeasonType  # noqa: E402
from cfb_edge_finder.schemas.game import GameRecord  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = REPO_ROOT / "src" / "cfb_edge_finder" / "data" / "fixtures" / "cfbd_games_2026_sample.json"
SCHEDULE_ARTIFACT_DIR = REPO_ROOT / "data" / "schedules"


def _load_fixture(path: Path) -> list[dict[str, Any]]:
    return json.loads(path.read_text())


def _load_previous_artifact(season: int) -> dict[str, str]:
    """Returns {vendor_game_id: canonical_game_id} from a prior run's
    artifact for this season, if one exists -- used by detect_reschedule.
    """
    artifact_path = SCHEDULE_ARTIFACT_DIR / f"{season}.json"
    if not artifact_path.exists():
        return {}
    data = json.loads(artifact_path.read_text())
    mapping: dict[str, str] = {}
    for row in data.get("games", []):
        for _source, vendor_id in row.get("source_game_ids", {}).items():
            mapping[vendor_id] = row["game_id"]
    return mapping


def _is_fbs_vs_fbs(raw: dict[str, Any]) -> bool:
    return raw.get("homeClassification") == "fbs" and raw.get("awayClassification") == "fbs"


def run_ingestion(season: int, mode: str, fixture_path: Path) -> tuple[list[GameRecord], IngestionSummary]:
    settings = Settings.from_env()
    resolved_mode = mode
    if mode == "auto":
        resolved_mode = "live" if settings.cfbd_api_key else "fixture"
        if resolved_mode == "fixture":
            print(
                "NOTICE: CFBD_API_KEY not set -- falling back to deterministic fixture mode. "
                "This run does NOT reflect a real fetched schedule.",
                file=sys.stderr,
            )

    if resolved_mode == "live":
        client = CFBDClient(api_key=settings.cfbd_api_key)
        try:
            raw_games = client.fetch_games(season=season, season_type=None)
        except CFBDAuthError as exc:
            print(f"ERROR: live mode requested but {exc}", file=sys.stderr)
            raise
    else:
        raw_games = _load_fixture(fixture_path)

    summary = IngestionSummary(season=season)
    summary.source_games_fetched = len(raw_games)

    previous_ids = _load_previous_artifact(season)

    normalized: list[GameRecord] = []
    observed_at = datetime.now(UTC)

    for raw in raw_games:
        if not _is_fbs_vs_fbs(raw):
            summary.non_fbs_filtered += 1
            continue
        try:
            game = normalize_cfbd_game(raw, observed_at=observed_at)
        except GameNormalizationError as exc:
            cause = exc.cause
            if isinstance(cause, TeamResolutionError):
                summary.unresolved_team_aliases.append(
                    f"{cause.raw_name!r} (source={cause.source}, game={exc.raw_game_id})"
                )
            elif isinstance(cause, UnclassifiablePostseasonError):
                summary.validation_failures.append(f"game {exc.raw_game_id}: unclassifiable postseason -- {cause}")
            else:
                summary.validation_failures.append(str(exc))
            continue

        game = detect_reschedule(previous_ids, game, source="cfbd")

        normalized.append(game)
        summary.canonical_teams_referenced.add(game.home_team_id)
        summary.canonical_teams_referenced.add(game.away_team_id)
        if game.neutral_site:
            summary.neutral_site_games += 1
        if game.season_type != SeasonType.REGULAR:
            summary.postseason_games += 1

    game_ids = [g.game_id for g in normalized]
    try:
        assert_unique_game_ids(game_ids)
    except ValueError as exc:
        summary.validation_failures.append(f"duplicate canonical game_id(s) within this run: {exc}")

    summary.fbs_games_retained = len(normalized)
    return normalized, summary


def write_artifact(season: int, games: list[GameRecord], mode: str) -> Path:
    SCHEDULE_ARTIFACT_DIR.mkdir(parents=True, exist_ok=True)
    artifact_path = SCHEDULE_ARTIFACT_DIR / f"{season}.json"
    payload = {
        "season": season,
        "generated_at": datetime.now(UTC).isoformat(),
        "source_mode": mode,
        "game_count": len(games),
        "games": [json.loads(g.model_dump_json()) for g in sorted(games, key=lambda g: g.game_id)],
    }
    artifact_path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n")
    return artifact_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument(
        "--mode",
        choices=["auto", "fixture", "live"],
        default="auto",
        help="auto: live if CFBD_API_KEY is set, else fixture (default). fixture: force deterministic fixture "
        "data. live: force a real API call, fails loudly if no CFBD_API_KEY is configured.",
    )
    parser.add_argument("--fixture-file", type=Path, default=DEFAULT_FIXTURE_PATH)
    args = parser.parse_args()

    try:
        games, summary = run_ingestion(args.season, args.mode, args.fixture_file)
    except CFBDAuthError:
        return 2

    resolved_mode = args.mode if args.mode != "auto" else ("live" if Settings.from_env().cfbd_api_key else "fixture")
    artifact_path = write_artifact(args.season, games, resolved_mode)

    print(summary.render())
    print(f"\nWrote canonical schedule artifact: {artifact_path}")
    if resolved_mode == "fixture":
        print("REMINDER: this run used fixture data, not a live fetch -- see docs/MILESTONE_B.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
