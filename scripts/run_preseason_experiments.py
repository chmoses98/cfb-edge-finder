#!/usr/bin/env python3
"""Run CONTROL and candidate ablations over the historical research cache.

Reads only the cache. Never calls CFBD, never touches production.

Stage 1 reproduces the frozen CONTROL. If the control cannot be
reproduced the run stops there: comparing candidates against a control
that does not work would be meaningless.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import defaultdict
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from cfb_edge_finder.modeling.corpus import build_team_game_lines  # noqa: E402
from cfb_edge_finder.modeling.leakage import AsOf  # noqa: E402
from cfb_edge_finder.research.preseason.ablation import (  # noqa: E402
    WALK_FORWARD_SPLIT,
    assert_control_unchanged,
)
from cfb_edge_finder.research.preseason.candidates import (  # noqa: E402
    CANDIDATES,
    apply_candidate,
    fit_beta,
)
from cfb_edge_finder.research.preseason.control import control_manifest  # noqa: E402
from cfb_edge_finder.research.preseason.corpus import (  # noqa: E402
    build_feature_tables,
    load_cache,
    summarize,
)
from cfb_edge_finder.research.preseason.evaluation import (  # noqa: E402
    margin_metrics,
    paired_comparison,  # noqa: E402
    total_metrics,
    winner_metrics,
)
from cfb_edge_finder.research.preseason.experiment import (  # noqa: E402
    RESEARCH_N_SIMULATIONS,
    build_fit,
    control_projection,
    segment,
    to_prediction,
)


def raw_shapes(cache_dir: Path, seasons: list[int]) -> tuple[list[dict], list[dict]]:
    """Rebuild the raw /games and /stats/game/advanced shapes that
    `build_team_game_lines` expects, from the compact cache."""
    games: list[dict] = []
    advanced: list[dict] = []
    for season in seasons:
        payload = json.loads((cache_dir / f"{season}.json").read_text())
        for g in payload["games"]:
            games.append({
                k: g.get(k)
                for k in (
                    "id", "season", "week", "seasonType", "startDate", "neutralSite",
                    "conferenceGame", "homeTeam", "awayTeam", "homePoints", "awayPoints",
                    "homeClassification", "awayClassification", "completed",
                )
            })
        for a in payload.get("advanced", []):
            advanced.append({
                "gameId": a.get("gameId"), "season": a.get("season"), "week": a.get("week"),
                "team": a.get("team"), "opponent": a.get("opponent"),
                "offense": {"plays": a.get("plays")},
            })
    return games, advanced


def report_segment(label: str, preds: list) -> dict:
    if not preds:
        return {"segment": label, "n": 0}
    w, m, t = winner_metrics(preds), margin_metrics(preds), total_metrics(preds)
    print(
        f"    {label:14} n={w.n:>5}  LL={w.log_loss:.4f}  Brier={w.brier:.4f}  "
        f"marginMAE={m.mae:6.2f}  RMSE={m.rmse:6.2f}  bias={m.bias:+6.2f}  "
        f"favTail={m.favorite_tail_bias:+6.2f}  totMAE={t.mae:6.2f}  totBias={t.bias:+6.2f}"
    )
    return {
        "segment": label, "n": w.n, "log_loss": w.log_loss, "brier": w.brier,
        "margin_mae": m.mae, "margin_rmse": m.rmse, "margin_bias": m.bias,
        "favorite_tail_bias": m.favorite_tail_bias,
        "total_mae": t.mae, "total_rmse": t.rmse, "total_bias": t.bias,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cache-dir", type=Path, required=True)
    parser.add_argument("--max-week", type=int, default=15)
    parser.add_argument("--n-simulations", type=int, default=RESEARCH_N_SIMULATIONS)
    parser.add_argument("--json-out", type=Path, default=None)
    args = parser.parse_args()

    assert_control_unchanged()
    manifest = control_manifest()
    seasons_cache = load_cache(args.cache_dir)
    tables = build_feature_tables(seasons_cache)
    corpus = summarize(seasons_cache)

    print("=" * 92)
    print("PRESEASON EXPERIMENTS -- STAGE 1: REPRODUCE THE FROZEN CONTROL")
    print("=" * 92)
    print(f"  control hash      : {manifest.content_hash()}")
    print(f"  control version   : {manifest.model_version}")
    print(f"  simulations/game  : {args.n_simulations} (research setting; production is "
          f"{manifest.payload['simulation']['n_simulations']})")
    print(f"  corpus            : {corpus.to_dict()}")

    seasons = sorted(seasons_cache)
    games_raw, advanced_raw = raw_shapes(args.cache_dir, seasons)
    lines, skipped = build_team_game_lines(games_raw, advanced_raw, captured_at=datetime.now(UTC))
    print(f"  team-game lines   : {len(lines)} built, {len(skipped)} skipped")

    evaluable = [s for s in seasons if any(x < s for x in seasons)]
    print(f"  evaluable seasons : {evaluable} "
          f"(the earliest cached season has no prior history to fit on)")

    all_preds: list = []
    per_season: dict[int, list] = defaultdict(list)
    margin_samples: dict[str, object] = {}
    differentials: dict[str, dict[str, float | None]] = {c.name: {} for c in CANDIDATES}
    started = time.perf_counter()

    for season in evaluable:
        cache = seasons_cache[season]
        table = tables[season]
        target = AsOf(season=season, week=1)
        by_week: dict[int, object] = {}
        season_games = [g for g in cache.fbs_games if g.week <= args.max_week]
        for game in season_games:
            week_as_of = AsOf(season=season, week=game.week)
            if game.week not in by_week:
                by_week[game.week] = build_fit(lines, week_as_of)
            fit = by_week[game.week]
            if fit is None:
                continue
            home_f = table.get(game.home_team, "returning_percentPassingPPA", target=target)
            away_f = table.get(game.away_team, "returning_percentPassingPPA", target=target)
            projection = control_projection(
                game, fit,
                home_passing_ppa=home_f.value if home_f else None,
                away_passing_ppa=away_f.value if away_f else None,
                n_simulations=args.n_simulations,
            )
            pred = to_prediction(game, projection)
            # CorrectedGameProjection wraps the raw simulation and adds a
            # margin_delta; the corrected margin distribution is the raw
            # one shifted by that delta.
            margins = (
                projection.raw.home_scores - projection.raw.away_scores + projection.margin_delta
            )
            all_preds.append(pred)
            per_season[season].append(pred)
            margin_samples[pred.game_id] = margins
            for spec in CANDIDATES:
                differentials[spec.name][pred.game_id] = spec.differential(game, table, target)
        print(f"  season {season}: {len(per_season[season])} FBS-vs-FBS games projected "
              f"({time.perf_counter() - started:.0f}s elapsed)")

    print(f"\n  CONTROL BASELINE (FBS-vs-FBS, seasons {evaluable})")
    results = {}
    for name in ("week_1", "weeks_2_3", "weeks_1_3", "weeks_4_plus", "neutral_site"):
        results[name] = report_segment(name, segment(all_preds, name))

    print("\n  CONTROL WEEK 1 BY SEASON")
    per_season_week1 = {}
    for season in evaluable:
        per_season_week1[season] = report_segment(f"{season} wk1", segment(per_season[season], "week_1"))

    reproduced = results["week_1"].get("n", 0) > 0 and results["weeks_4_plus"].get("n", 0) > 0
    print(f"\n  CONTROL REPRODUCED: {reproduced}")
    if not reproduced:
        print("  Stopping: candidates cannot be compared against a control that did not run.")

    # ---------------------------------------------- STAGE 2: ABLATIONS
    candidate_results = {}
    if reproduced:
        dev = tuple(s for s in WALK_FORWARD_SPLIT.development_seasons if s in evaluable)
        sel = WALK_FORWARD_SPLIT.selection_season
        conf = WALK_FORWARD_SPLIT.confirmation_season
        print("\n" + "=" * 92)
        print("STAGE 2: INDIVIDUAL CANDIDATE ABLATIONS (one family at a time)")
        print("=" * 92)
        print(f"  development : {list(dev)}   selection : {sel}   confirmation : {conf}")
        if set(dev) != set(WALK_FORWARD_SPLIT.development_seasons):
            missing = sorted(set(WALK_FORWARD_SPLIT.development_seasons) - set(dev))
            print(f"  DEVIATION   : declared development seasons {missing} are not evaluable")
            print("                (no prior season cached to fit ratings on). Disclosed, not hidden.")

        by_season = {s: per_season[s] for s in evaluable}
        for spec in CANDIDATES:
            diffs = differentials[spec.name]
            dev_rows = [(p, diffs.get(p.game_id)) for s in dev for p in by_season[s]
                        if p.both_fbs and diffs.get(p.game_id) is not None]
            fitted = fit_beta(spec, dev_rows, development_seasons=dev)
            if fitted is None:
                print(f"\n  {spec.name}: INSUFFICIENT DEVELOPMENT COVERAGE -- no beta fit")
                candidate_results[spec.name] = {"verdict": "INSUFFICIENT_COVERAGE"}
                continue

            entry = {
                "beta": fitted.beta,
                "development_n": fitted.n_games,
                "development_seasons": list(fitted.development_seasons),
                "mean_abs_differential": fitted.mean_abs_differential,
                "typical_points_effect": fitted.beta * fitted.mean_abs_differential,
                "segments": {},
            }
            print(f"\n  {spec.name}")
            print(f"    beta={fitted.beta:+.3f} pts per unit differential "
                  f"(dev n={fitted.n_games}, mean|diff|={fitted.mean_abs_differential:.3f} "
                  f"-> typical effect {fitted.beta * fitted.mean_abs_differential:+.2f} pts)")

            for phase, phase_seasons in (("development", dev), ("selection", (sel,)), ("confirmation", (conf,))):
                phase_preds = [p for s in phase_seasons if s in by_season for p in by_season[s]]
                for seg_name in ("week_1", "weeks_1_3", "weeks_4_plus"):
                    ctrl = segment(phase_preds, seg_name)
                    ctrl = [p for p in ctrl if diffs.get(p.game_id) is not None]
                    if len(ctrl) < 20:
                        continue
                    cand = [apply_candidate(p, diffs.get(p.game_id), fitted, margin_samples[p.game_id])
                            for p in ctrl]
                    cmp_margin = paired_comparison(
                        metric="margin_abs_error",
                        control_errors=[abs(p.projected_margin - p.actual_home_margin) for p in ctrl],
                        candidate_errors=[abs(p.projected_margin - p.actual_home_margin) for p in cand],
                    )
                    import math as _m
                    cmp_ll = paired_comparison(
                        metric="winner_log_loss",
                        control_errors=[-_m.log(max(min(p.home_win_probability,1-1e-12),1e-12) if p.home_won
                                                 else max(min(1-p.home_win_probability,1-1e-12),1e-12)) for p in ctrl],
                        candidate_errors=[-_m.log(max(min(p.home_win_probability,1-1e-12),1e-12) if p.home_won
                                                  else max(min(1-p.home_win_probability,1-1e-12),1e-12)) for p in cand],
                    )
                    key = f"{phase}:{seg_name}"
                    entry["segments"][key] = {
                        "n": cmp_margin.n_games,
                        "margin_mae_control": cmp_margin.control,
                        "margin_mae_candidate": cmp_margin.candidate,
                        "margin_mae_diff": cmp_margin.mean_paired_difference,
                        "margin_ci": [cmp_margin.ci_low, cmp_margin.ci_high],
                        "margin_improves": cmp_margin.improves,
                        "margin_degrades": cmp_margin.degrades,
                        "logloss_control": cmp_ll.control,
                        "logloss_candidate": cmp_ll.candidate,
                        "logloss_diff": cmp_ll.mean_paired_difference,
                        "logloss_improves": cmp_ll.improves,
                    }
                    flag = "IMPROVES" if cmp_margin.improves else ("DEGRADES" if cmp_margin.degrades else "flat")
                    print(f"      {key:26} n={cmp_margin.n_games:>5} "
                          f"marginMAE {cmp_margin.control:6.2f} -> {cmp_margin.candidate:6.2f} "
                          f"(dMAE {cmp_margin.mean_paired_difference:+.3f} "
                          f"CI[{cmp_margin.ci_low:+.3f},{cmp_margin.ci_high:+.3f}]) "
                          f"dLL {cmp_ll.mean_paired_difference:+.4f}  {flag}")
            candidate_results[spec.name] = entry

    if args.json_out:
        payload = {
            "control": manifest.to_dict(),
            "n_simulations": args.n_simulations,
            "corpus": corpus.to_dict(),
            "team_game_lines": len(lines),
            "skipped_rows": len(skipped),
            "evaluable_seasons": evaluable,
            "walk_forward_split": {
                "development": list(WALK_FORWARD_SPLIT.development_seasons),
                "selection": WALK_FORWARD_SPLIT.selection_season,
                "confirmation": WALK_FORWARD_SPLIT.confirmation_season,
                "excluded": list(WALK_FORWARD_SPLIT.excluded_seasons),
            },
            "control_baseline": results,
            "control_week1_by_season": per_season_week1,
            "control_reproduced": reproduced,
            "candidates": candidate_results,
        }
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        args.json_out.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
        print(f"\n  wrote {args.json_out}")
    return 0 if reproduced else 1


if __name__ == "__main__":
    raise SystemExit(main())
