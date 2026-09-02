"""Reproduce the production CONTROL (0.4.0) and 0.5.0 (talent prior) per
game, through the production entry points, and write one row per
FBS-vs-FBS game so V2 candidates can be compared on IDENTICAL games.

Inputs: the durable preseason research cache (games + advanced plays +
talent), i.e. exactly the inputs the model-repair mission used. Nothing
here calls CFBD.

Live-path parity notes (deliberate, disclosed):
- `home/away_percent_passing_ppa=None` -> QB state UNKNOWN (1.20x), which
  is what the live collector does (PPA is never loaded live).
- `prior_season_ratings=None`, as on the live path.
- n_simulations defaults to 4000 (research) vs 6000 live; the paired
  design shares one seed so Monte Carlo noise is common to both arms.
- Ratings history is every cached season strictly before the as-of
  (2019, 2021, ...), whereas the live 2026 path pools 2022-2025 only.

Outputs (CSV): game_id, season, week, home, away, neutral, actual margin/
total, control (0.4.0) and talent (0.5.0) projected margin/total, the
simulated sd/correlation, the empirical simulated win probability and the
closed-form (live pricer) win probability for each arm.
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from run_preseason_experiments import raw_shapes  # noqa: E402

from cfb_edge_finder.modeling.corpus import build_team_game_lines  # noqa: E402
from cfb_edge_finder.modeling.leakage import AsOf  # noqa: E402
from cfb_edge_finder.modeling.margin_correction_artifact import (  # noqa: E402
    FROZEN_MARGIN_CORRECTION_PARAMS,
    MARGIN_CORRECTION_ARTIFACT_VERSION,
    MARGIN_CORRECTION_TRAINING_CUTOFF,
)
from cfb_edge_finder.modeling.score_model import apply_margin_correction, project_game  # noqa: E402
from cfb_edge_finder.modeling.talent_prior import talent_margin_delta  # noqa: E402
from cfb_edge_finder.projections.distribution import margin_distribution, prob_greater_than  # noqa: E402
from cfb_edge_finder.research.preseason.corpus import build_feature_tables, load_cache  # noqa: E402
from cfb_edge_finder.research.preseason.experiment import (  # noqa: E402
    RESEARCH_N_SIMULATIONS,
    RESEARCH_SEED,
    build_fit,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seasons", type=int, nargs="+", default=[2021, 2022, 2023, 2024, 2025])
    parser.add_argument("--n-simulations", type=int, default=RESEARCH_N_SIMULATIONS)
    args = parser.parse_args()

    seasons_cache = load_cache(args.cache_dir)
    tables = build_feature_tables(seasons_cache)
    all_seasons = sorted(seasons_cache)
    games_raw, advanced_raw = raw_shapes(args.cache_dir, all_seasons)
    lines, skipped = build_team_game_lines(games_raw, advanced_raw, captured_at=datetime.now(UTC))
    print(f"team-game lines: {len(lines)} built, {len(skipped)} skipped")

    fields = [
        "game_id", "season", "week", "home", "away", "neutral", "actual_margin", "actual_total",
        "ctrl_margin", "ctrl_total", "ctrl_p_home_sim", "ctrl_p_home_closed",
        "talent_delta", "v050_margin", "v050_p_home_closed",
        "home_sd", "away_sd", "corr", "margin_sd", "total_sd", "history_rows",
    ]
    args.out.parent.mkdir(parents=True, exist_ok=True)
    started = time.perf_counter()
    n_rows = 0
    with args.out.open("w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=fields)
        writer.writeheader()
        for season in args.seasons:
            cache = seasons_cache[season]
            table = tables[season]
            target = AsOf(season=season, week=1)
            by_week: dict[int, object] = {}
            for game in cache.fbs_games:
                if game.week not in by_week:
                    by_week[game.week] = build_fit(lines, AsOf(season=season, week=game.week))
                fit = by_week[game.week]
                if fit is None:
                    continue
                raw = project_game(
                    home_id=game.home_team, away_id=game.away_team,
                    home_classification=game.home_classification, away_classification=game.away_classification,
                    is_neutral_site=game.neutral_site, ratings=fit.ratings, prior_season_ratings=None,
                    residual_pool=fit.residual_pool, home_percent_passing_ppa=None, away_percent_passing_ppa=None,
                    n_simulations=args.n_simulations, seed=RESEARCH_SEED,
                )
                ht = table.get(game.home_team, "talent_composite", target=target)
                at = table.get(game.away_team, "talent_composite", target=target)
                tdelta = talent_margin_delta(ht.value if ht else None, at.value if at else None)
                ctrl = apply_margin_correction(
                    raw, is_fbs_vs_fbs=True, method="linear", correction_model=FROZEN_MARGIN_CORRECTION_PARAMS,
                    artifact_version=MARGIN_CORRECTION_ARTIFACT_VERSION, as_of=fit.as_of,
                    training_cutoff=MARGIN_CORRECTION_TRAINING_CUTOFF, talent_margin_delta=0.0,
                )
                v050 = apply_margin_correction(
                    raw, is_fbs_vs_fbs=True, method="linear", correction_model=FROZEN_MARGIN_CORRECTION_PARAMS,
                    artifact_version=MARGIN_CORRECTION_ARTIFACT_VERSION, as_of=fit.as_of,
                    training_cutoff=MARGIN_CORRECTION_TRAINING_CUTOFF, talent_margin_delta=tdelta,
                )
                dist_c = ctrl.to_game_distribution()
                dist_t = v050.to_game_distribution()
                md_c = margin_distribution(dist_c)
                md_t = margin_distribution(dist_t)
                tot_var = (
                    dist_c.home_sd**2 + dist_c.away_sd**2 + 2 * dist_c.correlation * dist_c.home_sd * dist_c.away_sd
                )
                writer.writerow({
                    "game_id": game.game_id, "season": season, "week": game.week,
                    "home": game.home_team, "away": game.away_team, "neutral": int(game.neutral_site),
                    "actual_margin": game.home_margin, "actual_total": game.total_points,
                    "ctrl_margin": round(dist_c.home_mean - dist_c.away_mean, 4),
                    "ctrl_total": round(dist_c.home_mean + dist_c.away_mean, 4),
                    "ctrl_p_home_sim": round(ctrl.prob_home_win(), 5),
                    "ctrl_p_home_closed": round(prob_greater_than(md_c, 0.0), 5),
                    "talent_delta": round(tdelta, 4),
                    "v050_margin": round(dist_t.home_mean - dist_t.away_mean, 4),
                    "v050_p_home_closed": round(prob_greater_than(md_t, 0.0), 5),
                    "home_sd": round(dist_c.home_sd, 4), "away_sd": round(dist_c.away_sd, 4),
                    "corr": round(dist_c.correlation, 5),
                    "margin_sd": round(md_c.stdev, 4), "total_sd": round(float(np.sqrt(max(tot_var, 1e-6))), 4),
                    "history_rows": fit.history_rows,
                })
                n_rows += 1
            print(f"season {season} done: {n_rows} rows total ({time.perf_counter() - started:.0f}s)", flush=True)
    print(f"wrote {n_rows} rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
