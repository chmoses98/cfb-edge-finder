"""Build the V2 historical game-level modeling table from the V2 research
cache and write it as parquet with a provenance sidecar.

    python3 scripts/v2_build_dataset.py --cache-dir data/research_cache/v2 \
        --out data/research_cache/v2_work/dataset.parquet

Nothing here calls CFBD. 2026 outcomes are never read (see
research/v2/dataset.py FIREWALL).
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.research.v2.cache import V2Cache  # noqa: E402
from cfb_edge_finder.research.v2.dataset import build_dataset, save_dataset  # noqa: E402
from cfb_edge_finder.research.v2.preseason import verify_poll_timing  # noqa: E402
from cfb_edge_finder.research.v2.state import StateConfig  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--seasons", type=int, nargs="+", default=list(range(2014, 2026)))
    parser.add_argument("--current-season", type=int, default=2026)
    parser.add_argument("--season-decay", type=float, default=0.5)
    parser.add_argument("--lam", type=float, default=6.0)
    parser.add_argument("--min-eval-season", type=int, default=2015)
    args = parser.parse_args()

    cache = V2Cache(args.cache_dir)
    for s in (2019, 2024):
        print("poll timing check:", verify_poll_timing(cache, s))
    t0 = time.perf_counter()
    build = build_dataset(
        cache, seasons=args.seasons, current_season=args.current_season,
        state_cfg=StateConfig(season_decay=args.season_decay, lam=args.lam),
        min_eval_season=args.min_eval_season,
    )
    args.out.parent.mkdir(parents=True, exist_ok=True)
    save_dataset(build, args.out)
    print(f"wrote {len(build.games)} games x {len(build.games.columns)} cols ({len(build.features)} features) "
          f"to {args.out} in {time.perf_counter() - t0:.0f}s; meta={build.meta}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
