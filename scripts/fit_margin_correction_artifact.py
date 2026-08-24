#!/usr/bin/env python3
"""Fits and reports the frozen C.2 margin-correction artifact
(cfb_edge_finder.modeling.margin_correction_artifact) used by the live
single-game research CLI (scripts/build_cfb_baseline.py).

    python scripts/fit_margin_correction_artifact.py --seasons 2022 2023 2024 2025

*** WHY A SEPARATE SCRIPT, NOT A NEW CODE PATH ***
This script reuses `run_walk_forward_backtest` (the exact function
docs/MILESTONE_C2.md's ablations and confirmations were produced with,
called here with `margin_correction_method="none"` so every
`GameOutcome.model_margin_mean` is the model's own RAW, uncorrected
walk-forward margin projection -- the identical quantity
`margin_correction_method="linear"` would otherwise fit against
internally) and `margin_calibration.fit_linear_margin` (the exact
function that same walk-forward correction calls at every step) --
nothing here is reimplemented. Its only job is to report the resulting
coefficients/training-cutoff/n so a human can freeze them into
modeling/margin_correction_artifact.py; it never writes to that module
itself. This mirrors exactly how every other C.2 hyperparameter
(ridge_lambda, residual_scale, margin_correction_method itself) was
selected in this repo: run live, read the printed numbers, commit them.

*** WHY THIS IS A FROZEN ARTIFACT, NOT A PER-LIVE-CALL REFIT ***
See modeling/margin_correction_artifact.py's module docstring.
"""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from cfb_edge_finder.config import Settings  # noqa: E402
from cfb_edge_finder.data.cfbd_client import CFBDAuthError, CFBDClient  # noqa: E402
from cfb_edge_finder.modeling.backtest import run_walk_forward_backtest  # noqa: E402
from cfb_edge_finder.modeling.corpus import TeamGameLine, build_team_game_lines  # noqa: E402
from cfb_edge_finder.modeling.margin_calibration import fit_linear_margin  # noqa: E402
from cfb_edge_finder.modeling.ratings import DEFAULT_FCS_RIDGE_LAMBDA, DEFAULT_PACE_SHRINKAGE_K  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = REPO_ROOT / "src" / "cfb_edge_finder" / "data" / "fixtures" / "cfb_backtest_fixture_corpus.json"

# The final selected C.2 model (docs/MILESTONE_C2.md section 35) -- these
# are the defaults below, NOT this codebase's raw per-parameter defaults
# (e.g. DEFAULT_RIDGE_LAMBDA is 25.0; the selected model uses 10.0).
FINAL_C2_RIDGE_LAMBDA = 10.0
FINAL_C2_PACE_MODE = "matchup"
FINAL_C2_RESIDUAL_SCALE = 0.85
FINAL_C2_FCS_MODE = "pooled"
FINAL_C2_SEASON_SHRINKAGE_K = 4.0
FINAL_C2_CALIBRATION_METHOD = "platt"


def _fetch_live_lines(seasons: list[int], client: CFBDClient, captured_at: datetime) -> list[TeamGameLine]:
    all_lines: list[TeamGameLine] = []
    for season in seasons:
        raw_games = client.fetch_games(season=season, season_type=None, division="fbs")
        raw_advanced = client.fetch_advanced_team_game_stats(season=season)
        lines, _skipped = build_team_game_lines(raw_games, raw_advanced, captured_at=captured_at)
        all_lines.extend(lines)
    return all_lines


def _load_fixture_lines(path: Path) -> list[TeamGameLine]:
    import json

    raw = json.loads(path.read_text())
    return [TeamGameLine.model_validate(row) for row in raw["lines"]]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--mode", choices=["auto", "fixture", "live"], default="auto")
    parser.add_argument("--fixture-file", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--min-week-for-first-prediction", type=int, default=2)
    parser.add_argument("--n-simulations", type=int, default=6000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--ridge-lambda", type=float, default=FINAL_C2_RIDGE_LAMBDA)
    parser.add_argument("--fcs-ridge-lambda", type=float, default=DEFAULT_FCS_RIDGE_LAMBDA)
    parser.add_argument("--pace-shrinkage-k", type=float, default=DEFAULT_PACE_SHRINKAGE_K)
    parser.add_argument("--season-shrinkage-k", type=float, default=FINAL_C2_SEASON_SHRINKAGE_K)
    parser.add_argument("--fcs-mode", choices=["pooled", "tiered"], default=FINAL_C2_FCS_MODE)
    parser.add_argument("--pace-mode", choices=["symmetric", "matchup"], default=FINAL_C2_PACE_MODE)
    parser.add_argument("--residual-scale", type=float, default=FINAL_C2_RESIDUAL_SCALE)
    parser.add_argument(
        "--calibration-method", choices=["platt", "isotonic", "none"], default=FINAL_C2_CALIBRATION_METHOD
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    resolved_mode = args.mode
    if resolved_mode == "auto":
        resolved_mode = "live" if settings.cfbd_api_key else "fixture"
        if resolved_mode == "fixture":
            print("NOTICE: CFBD_API_KEY not set -- fixture mode. NOT a real fit.", file=sys.stderr)

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
        print("NOTICE: fixture mode -- illustrative pipeline check only, NOT a real fit.", file=sys.stderr)

    print(
        f"Config: seasons={args.seasons} ridge_lambda={args.ridge_lambda} "
        f"fcs_ridge_lambda={args.fcs_ridge_lambda} pace_shrinkage_k={args.pace_shrinkage_k} "
        f"season_shrinkage_k={args.season_shrinkage_k} fcs_mode={args.fcs_mode} "
        f"pace_mode={args.pace_mode} residual_scale={args.residual_scale} "
        f"calibration_method={args.calibration_method} "
        f"(margin_correction_method=none -- fitting against RAW model_margin_mean)"
    )

    outcomes = run_walk_forward_backtest(
        lines,
        min_week_for_first_prediction=args.min_week_for_first_prediction,
        n_simulations=args.n_simulations,
        seed=args.seed,
        calibration_method=args.calibration_method,
        ridge_lambda=args.ridge_lambda,
        fcs_ridge_lambda=args.fcs_ridge_lambda,
        pace_shrinkage_k=args.pace_shrinkage_k,
        season_shrinkage_k=args.season_shrinkage_k,
        fcs_mode=args.fcs_mode,
        pace_mode=args.pace_mode,
        residual_scale=args.residual_scale,
        margin_correction_method="none",
        total_correction_method="none",
    )
    if not outcomes:
        print("ERROR: zero backtest outcomes produced -- check corpus/season coverage.", file=sys.stderr)
        return 3

    fbs_outcomes = [o for o in outcomes if o.is_fbs_vs_fbs]
    projected = np.array([o.model_margin_mean for o in fbs_outcomes])
    actual = np.array([o.actual_home_points - o.actual_away_points for o in fbs_outcomes])
    params = fit_linear_margin(projected, actual)

    last_season, last_week = max((o.season, o.week) for o in outcomes)

    print(f"\n=== Margin-correction fit (FBS-vs-FBS only, n={len(fbs_outcomes)}) ===")
    print(f"a = {params.a!r}")
    print(f"b = {params.b!r}")
    print(f"is_identity_fallback = {params.is_identity_fallback}")
    print(f"training corpus covers through: season={last_season}, week={last_week}")
    print("\nFreeze into modeling/margin_correction_artifact.py as:")
    print(f"  FROZEN_MARGIN_CORRECTION_PARAMS = LinearMarginParams(a={params.a!r}, b={params.b!r})")
    print(f"  MARGIN_CORRECTION_TRAINING_N = {len(fbs_outcomes)}")
    print(f"  (training cutoff: strictly before AsOf(season={last_season + 1}, week=0))")

    print(f"\nMode: {resolved_mode}. Captured at: {captured_at.isoformat()}.")
    if resolved_mode == "fixture":
        print("REMINDER: fixture-mode coefficients are illustrative pipeline checks only, not a real fit.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
