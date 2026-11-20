#!/usr/bin/env python3
"""Walk-forward study of the experimental venue-only home effect (RESEARCH_ONLY, nothing activated).

    python3 scripts/research_home_effect.py --cfbd-root <research-data>/data/research_cache/v2 \\
        --espn-log data/football/2026/team_games.jsonl --espn-schedule data/football/2026/schedule.json \\
        --as-of 2026-11-20T00:00:00Z --out data/scripting/validation/home_effect_experimental

Protocol (fixed before any 2024-2026 error was computed):
  * every game is predicted from games that FINISHED before its data cutoff (gamelog.before), plus the
    end-of-season state of the PREVIOUS season (never the current one);
  * the incumbent is `adjust.fit_metric(points_per_game)` with its default config, evaluated on exactly the
    same games as every candidate (games where the incumbent publishes a baseline);
  * the config grid is scored on DEVELOPMENT seasons 2015-2023 only; the single best config by margin RMSE
    is frozen, then scored once on VALIDATION 2024-2025 and TEST 2026 (2026 weeks 6+ were never inspected).
No market price is read.
"""

from __future__ import annotations

import os

for _v in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS"):
    os.environ.setdefault(_v, "1")

import argparse  # noqa: E402
import gzip  # noqa: E402
import json  # noqa: E402
import sys  # noqa: E402
from concurrent.futures import ProcessPoolExecutor  # noqa: E402
from pathlib import Path  # noqa: E402

import numpy as np  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from cfb_edge_finder.scripting import home_effect_experimental as HE  # noqa: E402
from cfb_edge_finder.scripting.adjust import fit_metric  # noqa: E402
from cfb_edge_finder.scripting.baseline_eval import targets_from_rows  # noqa: E402
from cfb_edge_finder.scripting.gamelog import before, data_cutoff  # noqa: E402

DEV = list(range(2015, 2024))
VAL = [2024, 2025]
TEST = [2026]
GRID = [HE.HomeEffectConfig(home_prior_games=k, carryover=c) for c in (0.0, 0.3, 0.5, 0.7) for k in (1.0, 30.0, 100.0, 300.0)]
ARGS: argparse.Namespace


def load(season: int):
    from validate_scoring_baseline import load_cfbd, load_espn

    if season == 2026:
        rows, meta, _ = load_espn(Path(ARGS.espn_log), Path(ARGS.espn_schedule), ARGS.as_of)
    else:
        rows, meta, _ = load_cfbd(Path(ARGS.cfbd_root) / str(season))
    return rows, meta


def run_season(season: int, configs: list[HE.HomeEffectConfig]) -> list[dict]:
    rows, meta = load(season)
    prev_rows, _ = load(season - 1)
    targets, _ = targets_from_rows(rows, meta)
    start = min(r.kickoff_utc for r in rows)
    prior = HE.season_prior(prev_rows, start)
    inc_cache: dict[frozenset, object] = {}
    exp_cache: dict[tuple, object] = {}
    out = []
    for t in targets:
        cutoff = data_cutoff(t.kickoff_utc)
        hist = before(rows, cutoff)
        key = frozenset(r.game_id for r in hist)
        if key not in inc_cache:
            inc_cache[key] = fit_metric(hist, "points_per_game", cutoff)
        f = inc_cache[key]
        if not f.adjusted or f.mu is None:
            continue
        e = [f.get(t.home_id, "offense"), f.get(t.away_id, "defense"), f.get(t.away_id, "offense"), f.get(t.home_id, "defense")]
        if any(x is None or x.adjusted is None for x in e):
            continue
        x = 0.0 if t.neutral_site else 1.0
        h = f.home_effect or 0.0
        home = max(e[0].adjusted + e[1].adjusted - f.mu + h * x, 0.0)
        away = max(e[2].adjusted + e[3].adjusted - f.mu - h * x, 0.0)
        prior_games = min(sum(1 for r in hist if r.team_id == t.home_id), sum(1 for r in hist if r.team_id == t.away_id))
        rec = {
            "season": season, "week": t.week, "game_id": t.game_id, "neutral": t.neutral_site, "prior_games_min": prior_games,
            "actual_margin": t.home_points - t.away_points, "actual_total": t.home_points + t.away_points,
            "fcs": t.home_division != "fbs" or t.away_division != "fbs",
            "inc_margin": home - away, "inc_total": home + away, "inc_2h": 2 * h * x, "cand": {},
        }
        for i, cfg in enumerate(configs):
            k2 = (key, i)
            if k2 not in exp_cache:
                exp_cache[k2] = HE.fit_points(hist, cutoff, prior, cfg, enabled=True)
            m = HE.scoring_margin(exp_cache[k2], t.home_id, t.away_id, t.neutral_site)
            rec["cand"][i] = None if m["home_margin"] is None else [m["home_margin"], m["total_points"], 2 * (m["home_effect_applied"] or 0.0)]
        out.append(rec)
    return out


def stats(recs: list[dict], col) -> dict:
    p = np.array([col(r)[0] for r in recs]); a = np.array([r["actual_margin"] for r in recs])
    tp = np.array([col(r)[1] for r in recs]); ta = np.array([r["actual_total"] for r in recs])
    hx = np.array([col(r)[2] for r in recs]); e = p - a
    site = np.array([not r["neutral"] for r in recs])
    return {
        "n": len(recs), "margin_rmse": round(float(np.sqrt(np.mean(e**2))), 3), "margin_mae": round(float(np.mean(np.abs(e))), 3),
        "margin_bias_home_site": round(float(e[site].mean()), 3) if site.any() else None,
        "winner_acc": round(float(np.mean((p > 0) == (a > 0))), 4), "total_mae": round(float(np.mean(np.abs(tp - ta))), 3),
        "mean_2h_home_site": round(float(hx[site].mean()), 3) if site.any() else None,
    }


def table(recs: list[dict], col) -> dict:
    groups = {"all": recs, "weeks_1_5": [r for r in recs if (r["week"] or 0) <= 5], "weeks_6_plus": [r for r in recs if (r["week"] or 0) >= 6]}
    return {k: stats(v, col) for k, v in groups.items() if v}


def paired(recs: list[dict], i: int) -> dict:
    """Game-level paired difference in squared margin error, candidate minus incumbent, with SE."""
    d = np.array([(r["cand"][i][0] - r["actual_margin"]) ** 2 - (r["inc_margin"] - r["actual_margin"]) ** 2 for r in recs])
    return {"n": len(d), "mean_sq_err_diff": round(float(d.mean()), 3), "se": round(float(d.std(ddof=1) / np.sqrt(len(d))), 3)}


def main() -> None:
    global ARGS
    ap = argparse.ArgumentParser()
    ap.add_argument("--cfbd-root", required=True); ap.add_argument("--espn-log", required=True); ap.add_argument("--espn-schedule", required=True)
    ap.add_argument("--as-of", required=True); ap.add_argument("--out", required=True); ap.add_argument("--workers", type=int, default=4)
    ARGS = ap.parse_args()
    seasons = DEV + VAL + TEST
    with ProcessPoolExecutor(ARGS.workers, initializer=_init, initargs=(ARGS,)) as pool:
        recs = {s: r for s, r in zip(seasons, pool.map(run_season, seasons, [GRID] * len(seasons)))}
    common = lambda ss: [r for s in ss for r in recs[s] if all(r["cand"][i] is not None for i in range(len(GRID)))]
    dev = common(DEV)
    inc = lambda r: (r["inc_margin"], r["inc_total"], r["inc_2h"])
    cand = lambda i: (lambda r: r["cand"][i])
    grid_dev = [{"config": c.describe(), **stats(dev, cand(i))} for i, c in enumerate(GRID)]
    best = min(range(len(GRID)), key=lambda i: (grid_dev[i]["margin_rmse"], GRID[i].carryover, GRID[i].home_prior_games))
    a_only = min((i for i, c in enumerate(GRID) if c.carryover == 0.0), key=lambda i: grid_dev[i]["margin_rmse"])
    report = {
        "label": "RESEARCH_ONLY", "version": HE.VERSION,
        "protocol": {"development": DEV, "validation": VAL, "test": TEST, "selection": "min margin RMSE on development games common to all configs"},
        "incumbent_config_check": GRID[0].describe(),
        "development_grid": grid_dev,
        "selected": {"index": best, "config": GRID[best].describe()},
        "selected_home_prior_only": {"index": a_only, "config": GRID[a_only].describe()},
        "by_split": {},
        "by_season_weeks_1_5_mean_2h": {},
    }
    for name, ss in (("development", DEV), ("validation", VAL), ("test_2026", TEST)):
        rr = common(ss)
        report["by_split"][name] = {
            "incumbent": table(rr, inc), "candidate_selected": table(rr, cand(best)), "candidate_home_prior_only": table(rr, cand(a_only)),
            "paired_selected_vs_incumbent": {k: paired(v, best) for k, v in (("all", rr), ("weeks_1_5", [r for r in rr if (r["week"] or 0) <= 5]), ("weeks_6_plus", [r for r in rr if (r["week"] or 0) >= 6])) if v},
            "paired_home_prior_only_vs_incumbent": {k: paired(v, a_only) for k, v in (("all", rr), ("weeks_1_5", [r for r in rr if (r["week"] or 0) <= 5])) if v},
        }
    for s in seasons:
        rr = [r for r in common([s]) if (r["week"] or 0) <= 5 and not r["neutral"]]
        if rr:
            report["by_season_weeks_1_5_mean_2h"][s] = {"incumbent": round(float(np.mean([r["inc_2h"] for r in rr])), 2), "selected": round(float(np.mean([r["cand"][best][2] for r in rr])), 2), "n": len(rr)}
    out = Path(ARGS.out); out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(report, indent=1) + "\n")
    with gzip.open(out / "records.jsonl.gz", "wt") as fh:
        for s in seasons:
            for r in recs[s]:
                fh.write(json.dumps({**{k: v for k, v in r.items() if k != "cand"}, "selected": r["cand"][best], "home_prior_only": r["cand"][a_only]}) + "\n")
    print(json.dumps({k: report[k] for k in ("selected", "selected_home_prior_only", "by_split", "by_season_weeks_1_5_mean_2h")}, indent=1))


def _init(a: argparse.Namespace) -> None:
    global ARGS
    ARGS = a


if __name__ == "__main__":
    main()
