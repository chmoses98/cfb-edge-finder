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
    # Round 2: training recency, FCS rows in training, stacking, total variants
    2: [
        Candidate("struct_pre_ridge_d85", "ridge", "margin", "struct+pre", {"train_decay": 0.85}),
        Candidate("struct_pre_ridge_d70", "ridge", "margin", "struct+pre", {"train_decay": 0.70}),
        Candidate("struct_pre_ridge_allrows", "ridge", "margin", "struct+pre", train_fbs_only=False),
        Candidate("eff_pre_ridge_d85", "ridge", "margin", "eff+pre", {"train_decay": 0.85}),
        Candidate("full_ridge_d85", "ridge", "margin", "full", {"train_decay": 0.85}),
        Candidate("full_stack_d85", "ridge_lgbm", "margin", "full", {"train_decay": 0.85}),
        Candidate("struct_pre_stack", "ridge_lgbm", "margin", "struct+pre"),
        Candidate("full_lgbm_small", "lgbm", "margin", "full", {"num_leaves": 7, "min_child_samples": 80,
                                                                  "learning_rate": 0.01, "train_decay": 0.85}),
        Candidate("tot_eff_ridge_d85", "ridge", "total", "tot_eff", {"train_decay": 0.85}),
        Candidate("tot_eff_ridge_d70", "ridge", "total", "tot_eff", {"train_decay": 0.70}),
        Candidate("tot_struct_ridge_d85", "ridge", "total", "tot_struct", {"train_decay": 0.85}),
        Candidate("tot_full_ridge_d85", "ridge", "total", "tot_full", {"train_decay": 0.85}),
        Candidate("tot_full_lgbm_d85", "lgbm", "total", "tot_full", {"train_decay": 0.85}),
        Candidate("tot_full_stack_d85", "ridge_lgbm", "total", "tot_full", {"train_decay": 0.85}),
        Candidate("tot_eff_ridge_allrows_d85", "ridge", "total", "tot_eff", {"train_decay": 0.85},
                  train_fbs_only=False),
        Candidate("points_full_ridge_d85", "points_ridge", "points", "full", {"train_decay": 0.85}),
    ],
    # Round 3: preseason-family ablations on the best linear margin model (Phase 4)
    3: [
        Candidate(f"abl_{fam}", "ridge", "margin", f"struct+pre-no_{fam}")
        for fam in ("talent", "returning", "coaching", "prev_strength", "sp_prev", "poll", "fbs_new")
    ] + [
        Candidate("abl_early", "ridge", "margin", "struct+pre-no_early"),
        Candidate("abl_only_talent_prev", "ridge", "margin", "struct+pre-only_talent_prev"),
        Candidate("struct_pre_elo_ridge", "ridge", "margin", "struct+pre+elo"),
        Candidate("struct_pre_k2_ridge", "ridge", "margin", "struct+pre_k2"),
        Candidate("struct_pre_k8_ridge", "ridge", "margin", "struct+pre_k8"),
    ],
    # Round 4: state-config dataset variants (run with --dataset dataset_<tag>.parquet --suffix _<tag>)
    4: [
        Candidate("struct_pre_ridge", "ridge", "margin", "struct+pre"),
        Candidate("tot_eff_ridge", "ridge", "total", "tot_eff"),
        Candidate("eff_pre_ridge", "ridge", "margin", "eff+pre"),
    ],
    # Round 5: long-memory strength features (dataset rebuilt with long_decay)
    5: [
        Candidate("struct_pre_long_ridge", "ridge", "margin", "struct+pre+long"),
        Candidate("struct_pre_long_slim_ridge", "ridge", "margin", "struct+pre+long-no_sp_poll"),
        Candidate("tot_eff_long_ridge", "ridge", "total", "tot_eff+long"),
        Candidate("struct_pre_long_lgbm", "lgbm", "margin", "struct+pre+long"),
    ],
}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", type=Path, required=True)
    parser.add_argument("--out-dir", type=Path, required=True)
    parser.add_argument("--round", type=int, default=1)
    parser.add_argument("--only", nargs="*", default=None)
    parser.add_argument("--suffix", default="", help="appended to candidate names (e.g. a dataset variant tag)")
    args = parser.parse_args()

    df = pd.read_parquet(args.dataset)
    meta = json.load(open(str(args.dataset) + ".meta.json"))
    meta.pop("features", None)
    cands = ROUNDS[args.round]
    if args.only:
        cands = [c for c in cands if c.name in args.only]
    if args.suffix:
        for c in cands:
            c.name = f"{c.name}{args.suffix}"
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
