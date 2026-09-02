"""Phase 13: explainability and failure analysis for a candidate's
out-of-sample predictions.

    python3 scripts/v2_failure_analysis.py --dataset ... --preds-dir ... \
        --margin-model full_lgbm --total-model tot_full_lgbm --out analysis.json

Reports residual slices (season, week bucket, conference, favourite size,
projected total level, neutral, rest), the largest errors, and feature
importance for a refit of the same model on all seasons < 2025
(gain-based for LightGBM, standardised coefficients for ridge).
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.v2.features import FEATURE_SETS, matchup_frame  # noqa: E402


def slice_table(d: pd.DataFrame, key: str, col: str = "res_m") -> list[dict]:
    rows = []
    for k, g in d.groupby(key):
        rows.append(
            {
                key: (k if not isinstance(k, np.generic) else k.item()),
                "n": int(len(g)),
                "mae": round(float(g[col].abs().mean()), 3),
                "bias": round(float(g[col].mean()), 3),
            }
        )
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--preds-dir", type=Path, required=True)
    ap.add_argument("--margin-model", required=True)
    ap.add_argument("--total-model", required=True)
    ap.add_argument("--feature-set", default="full")
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()

    df = pd.read_parquet(args.dataset)
    pm = pd.read_parquet(args.preds_dir / f"{args.margin_model}.parquet")[["game_id", "pred_margin"]]
    pt = pd.read_parquet(args.preds_dir / f"{args.total_model}.parquet")[["game_id", "pred_total"]]
    d = df.merge(pm, on="game_id").merge(pt, on="game_id")
    d = d[d.both_fbs].copy()
    d["res_m"] = d.pred_margin - d.margin  # projected - actual
    d["res_t"] = d.pred_total - d.total
    d["week_bucket"] = pd.cut(d.week, [-1, 1, 3, 8, 15, 30], labels=["wk1", "wk2-3", "wk4-8", "wk9-15", "post"])
    d["fav_bucket"] = pd.cut(
        d.pred_margin.abs(), [-1, 3, 7, 14, 21, 28, 99], labels=["0-3", "3-7", "7-14", "14-21", "21-28", "28+"]
    )
    d["signed_fav_err"] = np.sign(d.pred_margin) * d.res_m
    d["tot_bucket"] = pd.cut(d.pred_total, [0, 45, 52, 58, 64, 200], labels=["<45", "45-52", "52-58", "58-64", "64+"])
    d["conf_pair"] = d.home_conf.fillna("?") + "|" + d.away_conf.fillna("?")
    out: dict = {"margin_model": args.margin_model, "total_model": args.total_model, "n": int(len(d))}
    out["by_season"] = slice_table(d, "season")
    out["by_week_bucket"] = slice_table(d, "week_bucket")
    out["by_fav_bucket"] = [
        {**r, "signed_fav_bias": round(float(d[d.fav_bucket == r["fav_bucket"]].signed_fav_err.mean()), 3)}
        for r in slice_table(d, "fav_bucket")
    ]
    out["total_by_level"] = slice_table(d, "tot_bucket", "res_t")
    out["total_by_week_bucket"] = slice_table(d, "week_bucket", "res_t")
    out["by_neutral"] = slice_table(d, "neutral")
    out["by_conference_game"] = slice_table(d, "conference_game")
    out["by_home_conf"] = sorted(slice_table(d, "home_conf"), key=lambda r: -r["n"])[:15]
    # team residuals (home perspective sign-corrected: positive = team over-projected)
    team_rows = []
    for t in sorted(set(d.home) | set(d.away)):
        h = d[d.home == t]
        a = d[d.away == t]
        r = np.concatenate([h.res_m.values, -a.res_m.values])
        if len(r) >= 20:
            team_rows.append(
                {
                    "team": t,
                    "n": int(len(r)),
                    "bias": round(float(r.mean()), 3),
                    "mae": round(float(np.abs(r).mean()), 3),
                }
            )
    team_rows.sort(key=lambda r: r["bias"])
    out["teams_most_under_projected"] = team_rows[:10]
    out["teams_most_over_projected"] = team_rows[-10:][::-1]
    worst = d.reindex(d.res_m.abs().sort_values(ascending=False).index).head(15)
    out["largest_margin_errors"] = [
        {
            "season": int(r.season),
            "week": int(r.week),
            "home": r.home,
            "away": r.away,
            "pred": round(float(r.pred_margin), 1),
            "actual": int(r.margin),
        }
        for r in worst.itertuples()
    ]
    worst_t = d.reindex(d.res_t.abs().sort_values(ascending=False).index).head(10)
    out["largest_total_errors"] = [
        {
            "season": int(r.season),
            "week": int(r.week),
            "home": r.home,
            "away": r.away,
            "pred": round(float(r.pred_total), 1),
            "actual": int(r.total),
        }
        for r in worst_t.itertuples()
    ]
    # feature importance: refit on seasons < 2025 (development), report top features
    X = matchup_frame(df)
    cols = FEATURE_SETS[args.feature_set]
    tr = df.both_fbs & df.completed & (df.season < 2025)
    M = X.loc[tr, cols].astype(float)
    M = M.fillna(M.median()).fillna(0.0)
    y = df.loc[tr, "margin"].values
    try:
        import lightgbm as lgb

        m = lgb.LGBMRegressor(
            n_estimators=600,
            learning_rate=0.02,
            num_leaves=15,
            min_child_samples=40,
            subsample=0.8,
            subsample_freq=1,
            colsample_bytree=0.8,
            reg_lambda=5.0,
            verbosity=-1,
            random_state=7,
        ).fit(M.values, y)
        gain = m.booster_.feature_importance(importance_type="gain")
        imp = sorted(zip(cols, gain, strict=True), key=lambda kv: -kv[1])
        tot = sum(gain) or 1.0
        out["lgbm_gain_top25"] = [{"feature": f, "share": round(float(g / tot), 4)} for f, g in imp[:25]]
    except Exception as exc:  # noqa: BLE001
        out["lgbm_gain_top25"] = f"unavailable: {exc}"
    Z = (M - M.mean()) / (M.std() + 1e-9)
    A = Z.values.T @ Z.values + 100.0 * np.eye(Z.shape[1])
    coef = np.linalg.solve(A, Z.values.T @ (y - y.mean()))
    ridge = sorted(zip(cols, coef, strict=True), key=lambda kv: -abs(kv[1]))
    out["ridge_std_coef_top25"] = [{"feature": f, "coef": round(float(c), 3)} for f, c in ridge[:25]]
    args.out.write_text(json.dumps(out, indent=2, default=float))
    print(
        json.dumps(
            {
                k: out[k]
                for k in (
                    "by_week_bucket",
                    "by_fav_bucket",
                    "total_by_level",
                    "teams_most_under_projected",
                    "teams_most_over_projected",
                    "largest_margin_errors",
                    "lgbm_gain_top25",
                )
            },
            indent=1,
            default=float,
        )[:5000]
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
