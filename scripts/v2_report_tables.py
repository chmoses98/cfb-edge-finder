"""Render markdown tables for the V2 research report from persisted
evaluation artifacts (distribution/calibration JSON, failure-analysis
JSON). Nothing here recomputes a metric.

    python3 scripts/v2_report_tables.py --dist data/research_cache/v2_work/dist_eval.json \
        --failure data/research_cache/v2_work/failure.json
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path


def table(rows: list[dict], cols: list[str], fmt: dict | None = None) -> str:
    fmt = fmt or {}
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    out = []
    for r in rows:
        cells = []
        for c in cols:
            v = r.get(c)
            if isinstance(v, float):
                cells.append(f"{v:.{fmt.get(c, 3)}f}")
            elif v is None:
                cells.append("")
            else:
                cells.append(str(v))
        out.append("| " + " | ".join(cells) + " |")
    return head + "\n".join(out)


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dist", type=Path, required=True)
    ap.add_argument("--failure", type=Path, required=True)
    args = ap.parse_args()
    d = json.loads(args.dist.read_text())
    f = json.loads(args.failure.read_text())

    print(f"Margin model `{d['margin_model']}`, total model `{d['total_model']}`, pricing method `{d['method']}`.\n")
    print("#### Uncertainty by test season (conditional-scale model fit on prior out-of-sample residuals)\n")
    rows = []
    for s, b in d["by_season"].items():
        rows.append(
            {
                "season": s,
                "n": b["n_games"],
                "resid sd margin": b["resid_sd_margin"],
                "model sd margin": b["mean_sd_margin"],
                "cov90 margin": b["coverage90_margin"],
                "resid sd total": b["resid_sd_total"],
                "model sd total": b["mean_sd_total"],
                "cov90 total": b["coverage90_total"],
                "spread Brier": b["spread"]["brier"],
                "spread ECE": b["spread"]["ece"],
                "total Brier": b["total"]["brier"],
                "total ECE": b["total"]["ece"],
                "winner LL": b["winner"]["log_loss"],
            }
        )
    print(
        table(
            rows,
            list(rows[0].keys()),
            {
                "cov90 margin": 3,
                "cov90 total": 3,
                "spread Brier": 4,
                "total Brier": 4,
                "spread ECE": 4,
                "total ECE": 4,
                "winner LL": 4,
            },
        )
    )
    sm = d["scale_models"][max(d["scale_models"])]
    print(
        f"\nLatest scale model (fit for {max(d['scale_models'])}): margin log-sd coefficients "
        f"{sm['margin']['coef']} (Student-t df: {sm['margin']['t_df']}); total {sm['total']['coef']} "
        f"(df: {sm['total']['t_df']}).\n"
    )
    for fam in ("spread", "total"):
        p = d["pooled"][fam]
        print(
            f"#### {fam.title()} contract calibration, pooled {d['pooled']['seasons'][0]}-{d['pooled']['seasons'][-1]} "
            f"({p['n_contracts']:,} contracts / {p['n_games']:,} games, game-equal weighted)\n"
        )
        print(
            f"Brier {p['brier']:.4f}, log loss {p['log_loss']:.4f}, ECE {p['ece']:.4f}; "
            f"90-95% events hit {p['hit_rate_90_95']:.3f}, 95%+ events hit {p['hit_rate_95_plus']:.3f}.\n"
        )
        print(table(p["bins"], ["bin", "n", "predicted", "observed", "gap"], {"predicted": 4, "observed": 4, "gap": 4}))
        print()
        for seg in ("week_1", "weeks_4_plus"):
            q = d["pooled"][seg][fam]
            print(
                f"- {seg}: Brier {q['brier']:.4f}, ECE {q['ece']:.4f}, 95%+ hit "
                f"{q['hit_rate_95_plus'] if q['hit_rate_95_plus'] is not None else 'n/a'}"
            )
        print()
    cc = d["control_comparison"]
    print(f"#### V2 vs 0.5.0 on the {cc['n_common']:,} common games ({cc['seasons'][0]}-{cc['seasons'][-1]})\n")
    rows = []
    for fam in ("spread", "total"):
        x = cc[fam]
        rows.append(
            {
                "family": fam,
                "V2 Brier": x["v2"]["brier"],
                "0.5.0 Brier": x["ctrl_0_5_0"]["brier"],
                "0.4.0 Brier": x["ctrl_0_4_0"]["brier"],
                "V2 ECE": x["v2"]["ece"],
                "0.5.0 ECE": x["ctrl_0_5_0"]["ece"],
                "V2 95%+ hit": x["v2"]["hit_rate_95_plus"],
                "0.5.0 95%+ hit": x["ctrl_0_5_0"]["hit_rate_95_plus"],
                "delta Brier (V2-0.5.0)": f"{x['brier_delta_v2_minus_050']['delta']:+.4f} "
                f"[{x['brier_delta_v2_minus_050']['ci95'][0]:+.4f}, {x['brier_delta_v2_minus_050']['ci95'][1]:+.4f}] "
                f"{x['brier_delta_v2_minus_050']['verdict']}",
            }
        )
    print(
        table(
            rows,
            list(rows[0].keys()),
            {
                "V2 Brier": 4,
                "0.5.0 Brier": 4,
                "0.4.0 Brier": 4,
                "V2 ECE": 4,
                "0.5.0 ECE": 4,
                "V2 95%+ hit": 3,
                "0.5.0 95%+ hit": 3,
            },
        )
    )
    print()
    for seg in ("week_1", "weeks_4_plus"):
        for fam in ("spread", "total"):
            x = cc[seg][fam]
            print(
                f"- {seg} {fam}: V2 {x['v2_brier']:.4f} vs 0.5.0 {x['ctrl_0_5_0_brier']:.4f}, delta "
                f"{x['delta']['delta']:+.4f} [{x['delta']['ci95'][0]:+.4f}, {x['delta']['ci95'][1]:+.4f}] "
                f"{x['delta']['verdict']}"
            )
    print()
    m = cc["margin_mae_delta_v2_minus_050"]
    t = cc["total_mae_delta_v2_minus_050"]
    w = cc["winner"]
    print(f"- margin MAE delta V2−0.5.0: {m['delta']:+.3f} [{m['ci95'][0]:+.3f}, {m['ci95'][1]:+.3f}] ({m['verdict']})")
    print(f"- total MAE delta V2−0.5.0: {t['delta']:+.3f} [{t['ci95'][0]:+.3f}, {t['ci95'][1]:+.3f}] ({t['verdict']})")
    print(
        f"- winner log loss: V2 {w['v2']['log_loss']:.4f} (Brier {w['v2']['brier']:.4f}) vs 0.5.0 closed-form "
        f"{w['ctrl_0_5_0_closed']['log_loss']:.4f} (Brier {w['ctrl_0_5_0_closed']['brier']:.4f}) vs 0.4.0 simulated "
        f"{w['ctrl_0_4_0_sim']['log_loss']:.4f}; delta {w['log_loss_delta_v2_minus_050']['delta']:+.4f} "
        f"[{w['log_loss_delta_v2_minus_050']['ci95'][0]:+.4f}, {w['log_loss_delta_v2_minus_050']['ci95'][1]:+.4f}]\n"
    )
    rows = [{"season": k.split("_")[1], **v} for k, v in cc.items() if k.startswith("season_")]
    print(
        table(
            rows,
            [
                "season",
                "n",
                "margin_mae_v2",
                "margin_mae_050",
                "wk1_margin_mae_v2",
                "wk1_margin_mae_050",
                "total_mae_v2",
                "total_mae_ctrl",
            ],
            {
                c: 2
                for c in (
                    "margin_mae_v2",
                    "margin_mae_050",
                    "wk1_margin_mae_v2",
                    "wk1_margin_mae_050",
                    "total_mae_v2",
                    "total_mae_ctrl",
                )
            },
        )
    )
    print("\n#### Failure analysis\n")
    print("Margin residual (projected − actual) by week bucket:\n")
    print(table(f["by_week_bucket"], ["week_bucket", "n", "mae", "bias"], {"mae": 2, "bias": 2}))
    print("\nBy projected favourite size (signed bias > 0 = favourite over-projected):\n")
    print(
        table(
            f["by_fav_bucket"],
            ["fav_bucket", "n", "mae", "bias", "signed_fav_bias"],
            {"mae": 2, "bias": 2, "signed_fav_bias": 2},
        )
    )
    print("\nTotal residual by projected total level:\n")
    print(table(f["total_by_level"], ["tot_bucket", "n", "mae", "bias"], {"mae": 2, "bias": 2}))
    print("\nTeams most under-projected (negative = team did better than projected) / over-projected:\n")
    print(
        table(
            f["teams_most_under_projected"][:6] + f["teams_most_over_projected"][:6],
            ["team", "n", "bias", "mae"],
            {"bias": 2, "mae": 2},
        )
    )
    print("\nLargest margin errors:\n")
    print(table(f["largest_margin_errors"][:8], ["season", "week", "home", "away", "pred", "actual"], {"pred": 1}))
    print("\nTop ridge standardised coefficients (refit on seasons < 2025):\n")
    print(table(f["ridge_std_coef_top25"][:12], ["feature", "coef"], {"coef": 3}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
