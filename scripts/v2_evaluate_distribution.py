"""Phase 9-10: uncertainty and contract-probability evaluation for a V2
margin model + total model pair, against the reproduced 0.4.0 / 0.5.0
control on the SAME games.

    python3 scripts/v2_evaluate_distribution.py \
        --dataset data/research_cache/v2_work/dataset.parquet \
        --preds-dir data/research_cache/v2_work/tournament/preds \
        --margin-model full_lgbm --total-model tot_full_lgbm \
        --control data/research_cache/v2_work/control_predictions.csv \
        --out data/research_cache/v2_work/distribution_eval.json

For every test season Y the conditional-scale model (uncertainty.py) is
fit on out-of-sample residuals from seasons < Y only. Contract
probabilities are then priced on the synthetic half-point grid (same
thresholds as the model-repair spec), game-equal weighted, and compared
to the deployed pricer's probabilities for the control on common games.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import norm

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.v2.features import matchup_frame  # noqa: E402
from cfb_edge_finder.research.v2.metrics import (  # noqa: E402
    SPREAD_THRESHOLDS,
    TOTAL_THRESHOLDS,
    build_contracts,
    calibration_summary,
    interval_coverage,
    paired_delta,
    winner_metrics,
)
from cfb_edge_finder.research.v2.uncertainty import fit_scale_model, prob_greater  # noqa: E402

SCALE_COLS_MARGIN = ["abs_pred_margin", "early_w", "fcs_involved", "pred_total_level"]
SCALE_COLS_TOTAL = ["pred_total_level", "early_w", "abs_pred_margin"]


def contracts_for(pred_m, sd_m, pred_t, sd_t, actual_m, actual_t, model_m=None, model_t=None, method="normal"):
    return build_contracts(
        spread_prob_fn=lambda T: prob_greater(pred_m, sd_m, T, model_m, method),
        total_prob_fn=lambda T: prob_greater(pred_t, sd_t, T, model_t, method),
        actual_margin=actual_m,
        actual_total=actual_t,
        spread_thresholds=SPREAD_THRESHOLDS,
        total_thresholds=TOTAL_THRESHOLDS,
    )


def control_contracts(c: pd.DataFrame, margin_col: str):
    """The DEPLOYED pricer: Normal on (mean, sd) with a 0.5 continuity
    correction applied on top of the half-point threshold (the live
    behaviour, see the Phase 1 audit)."""
    m = c[margin_col].values
    sdm = c.margin_sd.values
    t = c.ctrl_total.values
    sdt = c.total_sd.values
    return build_contracts(
        spread_prob_fn=lambda T: 1 - norm.cdf((T + 0.5 - m) / sdm),
        total_prob_fn=lambda T: 1 - norm.cdf((T + 0.5 - t) / sdt),
        actual_margin=c.actual_margin.values,
        actual_total=c.actual_total.values,
    )


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dataset", type=Path, required=True)
    ap.add_argument("--preds-dir", type=Path, required=True)
    ap.add_argument("--margin-model", required=True)
    ap.add_argument("--total-model", required=True)
    ap.add_argument("--control", type=Path, required=True)
    ap.add_argument("--out", type=Path, required=True)
    ap.add_argument("--method", default="normal", choices=["normal", "t", "empirical"])
    args = ap.parse_args()

    df = pd.read_parquet(args.dataset)
    X = matchup_frame(df)
    pm = pd.read_parquet(args.preds_dir / f"{args.margin_model}.parquet")[["game_id", "pred_margin"]]
    pt = pd.read_parquet(args.preds_dir / f"{args.total_model}.parquet")[["game_id", "pred_total"]]
    d = (
        df[["game_id", "season", "week", "margin", "total", "home_won", "neutral", "postseason", "both_fbs"]]
        .merge(pm, on="game_id")
        .merge(pt, on="game_id")
    )
    d = d[d.both_fbs].copy()
    d["early_w"] = d.game_id.map(dict(zip(df.game_id, X["early_w"], strict=True)))
    d["fcs_involved"] = 0.0
    d["abs_pred_margin"] = d.pred_margin.abs()
    d["pred_total_level"] = d.pred_total
    d["res_m"] = d.margin - d.pred_margin
    d["res_t"] = d.total - d.pred_total
    seasons = sorted(d.season.unique())

    out: dict = {
        "margin_model": args.margin_model,
        "total_model": args.total_model,
        "method": args.method,
        "by_season": {},
        "scale_models": {},
    }
    d["sd_m"] = np.nan
    d["sd_t"] = np.nan
    d["sd_m_const"] = np.nan
    d["sd_t_const"] = np.nan
    for y in seasons:
        hist = d[d.season < y]
        if len(hist) < 300:
            continue
        sm = fit_scale_model(hist.res_m.values, hist, SCALE_COLS_MARGIN)
        st = fit_scale_model(hist.res_t.values, hist, SCALE_COLS_TOTAL)
        cur = d.season == y
        d.loc[cur, "sd_m"] = sm.sd(d[cur])
        d.loc[cur, "sd_t"] = st.sd(d[cur])
        d.loc[cur, "sd_m_const"] = float(np.std(hist.res_m))
        d.loc[cur, "sd_t_const"] = float(np.std(hist.res_t))
        out["scale_models"][str(y)] = {
            "margin": {
                "coef": dict(zip(["const"] + SCALE_COLS_MARGIN, np.round(sm.coef, 5), strict=True)),
                "t_df": sm.df_t,
            },
            "total": {
                "coef": dict(zip(["const"] + SCALE_COLS_TOTAL, np.round(st.coef, 5), strict=True)),
                "t_df": st.df_t,
            },
        }
        e = d[cur]
        cs = contracts_for(
            e.pred_margin.values,
            e.sd_m.values,
            e.pred_total.values,
            e.sd_t.values,
            e.margin.values,
            e.total.values,
            sm,
            st,
            args.method,
        )
        cs_const = contracts_for(
            e.pred_margin.values,
            e.sd_m_const.values,
            e.pred_total.values,
            e.sd_t_const.values,
            e.margin.values,
            e.total.values,
        )
        spread = cs.subset(cs.family == "spread")
        total = cs.subset(cs.family == "total")
        p_home = prob_greater(e.pred_margin.values, e.sd_m.values, 0.0, sm, args.method)
        out["by_season"][str(y)] = {
            "n_games": int(len(e)),
            "resid_sd_margin": round(float(e.res_m.std()), 3),
            "resid_sd_total": round(float(e.res_t.std()), 3),
            "mean_sd_margin": round(float(e.sd_m.mean()), 3),
            "mean_sd_total": round(float(e.sd_t.mean()), 3),
            "coverage90_margin": round(interval_coverage(e.pred_margin.values, e.margin.values, e.sd_m.values), 4),
            "coverage90_total": round(interval_coverage(e.pred_total.values, e.total.values, e.sd_t.values), 4),
            "spread": calibration_summary(spread).to_dict(),
            "total": calibration_summary(total).to_dict(),
            "all_const_sd_brier": calibration_summary(cs_const).to_dict()["brier"],
            "winner": winner_metrics(p_home, e.home_won.values).to_dict(),
        }
    # pooled calibration (seasons with a scale model)
    ev = d[d.sd_m.notna()]
    cs = contracts_for(
        ev.pred_margin.values, ev.sd_m.values, ev.pred_total.values, ev.sd_t.values, ev.margin.values, ev.total.values
    )
    out["pooled"] = {
        "seasons": [int(s) for s in sorted(ev.season.unique())],
        "spread": calibration_summary(cs.subset(cs.family == "spread")).to_dict(),
        "total": calibration_summary(cs.subset(cs.family == "total")).to_dict(),
    }
    for seg, mask in (("week_1", ev.week <= 1), ("weeks_4_plus", (ev.week >= 4) & ~ev.postseason.astype(bool))):
        e = ev[mask]
        c2 = contracts_for(
            e.pred_margin.values, e.sd_m.values, e.pred_total.values, e.sd_t.values, e.margin.values, e.total.values
        )
        out["pooled"][seg] = {
            "spread": calibration_summary(c2.subset(c2.family == "spread")).to_dict(),
            "total": calibration_summary(c2.subset(c2.family == "total")).to_dict(),
        }

    # ---- control comparison on common games
    c = pd.read_csv(args.control, dtype={"game_id": str})
    common = ev.merge(c, on="game_id", suffixes=("", "_c"))
    out["control_comparison"] = {
        "n_common": int(len(common)),
        "seasons": [int(s) for s in sorted(common.season.unique())],
    }
    if len(common):
        v2 = contracts_for(
            common.pred_margin.values,
            common.sd_m.values,
            common.pred_total.values,
            common.sd_t.values,
            common.margin.values,
            common.total.values,
        )
        c040 = control_contracts(common, "ctrl_margin")
        c050 = control_contracts(common, "v050_margin")
        for fam in ("spread", "total"):
            mask = v2.family == fam
            a, b40, b50 = v2.subset(mask), c040.subset(mask), c050.subset(mask)
            out["control_comparison"][fam] = {
                "v2": calibration_summary(a).to_dict(),
                "ctrl_0_4_0": calibration_summary(b40).to_dict(),
                "ctrl_0_5_0": calibration_summary(b50).to_dict(),
                "brier_delta_v2_minus_050": paired_delta(
                    (b50.prob - b50.hit) ** 2, (a.prob - a.hit) ** 2, a.game_idx, a.weight
                ),
                "brier_delta_v2_minus_040": paired_delta(
                    (b40.prob - b40.hit) ** 2, (a.prob - a.hit) ** 2, a.game_idx, a.weight
                ),
            }
        for seg, mask in (
            ("week_1", common.week <= 1),
            ("weeks_4_plus", (common.week >= 4) & ~common.postseason.astype(bool)),
        ):
            e = common[mask]
            v2s = contracts_for(
                e.pred_margin.values, e.sd_m.values, e.pred_total.values, e.sd_t.values, e.margin.values, e.total.values
            )
            c50s = control_contracts(e, "v050_margin")
            out["control_comparison"][seg] = {}
            for fam in ("spread", "total"):
                m2 = v2s.family == fam
                a, b = v2s.subset(m2), c50s.subset(m2)
                out["control_comparison"][seg][fam] = {
                    "v2_brier": calibration_summary(a).to_dict()["brier"],
                    "ctrl_0_5_0_brier": calibration_summary(b).to_dict()["brier"],
                    "delta": paired_delta((b.prob - b.hit) ** 2, (a.prob - a.hit) ** 2, a.game_idx, a.weight),
                }
        # point-estimate paired deltas
        gid = np.arange(len(common))
        out["control_comparison"]["margin_mae_delta_v2_minus_050"] = paired_delta(
            np.abs(common.v050_margin - common.margin), np.abs(common.pred_margin - common.margin), gid
        )
        out["control_comparison"]["total_mae_delta_v2_minus_050"] = paired_delta(
            np.abs(common.ctrl_total - common.total), np.abs(common.pred_total - common.total), gid
        )
        p_v2 = prob_greater(common.pred_margin.values, common.sd_m.values, 0.0)
        out["control_comparison"]["winner"] = {
            "v2": winner_metrics(p_v2, common.home_won.values).to_dict(),
            "ctrl_0_5_0_closed": winner_metrics(common.v050_p_home_closed.values, common.home_won.values).to_dict(),
            "ctrl_0_4_0_sim": winner_metrics(common.ctrl_p_home_sim.values, common.home_won.values).to_dict(),
            "log_loss_delta_v2_minus_050": paired_delta(
                -(
                    common.home_won * np.log(np.clip(common.v050_p_home_closed, 1e-9, 1 - 1e-9))
                    + (1 - common.home_won) * np.log(np.clip(1 - common.v050_p_home_closed, 1e-9, 1 - 1e-9))
                ),
                -(
                    common.home_won * np.log(np.clip(p_v2, 1e-9, 1 - 1e-9))
                    + (1 - common.home_won) * np.log(np.clip(1 - p_v2, 1e-9, 1 - 1e-9))
                ),
                gid,
            ),
        }
        for y, e in common.groupby("season"):
            out["control_comparison"][f"season_{int(y)}"] = {
                "n": int(len(e)),
                "margin_mae_v2": round(float(np.abs(e.pred_margin - e.margin).mean()), 3),
                "margin_mae_050": round(float(np.abs(e.v050_margin - e.margin).mean()), 3),
                "wk1_margin_mae_v2": round(float(np.abs(e[e.week <= 1].pred_margin - e[e.week <= 1].margin).mean()), 3),
                "wk1_margin_mae_050": round(
                    float(np.abs(e[e.week <= 1].v050_margin - e[e.week <= 1].margin).mean()), 3
                ),
                "total_mae_v2": round(float(np.abs(e.pred_total - e.total).mean()), 3),
                "total_mae_ctrl": round(float(np.abs(e.ctrl_total - e.total).mean()), 3),
            }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(out, indent=2, default=float))
    print(json.dumps({k: out[k] for k in ("pooled", "control_comparison")}, indent=1, default=float)[:6000])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
