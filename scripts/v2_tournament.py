"""Run a declared round of the V2 model tournament.

    python3 scripts/v2_tournament.py --dataset data/research_cache/v2_work/dataset.parquet \
        --out-dir data/research_cache/v2_work/tournament --round 1

Rounds are declared in code (below) so the candidate list is part of the
record. Every run appends to registry.jsonl; nothing is deleted.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

import pandas as pd

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.v2.tournament import Candidate, run_tournament  # noqa: E402

ROUNDS: dict[int, list[Candidate]] = {
    1: [
        Candidate("zero", "zero", "margin", "struct"),
        Candidate("market_close", "market", "margin", "struct"),
        Candidate("elo_ridge", "ridge", "margin", "elo_only"),
        Candidate("struct_ridge", "ridge", "margin", "struct"),
        Candidate("struct_pre_ridge", "ridge", "margin", "struct+pre"),
        Candidate("eff_ridge", "ridge", "margin", "eff"),
        Candidate("eff_pre_ridge", "ridge", "margin", "eff+pre"),
        Candidate("eff_pre_sit_ridge", "ridge", "margin", "eff+pre+sit"),
        Candidate("full_ridge", "ridge", "margin", "full"),
        Candidate("full_elo_ridge", "ridge", "margin", "full+elo"),
        Candidate("eff_pre_lgbm", "lgbm", "margin", "eff+pre"),
        Candidate("full_lgbm", "lgbm", "margin", "full"),
        Candidate("full_lgbm_l1", "lgbm", "margin", "full", {"objective": "l1"}),
        Candidate("full_elo_lgbm", "lgbm", "margin", "full+elo"),
        Candidate("points_full_ridge", "points_ridge", "points", "full"),
        Candidate("points_full_lgbm", "points_lgbm", "points", "full"),
        Candidate("tot_struct_ridge", "ridge", "total", "tot_struct"),
        Candidate("tot_eff_ridge", "ridge", "total", "tot_eff"),
        Candidate("tot_eff_pre_ridge", "ridge", "total", "tot_eff+pre"),
        Candidate("tot_full_ridge", "ridge", "total", "tot_full"),
        Candidate("tot_full_lgbm", "lgbm", "total", "tot_full"),
        Candidate("tot_eff_lgbm", "lgbm", "total", "tot_eff"),
        Candidate("win_full_logit", "logit", "winner", "full"),
        Candidate("win_full_lgbm", "lgbm_clf", "winner", "full"),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--only", nargs="*", default=None)
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset)
    meta = json.load(open(str(args.dataset) + ".meta.json"))
    meta.pop("features", None)
    cands = ROUNDS[args.round]
    if args.only:
        cands = [c for c in cands if c.name in args.only]
    t0 = time.perf_counter()
    lb = run_tournament(cands, df, args.out_dir, dataset_meta=meta)
    lb.to_csv(args.out_dir / f"leaderboard_round{args.round}.csv", index=False)
    pd.set_option("display.width", 250)
    pd.set_option("display.max_columns", 40)
    show = [c for c in lb.columns if not c.startswith(("m_mae_", "t_mae_"))]
    print(lb[show].round(4).to_string(index=False))
    print(f"round {args.round} done in {time.perf_counter() - t0:.0f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
