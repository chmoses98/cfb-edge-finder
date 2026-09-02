"""Render the experiment registry as markdown leaderboards (Phase 7/15).

    python3 scripts/v2_leaderboard.py --registry data/research_cache/v2_work/tournament/registry.jsonl

Prints a pooled leaderboard plus per-season margin and total MAE tables.
Duplicate candidate names keep the LAST run (re-runs supersede)."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.v2.tournament import leaderboard_row  # noqa: E402


def load(registry: Path) -> pd.DataFrame:
    rows = {}
    for line in registry.open():
        r = json.loads(line)
        name = r["candidate"]["name"]
        row = leaderboard_row(name, r["metrics"])
        row["model"] = r["candidate"].get("model")
        row["target"] = r["candidate"].get("target")
        row["feature_set"] = r["candidate"].get("feature_set", "")
        row["n_features"] = r.get("n_features")
        row["runtime_s"] = r.get("runtime_s")
        row["spec_hash"] = r.get("spec_hash", "")
        row["dataset"] = (r.get("dataset") or {}).get("feature_hash", "")
        rows[name] = row
    return pd.DataFrame(list(rows.values()))


def md(df: pd.DataFrame, cols: list[str], digits: int = 3) -> str:
    d = df[cols].copy()
    for c in cols:
        if d[c].dtype.kind == "f":
            d[c] = d[c].map(lambda v: "" if pd.isna(v) else f"{v:.{digits}f}")
    head = "| " + " | ".join(cols) + " |\n|" + "---|" * len(cols) + "\n"
    body = "\n".join("| " + " | ".join(str(v) for v in row) + " |" for row in d.values.tolist())
    return head + body


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--registry", type=Path, required=True)
    ap.add_argument("--filter", default=None, help="substring filter on candidate name")
    args = ap.parse_args()
    lb = load(args.registry)
    if args.filter:
        lb = lb[lb.candidate.str.contains(args.filter)]
    margin = lb[lb.margin_mae.notna()].sort_values("margin_mae")
    total = lb[lb.total_mae.notna()].sort_values("total_mae")
    winner = lb[lb.winner_log_loss.notna()].sort_values("winner_log_loss")
    print("### Margin candidates (pooled 2017-2025 test seasons, FBS-vs-FBS)\n")
    print(md(margin, ["candidate", "model", "feature_set", "n_features", "margin_mae", "margin_rmse", "margin_bias",
                      "margin_fav_tail_bias", "wk1_margin_mae", "wk4p_margin_mae", "winner_log_loss", "runtime_s"]))
    print("\n### Margin MAE by test season\n")
    print(md(margin, ["candidate"] + [c for c in margin.columns if c.startswith("m_mae_")], 2))
    print("\n### Total candidates\n")
    print(md(total, ["candidate", "model", "feature_set", "n_features", "total_mae", "total_bias", "wk1_total_mae",
                     "wk4p_total_mae", "runtime_s"]))
    print("\n### Total MAE by test season\n")
    print(md(total, ["candidate"] + [c for c in total.columns if c.startswith("t_mae_")], 2))
    print("\n### Winner probability\n")
    print(md(winner, ["candidate", "winner_log_loss", "winner_brier"], 4))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
