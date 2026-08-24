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
from cfb_edge_finder.modeling.margin_correction_artifact import (  # noqa: E402
    FROZEN_MARGIN_CORRECTION_PARAMS,
    MARGIN_CORRECTION_ARTIFACT_VERSION,
    MARGIN_CORRECTION_METHOD,
    MARGIN_CORRECTION_TRAINING_CUTOFF,
)
from cfb_edge_finder.modeling.ratings import DEFAULT_RIDGE_LAMBDA, fit_fbs_efficiency_ratings  # noqa: E402
from cfb_edge_finder.modeling.score_model import (  # noqa: E402
    DEFAULT_RESIDUAL_SCALE,
    apply_margin_correction,
    build_expanding_residual_pool,
    project_game,
)
from cfb_edge_finder.schemas.provenance import DataProvenance, ModelVersion  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = REPO_ROOT / "src" / "cfb_edge_finder" / "data" / "fixtures" / "cfb_backtest_fixture_corpus.json"
MODEL_VERSION = "0.4.0-milestone-c2-live-margin-correction"
"""Bumped from 0.3.0-milestone-c2 (which described this CLI's ratings/
pace/residual-scale/calibration behavior only -- it did NOT yet apply the
validated margin_correction_method="linear" correction to a live
single-game projection at all, docs/MILESTONE_C2.md section 38's
explicitly flagged gap). This version identifies live projections that
DO apply that correction (see MARGIN_CORRECTION_METHOD below) -- a real
behavioral difference from every projection produced under
"0.3.0-milestone-c2", so that prior string is never reused for these.
Every historical prediction stays reproducible: re-running this script
with the same seasons/as-of/seed against a given git commit is
deterministic, and this string plus git_commit_sha identify exactly
which hyperparameters/code produced any past record."""
def _ratings_component_version(margin_correction_method: str) -> str:
    """Milestone C.2: compact, versioned summary of the rating/calibration/
    FCS-treatment/margin-correction configuration ACTUALLY USED for this
    specific run, per mission section 18 ("model/feature/calibration/
    uncertainty/FCS-treatment version"). Deliberately a function of the
    resolved `--margin-correction-method`, not a module-level constant --
    a run made with `--margin-correction-method none` must never claim
    `margin_correction_method=linear` in its own provenance record, even
    though "linear" is the final selected C.2 model's default. fcs_mode=
    tiered and pace_shrinkage_k=1.0 were evidence-tested and rejected;
    pace_mode=matchup and residual_scale=0.85 were evidence-tested and
    ADOPTED in Part 2; margin_correction_method="linear" was evidence-
    tested and ADOPTED in Part 3 (docs/MILESTONE_C2.md) -- each selected
    on 2022-2024 development data alone and confirmed, once, on the
    untouched 2025 season. total_correction_method remains "none": no
    total candidate improved on doing nothing (section 32), so this
    string deliberately omits a total_correction_method entry rather than
    claiming one that does nothing."""
    artifact_version = MARGIN_CORRECTION_ARTIFACT_VERSION if margin_correction_method != "none" else None
    return (
        f"ridge_lambda={DEFAULT_RIDGE_LAMBDA};pace_mode=matchup;"
        f"residual_scale={DEFAULT_RESIDUAL_SCALE};fcs_mode=pooled;calibration=platt;"
        f"fcs_treatment=pooled-shrinkage-v2;margin_correction_method={margin_correction_method};"
        f"margin_correction_artifact={artifact_version}"
    )


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
    parser.add_argument(
        "--margin-correction-method",
        choices=["none", MARGIN_CORRECTION_METHOD],
        default=MARGIN_CORRECTION_METHOD,
        help=(
            f"Milestone C.2 Part 3 favorite-tail margin correction, applied via the frozen "
            f"artifact in modeling/margin_correction_artifact.py (version "
            f"{MARGIN_CORRECTION_ARTIFACT_VERSION}). Defaults to the final selected C.2 model's "
            f"'{MARGIN_CORRECTION_METHOD}'; pass 'none' to reproduce pre-correction behavior "
            "(e.g. for parity verification against a --margin-correction-method "
            f"{MARGIN_CORRECTION_METHOD} run)."
        ),
    )
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

    raw_projection = project_game(
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

    is_fbs_vs_fbs = args.home_classification == "fbs" and args.away_classification == "fbs"
    projection = apply_margin_correction(
        raw_projection,
        is_fbs_vs_fbs=is_fbs_vs_fbs,
        method=args.margin_correction_method,
        correction_model=(FROZEN_MARGIN_CORRECTION_PARAMS if args.margin_correction_method != "none" else None),
        artifact_version=(MARGIN_CORRECTION_ARTIFACT_VERSION if args.margin_correction_method != "none" else None),
        as_of=as_of,
        training_cutoff=MARGIN_CORRECTION_TRAINING_CUTOFF,
    )

    dist = projection.to_game_distribution()
    uncertainty = projection.to_uncertainty_profile()
    record = projection.to_projection_record(
        projection_id=str(uuid.uuid4()),
        game_id=f"research-{args.home}-vs-{args.away}-{as_of.season}-w{as_of.week}",
        model_version=ModelVersion(
            model_version=MODEL_VERSION,
            ratings_component_version=_ratings_component_version(args.margin_correction_method),
            pricing_engine_version="0.1.0",
        ),
        provenance=DataProvenance(schedule_source="cfbd", data_timestamp=captured_at),
        projection_timestamp=datetime.now(UTC),
    )

    print(f"\n=== Research projection: {args.home} (home) vs {args.away} (away), as-of {as_of!r} ===")
    print(f"Mode: {resolved_mode}. Training rows: {ratings.n_training_rows}, teams: {ratings.n_teams_with_data}")
    print(f"Model version: {MODEL_VERSION}")
    print(f"Data/training cutoff for this projection's own ratings: strictly before {as_of!r}")
    print(
        f"Margin correction: method={projection.method} applied={projection.correction_applied} "
        f"skip_reason={projection.correction_skip_reason} artifact_version={projection.artifact_version} "
        f"artifact_training_cutoff={MARGIN_CORRECTION_TRAINING_CUTOFF!r} delta={projection.margin_delta:+.3f}"
    )
    print(
        f"Expected points: home={projection.expected_home_points:.1f} away={projection.expected_away_points:.1f} "
        f"(raw, uncorrected: home={raw_projection.expected_home_points:.1f} "
        f"away={raw_projection.expected_away_points:.1f})"
    )
    print(
        f"Expected margin (corrected): {projection.expected_margin:+.2f} "
        f"(raw, uncorrected: {projection.raw_expected_margin:+.2f})"
    )
    print(f"Expected total: {projection.expected_total:.2f} (total_correction_method=none -- always unchanged)")
    print(f"GameDistribution: {dist}")
    print(f"P(home win) = {projection.prob_home_win():.4f}, P(away win) = {projection.prob_away_win():.4f}")
    for threshold in (-14, -7, -3.5, 0, 3.5, 7, 14):
        print(f"  P(margin > {threshold:+.1f}) = {projection.prob_margin_greater_than(threshold):.4f}")
    for threshold in (35, 42, 49, 56, 63):
        print(f"  P(total > {threshold}) = {projection.prob_total_greater_than(threshold):.4f}")
    print(f"UncertaintyProfile: {uncertainty}")
    print(
        "FBS-vs-FCS status: UNSUPPORTED_FOR_PRICING (margin correction never applied to a non-"
        f"FBS-vs-FBS game -- this projection is_fbs_vs_fbs={is_fbs_vs_fbs})"
    )
    print(
        "STATUS: RESEARCH-ONLY. No Kalshi pricing, betting, staking, or recommendation logic "
        "anywhere in this output."
    )
    print(f"\nProjectionRecord: {record.model_dump_json(indent=2)}")
    if resolved_mode == "fixture":
        print("\nREMINDER: this run used fixture data, not a live fetch.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
