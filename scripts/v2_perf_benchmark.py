"""Phase 18: quantify V2 training / inference cost for the candidate
production spec (ridge on struct+pre features over a walk-forward team
state).

    python3 scripts/v2_perf_benchmark.py --cache-dir data/research_cache/v2 \
        --dataset data/research_cache/v2_work/dataset.parquet --feature-set struct+pre

Reports: full dataset rebuild time (already measured by the build script),
one as-of state fit time, ridge training time on all completed FBS-vs-FBS
games, per-game and 100-game-slate inference time from a cached state,
and the serialized artifact size (state tables + ridge coefficients).
"""

from __future__ import annotations

import argparse
import json
import pickle
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.v2.cache import V2Cache  # noqa: E402
from cfb_edge_finder.research.v2.dataset import STATE_METRICS, games_frame  # noqa: E402
from cfb_edge_finder.research.v2.features import FEATURE_SETS, matchup_frame  # noqa: E402
from cfb_edge_finder.research.v2.state import StateConfig, fit_state, side_rows  # noqa: E402
from cfb_edge_finder.research.v2.teamgames import build_team_game_table  # noqa: E402
from cfb_edge_finder.research.v2.tournament import _Ridge  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cache-dir", type=Path, required=True)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--feature-set", default="struct+pre")
    ap.add_argument("--alpha", type=float, default=100.0)
    args = ap.parse_args()
    out: dict = {}

    cache = V2Cache(args.cache_dir)
    seasons = list(range(2014, 2026))
    t0 = time.perf_counter()
    games = games_frame(cache, seasons, current_season=None)
    tg = build_team_game_table(cache, seasons)
    long = side_rows(
        games[games.completed], tg, [m for m in STATE_METRICS if m in tg.columns or m in ("pts_for", "margin")]
    )
    out["load_history_seconds"] = round(time.perf_counter() - t0, 2)
    metrics = [m for m in STATE_METRICS if m in long.columns]
    t0 = time.perf_counter()
    state = fit_state(long, metrics, cutoff_season=2026, cutoff_week=1, cfg=StateConfig())
    out["state_fit_seconds_one_asof"] = round(time.perf_counter() - t0, 3)
    out["state_teams"] = len(state.teams)

    df = pd.read_parquet(args.dataset)
    X = matchup_frame(df)
    cols = FEATURE_SETS[args.feature_set]
    tr = (df.both_fbs & df.completed).values
    M = X.loc[tr, cols].astype(float)
    fill = M.median()
    M = M.fillna(fill).fillna(0.0)
    t0 = time.perf_counter()
    model = _Ridge(args.alpha).fit(M.values, df.loc[tr, "margin"].values)
    out["ridge_train_seconds"] = round(time.perf_counter() - t0, 4)
    out["ridge_train_rows"] = int(tr.sum())
    out["n_features"] = len(cols)

    te = (df.season == 2026).values
    Mte = X.loc[te, cols].astype(float).fillna(fill).fillna(0.0).values
    t0 = time.perf_counter()
    for _ in range(100):
        model.predict(Mte[:1])
    out["inference_seconds_per_game"] = round((time.perf_counter() - t0) / 100, 6)
    t0 = time.perf_counter()
    model.predict(Mte[:100])
    out["inference_seconds_100_game_slate"] = round(time.perf_counter() - t0, 6)
    # feature construction for a slate (from cached dataset rows) is the dominant cost
    t0 = time.perf_counter()
    matchup_frame(df[te].head(100))
    out["feature_build_seconds_100_game_slate"] = round(time.perf_counter() - t0, 4)

    artifact = {
        "state_offense": state.offense.to_dict(),
        "state_defense": state.defense.to_dict(),
        "state_mu": state.mu.to_dict(),
        "state_hfa": state.hfa.to_dict(),
        "ridge": {
            "coef": model.coef_.tolist(),
            "mean": model.mean_.tolist(),
            "std": model.std_.tolist(),
            "y_mean": model.y_mean_,
            "alpha": args.alpha,
            "features": cols,
            "fill": fill.to_dict(),
        },
    }
    blob = pickle.dumps(artifact)
    out["artifact_bytes_pickle"] = len(blob)
    out["artifact_bytes_json"] = len(json.dumps(artifact).encode())
    print(json.dumps(out, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
