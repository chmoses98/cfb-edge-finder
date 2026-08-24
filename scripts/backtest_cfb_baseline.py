#!/usr/bin/env python3
"""Milestone C walk-forward backtest CLI.

    python scripts/backtest_cfb_baseline.py --seasons 2022 2023 2024

Fetches (live CFBD, or --mode fixture for a small deterministic
dry-run corpus), builds the TeamGameLine corpus, runs a genuine
chronological walk-forward backtest (cfb_edge_finder.modeling.backtest --
never a random train/test split, see that module's docstring), and prints
both the naive-benchmark and full-model metrics side by side, with
segmentation by season, early-vs-later season, neutral-site, and
FBS-vs-FBS vs FBS-vs-FCS.

IMPORTANT: fixture mode uses a small synthetic corpus (see
src/cfb_edge_finder/data/fixtures/cfb_backtest_fixture_corpus.json) purely
to exercise the pipeline deterministically without live access. Its
metrics are NOT meaningful forecasting-quality numbers and must never be
reported as such -- see docs/MILESTONE_C.md.
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
from cfb_edge_finder.modeling.backtest import (  # noqa: E402
    BacktestMetrics,
    compute_metrics,
    run_walk_forward_backtest,
    segment,
)
from cfb_edge_finder.modeling.corpus import TeamGameLine, build_team_game_lines  # noqa: E402
from cfb_edge_finder.modeling.diagnostics import (  # noqa: E402
    print_diagnostic_report,
    source_of_margin_bias_summary,
    source_of_total_bias_summary,
)
from cfb_edge_finder.modeling.priors import DEFAULT_SEASON_SHRINKAGE_K  # noqa: E402
from cfb_edge_finder.modeling.ratings import (  # noqa: E402
    DEFAULT_FCS_RIDGE_LAMBDA,
    DEFAULT_PACE_SHRINKAGE_K,
    DEFAULT_RIDGE_LAMBDA,
)
from cfb_edge_finder.modeling.score_model import DEFAULT_RESIDUAL_SCALE  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_FIXTURE_PATH = REPO_ROOT / "src" / "cfb_edge_finder" / "data" / "fixtures" / "cfb_backtest_fixture_corpus.json"


def _fetch_live_lines(
    seasons: list[int], client: CFBDClient, captured_at: datetime
) -> tuple[list[TeamGameLine], list[dict]]:
    all_lines: list[TeamGameLine] = []
    all_skipped: list[dict] = []
    for season in seasons:
        raw_games = client.fetch_games(season=season, season_type=None, division="fbs")
        raw_advanced = client.fetch_advanced_team_game_stats(season=season)
        lines, skipped = build_team_game_lines(raw_games, raw_advanced, captured_at=captured_at)
        all_lines.extend(lines)
        for s in skipped:
            s["season"] = season
        all_skipped.extend(skipped)
    return all_lines, all_skipped


def _load_fixture_lines(path: Path) -> list[TeamGameLine]:
    raw = json.loads(path.read_text())
    return [TeamGameLine.model_validate(row) for row in raw["lines"]]


def _print_segment(label: str, subset: list) -> None:
    if not subset:
        return
    print(f"\n=== Segment: {label} ===")
    raw_metrics = compute_metrics(subset, prob_attr="model_prob_home_win")
    cal_metrics = compute_metrics(subset, prob_attr="calibrated_prob_home_win")
    print(_metrics_summary(f"Milestone C model (raw) -- {label}", raw_metrics))
    print(_metrics_summary(f"Milestone C model (calibrated) -- {label}", cal_metrics))


def _metrics_summary(label: str, metrics: BacktestMetrics) -> str:
    lines = [
        f"--- {label} (n={metrics.n_games}) ---",
        f"  winner log loss:        {metrics.winner_log_loss:.4f}",
        f"  winner Brier:            {metrics.winner_brier:.4f}",
        f"  margin MAE/RMSE/bias: {metrics.margin_mae:.2f} / {metrics.margin_rmse:.2f} / {metrics.margin_bias:+.2f}",
        f"  margin 90% interval coverage: {metrics.margin_interval_coverage_90:.3f}",
        f"  total MAE/RMSE/bias: {metrics.total_mae:.2f} / {metrics.total_rmse:.2f} / {metrics.total_bias:+.2f}",
        f"  total 90% interval coverage:  {metrics.total_interval_coverage_90:.3f}",
        "  calibration bins:",
    ]
    for b in metrics.calibration_bins:
        lines.append(
            f"    {b['bin']}: predicted={b['predicted_prob']:.3f} observed={b['observed_win_rate']:.3f} n={b['n']}"
        )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--seasons", type=int, nargs="+", required=True)
    parser.add_argument("--mode", choices=["auto", "fixture", "live"], default="auto")
    parser.add_argument("--fixture-file", type=Path, default=DEFAULT_FIXTURE_PATH)
    parser.add_argument("--min-week-for-first-prediction", type=int, default=2)
    parser.add_argument("--n-simulations", type=int, default=4000)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--calibration-method",
        choices=["platt", "isotonic", "none"],
        default="platt",
        help="Leakage-safe win-probability recalibration method (modeling/calibration.py). "
        "'none' disables recalibration entirely (calibrated == raw).",
    )
    parser.add_argument("--ridge-lambda", type=float, default=DEFAULT_RIDGE_LAMBDA)
    parser.add_argument("--fcs-ridge-lambda", type=float, default=DEFAULT_FCS_RIDGE_LAMBDA)
    parser.add_argument("--pace-shrinkage-k", type=float, default=DEFAULT_PACE_SHRINKAGE_K)
    parser.add_argument("--season-shrinkage-k", type=float, default=DEFAULT_SEASON_SHRINKAGE_K)
    parser.add_argument(
        "--fcs-mode",
        choices=["pooled", "tiered"],
        default="pooled",
        help="'pooled' (Milestone C default) or 'tiered' (Milestone C.2 candidate, "
        "modeling/ratings.py's weak/average/strong historical-performance tiers).",
    )
    parser.add_argument(
        "--pace-mode",
        choices=["symmetric", "matchup"],
        default="symmetric",
        help="'symmetric' (Milestone C default: both teams share one (home_pace + away_pace)/2 "
        "expected-plays value) or 'matchup' (Milestone C.2 totals candidate, modeling/ratings.py's "
        "own-offense-pace x opponent-defense-pace-allowed interaction).",
    )
    parser.add_argument(
        "--residual-scale",
        type=float,
        default=DEFAULT_RESIDUAL_SCALE,
        help="Milestone C.2 uncertainty-calibration candidate: global multiplier on every simulated "
        "residual draw (1.0 = Milestone C default/no-op). See score_model.py's DEFAULT_RESIDUAL_SCALE.",
    )
    parser.add_argument(
        "--variant-label",
        default=None,
        help="Free-text label printed with the run, for matching against an ablation table.",
    )
    parser.add_argument(
        "--diagnostics",
        action="store_true",
        help="Print the full Milestone C.2 segmented diagnostic report (modeling/diagnostics.py) "
        "in addition to the standard segments.",
    )
    args = parser.parse_args()

    settings = Settings.from_env()
    resolved_mode = args.mode
    if resolved_mode == "auto":
        resolved_mode = "live" if settings.cfbd_api_key else "fixture"
        if resolved_mode == "fixture":
            print("NOTICE: CFBD_API_KEY not set -- fixture mode. Metrics are NOT meaningful.", file=sys.stderr)

    captured_at = datetime.now(UTC)
    if resolved_mode == "live":
        client = CFBDClient(api_key=settings.cfbd_api_key)
        try:
            lines, skipped = _fetch_live_lines(args.seasons, client, captured_at)
        except CFBDAuthError as exc:
            print(f"ERROR: live mode requested but {exc}", file=sys.stderr)
            return 2
        print(f"Fetched {len(lines)} team-game lines across seasons {args.seasons}; {len(skipped)} games skipped.")
        if skipped:
            print("Skip reasons (first 20):")
            for s in skipped[:20]:
                print(f"  season={s.get('season')} game={s.get('game_id')}: {s.get('reason')}")
    else:
        lines = _load_fixture_lines(args.fixture_file)
        print(f"Loaded {len(lines)} fixture team-game lines. NOT live data -- see module docstring.")

    if args.variant_label:
        print(f"\n=== VARIANT: {args.variant_label} ===")
    print(
        f"Config: ridge_lambda={args.ridge_lambda} fcs_ridge_lambda={args.fcs_ridge_lambda} "
        f"pace_shrinkage_k={args.pace_shrinkage_k} season_shrinkage_k={args.season_shrinkage_k} "
        f"fcs_mode={args.fcs_mode} pace_mode={args.pace_mode} residual_scale={args.residual_scale} "
        f"calibration_method={args.calibration_method}"
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
    )
    if not outcomes:
        print("ERROR: zero backtest outcomes produced -- check corpus/season coverage.", file=sys.stderr)
        return 3

    print(f"\n=== Overall ({len(outcomes)} predicted games) ===")
    naive_metrics = compute_metrics(outcomes, prob_attr="naive_prob_home_win")
    raw_metrics = compute_metrics(outcomes, prob_attr="model_prob_home_win")
    cal_metrics = compute_metrics(outcomes, prob_attr="calibrated_prob_home_win")
    print(_metrics_summary("Naive benchmark", naive_metrics))
    print(_metrics_summary("Milestone C model (raw, uncalibrated)", raw_metrics))
    print(_metrics_summary(f"Milestone C model (calibrated, method={args.calibration_method})", cal_metrics))

    fbs_vs_fbs = segment(outcomes, lambda o: o.is_fbs_vs_fbs)
    fbs_vs_fcs = segment(outcomes, lambda o: not o.is_fbs_vs_fbs)
    neutral = segment(outcomes, lambda o: o.is_neutral_site)
    non_neutral = segment(outcomes, lambda o: not o.is_neutral_site)

    for label, subset in [
        ("FBS-vs-FBS", fbs_vs_fbs),
        ("FBS-vs-FCS", fbs_vs_fcs),
        ("Neutral site", neutral),
        ("Home/away (non-neutral)", non_neutral),
    ]:
        _print_segment(label, subset)

    for season in sorted({o.season for o in outcomes}):
        season_subset = segment(outcomes, lambda o, s=season: o.season == s)
        _print_segment(f"season {season}", season_subset)

    early = segment(outcomes, lambda o: o.week <= 3)
    later = segment(outcomes, lambda o: o.week > 3)
    _print_segment("Early season (week<=3)", early)
    _print_segment("Later season (week>3)", later)

    if args.diagnostics:
        print("\n=== Milestone C.2 diagnostic segmentation (calibrated view) ===")
        print_diagnostic_report(outcomes)
        print("\n=== Milestone C.2 margin-bias source summary ===")
        for key, value in source_of_margin_bias_summary(outcomes).items():
            print(f"  {key}: {value if value is None else f'{value:+.2f}'}")
        print("\n=== Milestone C.2 total-bias source summary ===")
        for key, value in source_of_total_bias_summary(outcomes).items():
            print(f"  {key}: {value if value is None else f'{value:+.2f}'}")

    print(f"\nMode: {resolved_mode}. Captured at: {captured_at.isoformat()}.")
    if resolved_mode == "fixture":
        print("REMINDER: fixture-mode metrics are illustrative pipeline checks only, not real forecasting results.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
