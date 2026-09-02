"""Phase 12: chronological ensembles over persisted tournament predictions.

    python3 scripts/v2_ensemble.py --dataset ... --preds-dir ... \
        --members struct_pre_ridge full_elo_ridge eff_pre_lgbm --target margin --name ens_margin_v1

For test season Y the member weights are fit (non-negative, sum-to-one
least squares) on the members' OUT-OF-SAMPLE predictions from seasons
< Y only, then applied to season Y. The first evaluable season uses an
equal-weight average. Output is a prediction parquet in the same format
as tournament candidates plus a registry line, so ensembles compete on
identical terms.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.optimize import nnls
from scipy.stats import norm

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.v2.tournament import evaluate, leaderboard_row  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--preds-dir", type=Path, required=True)
    ap.add_argument("--members", nargs="+", required=True)
    ap.add_argument("--target", choices=["margin", "total"], default="margin")
    ap.add_argument("--name", required=True)
    ap.add_argument("--equal", action="store_true", help="equal weights everywhere (no learned weights)")
    ap.add_argument(
        "--affine",
        action="store_true",
        help="fit y = a + b*blend chronologically on prior OOS seasons (shrinkage / level recalibration)",
    )
    args = ap.parse_args()

    df = pd.read_parquet(args.dataset)
    col = f"pred_{args.target}"
    frames = []
    for m in args.members:
        p = pd.read_parquet(args.preds_dir / f"{m}.parquet")[["game_id", "season", "week", col]]
        frames.append(p.rename(columns={col: m}))
    P = frames[0]
    for f in frames[1:]:
        P = P.merge(f, on=["game_id", "season", "week"], how="inner")
    P = P.merge(df[["game_id", args.target]], on="game_id")
    y = P[args.target].values
    M = P[args.members].values
    P["pred"] = np.nan
    weights_by_season = {}
    t0 = time.perf_counter()
    for s in sorted(P.season.unique()):
        hist = P.season < s
        cur = P.season == s
        if args.equal or hist.sum() < 300:
            w = np.full(len(args.members), 1.0 / len(args.members))
        else:
            w, _ = nnls(M[hist], y[hist])
            if w.sum() <= 0:
                w = np.full(len(args.members), 1.0 / len(args.members))
            w = w / w.sum()
        blend = M @ w
        if args.affine and hist.sum() >= 300:
            A = np.column_stack([np.ones(hist.sum()), blend[hist]])
            ab, *_ = np.linalg.lstsq(A, y[hist], rcond=None)
            w = np.concatenate([w, ab])
            P.loc[cur, "pred"] = ab[0] + ab[1] * blend[cur]
        else:
            P.loc[cur, "pred"] = blend[cur]
        weights_by_season[int(s)] = [round(float(x), 4) for x in w]
    out = pd.DataFrame({"game_id": P.game_id, "season": P.season, "week": P.week})
    if args.target == "margin":
        out["pred_margin"] = P["pred"].values
        out["pred_total"] = np.nan
        # winner probability from a chronological residual sd
        sd = np.full(len(P), np.nan)
        for s in sorted(P.season.unique()):
            hist = (P.season < s).values & P["pred"].notna().values
            if hist.sum() >= 300:
                sd[(P.season == s).values] = float(np.std(y[hist] - P["pred"].values[hist]))
        out["margin_sd"] = sd
        out["p_home"] = 1 - norm.cdf(-out["pred_margin"] / out["margin_sd"])
    else:
        out["pred_total"] = P["pred"].values
        out["pred_margin"] = np.nan
        out["margin_sd"] = np.nan
        out["p_home"] = np.nan
    ev = evaluate(out, df)
    rec = {
        "candidate": {
            "name": args.name,
            "model": "ensemble_nnls" if not args.equal else "ensemble_equal",
            "target": args.target,
            "members": args.members,
        },
        "weights_by_season": weights_by_season,
        "metrics": ev,
        "runtime_s": round(time.perf_counter() - t0, 1),
        "ran_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    with (args.preds_dir.parent / "registry.jsonl").open("a") as reg:
        reg.write(json.dumps(rec, default=float) + "\n")
    out.to_parquet(args.preds_dir / f"{args.name}.parquet", index=False)
    row = leaderboard_row(args.name, ev)
    print(
        {
            k: (round(v, 4) if isinstance(v, float) else v)
            for k, v in row.items()
            if not k.startswith(("m_mae_", "t_mae_"))
        }
    )
    print("weights by season:", weights_by_season)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
