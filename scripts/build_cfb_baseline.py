#!/usr/bin/env python3
"""Milestone C research CLI: fit ratings as-of a cutoff and project one game.

    python scripts/build_cfb_baseline.py --seasons 2023 2024 2025 \\
        --as-of-season 2025 --as-of-week 8 --home ohio-state --away michigan

Fetches (live CFBD, or --mode fixture), builds the corpus, fits ratings
strictly from games before --as-of-season/--as-of-week (leakage-checked --
see cfb_edge_finder.modeling.leakage), and prints a full research
projection for the requested matchup: expected scores, win probability,
margin/total distributions at several example thresholds, and the
model/data provenance that produced it. Research mode only -- prints no
recommendation, no bet sizing, no edge classification.
"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.modeling.corpus import TeamGameLine, build_team_game_lines  # noqa: E402
from cfb_edge_finder.modeling.leakage import AsOf  # noqa: E402
from cfb_edge_finder.modeling.ratings import DEFAULT_RIDGE_LAMBDA, fit_fbs_efficiency_ratings  # noqa: E402
from cfb_edge_finder.modeling.score_model import (  # noqa: E402
    DEFAULT_RESIDUAL_SCALE,
    build_expanding_residual_pool,
    project_game,
)
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = REPO_ROOT / "src" / "cfb_edge_finder" / "data" / "fixtures" / "cfb_backtest_fixture_corpus.json"
MODEL_VERSION = "0.3.0-milestone-c2"
"""Bumped from 0.2.0 for this pass's second round of C.2 changes
(pace_mode=matchup, residual_scale=0.85 -- see docs/MILESTONE_C2.md).
Every historical prediction stays reproducible: re-running this script
with the same seasons/as-of/seed against a given git commit is
deterministic, and this string plus git_commit_sha identify exactly
which hyperparameters/code produced any past record."""
RATINGS_COMPONENT_VERSION = (
    f"ridge_lambda={DEFAULT_RIDGE_LAMBDA};pace_mode=matchup;"
    f"residual_scale={DEFAULT_RESIDUAL_SCALE};fcs_mode=pooled;calibration=platt;"
    "fcs_treatment=pooled-shrinkage-v2"
)
"""Milestone C.2: compact, versioned summary of the rating/calibration/
FCS-treatment configuration actually used, per mission section 18 ("model/
feature/calibration/uncertainty/FCS-treatment version"). fcs_mode=tiered
and pace_shrinkage_k=1.0 were evidence-tested and rejected; pace_mode=matchup
and residual_scale=0.85 were evidence-tested and ADOPTED this pass, each
selected on 2022-2024 development data alone and confirmed, once, on the
untouched 2025 season (docs/MILESTONE_C2.md)."""


def _fetch_live_lines(seasons: list[int], client: CFBDClient, captured_at: datetime) -> list[TeamGameLine]:
    all_lines: list[TeamGameLine] = []
    for season in seasons:
        raw_games = client.fetch_games(season=season, season_type=None, division="fbs")
        raw_advanced = client.fetch_advanced_team_game_stats(season=season)
        lines, _skipped = build_team_game_lines(raw_games, raw_advanced, captured_at=captured_at)
        all_lines.extend(lines)
    return all_lines


def _load_fixture_lines(path: Path) -> list[TeamGameLine]:
    raw = json.loads(path.read_text())
    return [TeamGameLine.model_validate(row) for row in raw["lines"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--as-of-season", type=int, required=True)
    parser.add_argument("--as-of-week", type=int, required=True)
    parser.add_argument("--home", required=True, help="Canonical team_id, e.g. ohio-state")
    parser.add_argument("--away", required=True)
    parser.add_argument("--neutral-site", action="store_true")
    parser.add_argument("--home-classification", default="fbs")
    parser.add_argument("--away-classification", default="fbs")
    parser.add_argument("--mode", choices=["auto", "fixture", "live"], default="auto")
    parser.add_argument("--fixture-file", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--n-simulations", type=int, default=20000)
    parser.add_argument("--seed", type=int, default=0)
    args = parser.parse_args()

    settings = Settings.from_env()
    resolved_mode = args.mode
    if resolved_mode == "auto":
        resolved_mode = "live" if settings.cfbd_api_key else "fixture"
        if resolved_mode == "fixture":
            print("NOTICE: CFBD_API_KEY not set -- fixture mode. NOT a real projection.", file=sys.stderr)

    captured_at = datetime.now(UTC)
    if resolved_mode == "live":
        client = CFBDClient(api_key=settings.cfbd_api_key)
        try:
            lines = _fetch_live_lines(args.seasons, client, captured_at)
        except CFBDAuthError as exc:
            print(f"ERROR: live mode requested but {exc}", file=sys.stderr)
            return 2
    else:
        lines = _load_fixture_lines(args.fixture_file)
        print("NOTICE: fixture mode -- illustrative pipeline check only, not a real projection.", file=sys.stderr)

    as_of = AsOf(season=args.as_of_season, week=args.as_of_week)
    history = [ln for ln in lines if ln.as_of.is_strictly_before(as_of)]
    if not history:
        print(f"ERROR: no leakage-safe history strictly before {as_of!r} in the loaded corpus.", file=sys.stderr)
        return 3

    ratings = fit_fbs_efficiency_ratings(history, as_of)
    residual_pool = build_expanding_residual_pool(history, as_of)

    projection = project_game(
        home_id=args.home,
        away_id=args.away,
        home_classification=args.home_classification,
        away_classification=args.away_classification,
        is_neutral_site=args.neutral_site,
        ratings=ratings,
        prior_season_ratings=None,
        residual_pool=residual_pool,
        home_percent_passing_ppa=None,
        away_percent_passing_ppa=None,
        n_simulations=args.n_simulations,
        seed=args.seed,
    )

    dist = projection.to_game_distribution()
    uncertainty = projection.to_uncertainty_profile()
    record = projection.to_projection_record(
        projection_id=str(uuid.uuid4()),
        game_id=f"research-{args.home}-vs-{args.away}-{as_of.season}-w{as_of.week}",
        model_version=ModelVersion(
            model_version=MODEL_VERSION,
            ratings_component_version=RATINGS_COMPONENT_VERSION,
            pricing_engine_version="0.1.0",
        ),
        provenance=DataProvenance(schedule_source="cfbd", data_timestamp=captured_at),
        projection_timestamp=datetime.now(UTC),
    )

    print(f"\n=== Research projection: {args.home} (home) vs {args.away} (away), as-of {as_of!r} ===")
    print(f"Mode: {resolved_mode}. Training rows: {ratings.n_training_rows}, teams: {ratings.n_teams_with_data}")
    print(f"Expected points: home={projection.expected_home_points:.1f} away={projection.expected_away_points:.1f}")
    print(f"GameDistribution: {dist}")
    print(f"P(home win) = {projection.prob_home_win():.4f}, P(away win) = {projection.prob_away_win():.4f}")
    for threshold in (-14, -7, -3.5, 0, 3.5, 7, 14):
        print(f"  P(margin > {threshold:+.1f}) = {projection.prob_margin_greater_than(threshold):.4f}")
    for threshold in (35, 42, 49, 56, 63):
        print(f"  P(total > {threshold}) = {projection.prob_total_greater_than(threshold):.4f}")
    print(f"UncertaintyProfile: {uncertainty}")
    print(f"\nProjectionRecord: {record.model_dump_json(indent=2)}")
    if resolved_mode == "fixture":
        print("\nREMINDER: this run used fixture data, not a live fetch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
