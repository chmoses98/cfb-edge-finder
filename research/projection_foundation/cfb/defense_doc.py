"""Writes DEFENSE_RATINGS.md (contract + validation tables) from results/defense_ratings_validation.json.

    python -m cfb.defense_doc
"""
from __future__ import annotations

import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.dirname(HERE))

from cfb import paths  # noqa: E402


def ci(c):
    return f"{c['mean_diff']:+.3f} [{c['ci95'][0]:+.3f}, {c['ci95'][1]:+.3f}]"


def main():
    v = json.load(open(paths.RESULTS / "defense_ratings_validation.json"))
    pub, conf = v["published_rating"], v["conference_offsets"]
    L = []
    A = L.append
    A("# CFB defensive ratings - shadow publication and validation")
    A("")
    A("*Research only (`authority: research_only`, `role: shadow`). The production script engine is unchanged; this file is published to the "
      "`projection-data` branch as `ratings/cfb/defense_latest.json` NEXT TO the script engine's own current rank for every team.*")
    A("")
    A("## What is published")
    A("")
    A(f"`ratings/cfb/defense_latest.json` (`sift.defense_ratings.v1`), rewritten by every live run when its content changes (`defense_<UTC>.json` copies "
      f"are kept). One row per FBS defence ({'conference offsets ON' if pub['conference_offsets'] else 'no conference offsets'}; ridge alpha {pub['alpha']}"
      f"{', conference alpha ' + str(pub['conf_alpha']) if pub['conference_offsets'] else ''}, prior-season weight {pub['decay']} per season back over {pub['history']} seasons, "
      f"every non-FBS opponent pooled into one FCS entity).")
    A("")
    A("| field | meaning |")
    A("|---|---|")
    rows = [
        ("team_id, team", "ESPN team id and display name"),
        ("rating", "points per game allowed against an average offence, relative to the FBS-average defence (negative = better)"),
        ("se", "posterior SD of that rating (ridge posterior, same construction the production engine uses)"),
        ("rank", "1 = fewest points allowed; **null while rank_status is SUPPRESSED**"),
        ("rank_point, rank_lo, rank_hi", "point rank and the 80% posterior rank interval (always published, also when `rank` is withheld)"),
        ("p_top10, p_top25", "posterior probability of a top-10 / top-25 defence"),
        ("rank_status / rank_reasons", "OK, PROVISIONAL (graph not yet one component or < 3 games) or SUPPRESSED (see policy), with the reasons"),
        ("games_played", "this season's games before `data_asof_utc`"),
        ("effective_games, carry_share", "information the rating rests on in game-equivalents (prior seasons count 0.2 per game) and the share of it carried from earlier seasons"),
        ("connected_to_main, component_size", "membership of the largest connected component of this season's FBS-vs-FBS schedule graph"),
        ("fcs_pooled, fcs_games_played", "true when games against FCS opponents entered this rating (handled through the pooled FCS entity); count of this season's FCS games"),
        ("script_engine.{rank, universe, rating, raw_points_allowed, se, games, stable, fbs_components, published_when_unstable}",
         "the production script engine's CURRENT points-allowed defence rank for the same team (recomputed read-only with `adjust.fit_league` on the committed game log), its rating, and whether its own fit is flagged stable"),
    ]
    for a, b in rows:
        A(f"| `{a}` | {b} |")
    A("")
    A("Top level: `schema`, `role`, `authority`, `season`, `generated_utc`, `data_asof_utc`, `model{id,version,spec}`, `league{fbs_schedule_components, largest_component_share, "
      "median_fbs_games, ranks_published, rule}`, `notes`, `teams[]`.")
    A("")
    A("## Rank policy (why ranks can be null)")
    A("")
    A("The production engine published ranks while the FBS schedule graph still had ~85 components after week 1 and ~40 after week 2, i.e. ranked teams that "
      "had never been compared with each other. Here `rank` is **withheld** (rank_status SUPPRESSED) while the current-season FBS-vs-FBS graph is disconnected AND "
      "the median FBS team has fewer than 4 games, and for any team outside the main component afterwards. Ratings and rank bands are still published so "
      "a consumer can show 'somewhere between 20 and 90'. These thresholds are product policy, not fitted values; the production engine's own `stable` rule "
      "(one component and median >= 3 games) is recorded next to every team for comparison.")
    A("")
    A("## Validation (all numbers produced by `python -m cfb.defense_validate`, game-clustered paired bootstrap, negative = new rating better)")
    A("")
    A(f"* Reproduction: the module's fit equals the frozen walk-forward ridge (`D_pts_pred` in the study frame) to {v['reproduction']['max_abs_diff_points']:.1e} points on {len(v['reproduction']['blocks'])} sampled blocks.")
    A("* Test: each team's points in its NEXT game (= points allowed by the opposing defence), regular season, FBS-vs-FBS, rating as of the week of the game. The script-engine comparator is "
      "the production code replayed week by week (`adjust.fit_league`, season-to-date ridge, lambda = 4 games, points per game): its prediction is `adj_off(team) + adj_def(opp) - mu + h*site`. "
      "The engine publishes no adjusted rating before week 2, so week-1 games are excluded from this comparison.")
    A("")
    A("### Next-game points: new rating vs script-engine rating")
    A("")
    A("| split | weeks | games | MAE new | MAE script engine | new minus script engine (MAE) | squared error |")
    A("|---|---|---|---|---|---|---|")
    for split in ("development", "validation", "holdout", "holdout_2025"):
        for wb in ("all", "wk2-4", "wk5-8", "wk9+"):
            r = v["downstream"].get(f"{split}|FBS_vs_FBS|{wb}")
            if r:
                A(f"| {split} | {wb} | {r['n_games']} | {r['new_mae']:.3f} | {r['script_engine_mae']:.3f} | {ci(r['new_minus_se_mae'])} | {ci(r['new_minus_se_sqerr'])} |")
    A("")
    A("The same comparison with the frozen team-points ridge (no conference offsets) is in `downstream_frozen_D` of the JSON; its all-weeks differences are "
      + ", ".join(f"{s} {v['downstream_frozen_D'][f'{s}|FBS_vs_FBS|all']['new_minus_se_mae']['mean_diff']:+.3f}" for s in ("validation", "holdout", "holdout_2025")) + ".")
    A("")
    A("### Defence term only (frozen ridge): swap the defence rating inside an otherwise unchanged model (paired MAE difference; negative = the new defence term is better)")
    A("")
    A("| split | weeks | engine model with the NEW defence minus engine model | new model minus new model with the ENGINE's defence |")
    A("|---|---|---|---|")
    for split in ("validation", "holdout", "holdout_2025"):
        for wb in ("all", "wk2-4", "wk5-8", "wk9+"):
            r = v["downstream_frozen_D"].get(f"{split}|FBS_vs_FBS|{wb}")
            if r and "defence_only" in r:
                d = r["defence_only"]
                A(f"| {split} | {wb} | {ci(d['new_def_vs_se_def_inside_se_model'])} | {ci(d['new_def_vs_se_def_inside_new_model'])} |")
    A("")
    A("Reading: most of the all-weeks gain over the engine is a system effect (carried prior seasons, FCS pooling, the offence term and a jointly fitted home advantage); "
      "the defence rating alone is significantly better in weeks 2-4 (where the engine's ranks rest on 1-3 unconnected games) and converges with the engine's from about week 5.")
    A("")
    A("### Rank reliability against independent rest-of-season defensive truth (mean over seasons; Spearman / top-10 overlap)")
    A("")
    A("| split | week | new rho | script engine rho | new top-10 hits | engine top-10 hits | FBS components (mean) | median games |")
    A("|---|---|---|---|---|---|---|---|")
    for split in ("development", "validation", "holdout", "holdout_2025"):
        for r in v["rank_reliability"].get(split, []):
            A(f"| {split} | {r['week']} | {r['rho_new']:.2f} | {r['rho_se']:.2f} | {r['top10_new']:.1f} | {r['top10_se']:.1f} | {r['components']:.0f} | {r['median_games']:.0f} |")
    A("")
    A("### Conference offsets (decided on development only)")
    A("")
    b = conf["best_on_development"]
    A(f"Grid of 9 variants (team ridge alpha x conference alpha), walk-forward on 2016-2019, FBS-vs-FBS next-game MAE vs the module without offsets "
      f"(baseline {conf['baseline_dev_mae_fbs_vs_fbs']:.4f}). Best on development: team alpha {b['alpha_team']}, conference alpha {b['alpha_conf']}, "
      f"MAE {b['mae']:.4f}, difference {ci(b['vs_baseline_all_weeks'])}; weeks 1-4 {ci(b['vs_baseline_weeks_1_4'])}. "
      f"Rule: adopt only if that development interval excludes 0 (and no later split is significantly worse). **Adopted: {conf['adopted']}.** "
      "Because the best of nine variants was selected, the later splits (not used for the decision) are the real check: "
      + "; ".join(f"{k} {ci(x)}" for k, x in conf["confirmation_best_variant_vs_baseline"].items()) + ".")
    A("")
    A("| team alpha | conference alpha | dev MAE | vs no offsets (all weeks) |")
    A("|---|---|---|---|")
    for g in conf["grid"]:
        A(f"| {g['alpha_team']} | {g['alpha_conf']} | {g['mae']:.4f} | {ci(g['vs_baseline_all_weeks'])} |")
    A("")
    A("### Calibration of the uncertainty")
    A("")
    rb = v["rank_band"]
    A("Share of teams whose week-w rating (centred) lies inside the 80% interval of an independent rest-of-season rating (weeks >= w, own SE included), by variance inflation "
      f"factor; the factor used for publication is the smallest reaching 80% on development: **{rb['var_inflation']}**.")
    A("")
    A("| split | x1.0 | x1.5 | x2.0 | published rank band vs rest-of-season rank |")
    A("|---|---|---|---|---|")
    for split, c in rb["coverage"].items():
        A(f"| {split} | {c['1.0']:.2f} | {c['1.5']:.2f} | {c['2.0']:.2f} | {c['rank_band']:.2f} |")
    A("")
    A("The published 80% **rank** band describes uncertainty in the rating NOW; it contains the (itself noisy) rest-of-season rank for ~60-65% of teams, so it is a "
      "lower bound on how much a rank can move, not a forecast interval for the final rank.")
    A("")
    A("## Limits")
    A("")
    A("* Points per game still mix in a team's own pace (study section 7: rank gap vs per-drive correlates +0.34 with own plays); a per-drive defensive rating is future work.")
    A("* FCS opponents are one pooled entity: it fixes the flattering effect of playing an FCS team but cannot tell FCS teams apart.")
    A("* Rest-of-season truth is itself estimated from few games; rank correlations of ~0.5-0.65 are close to what any rating can reach (even a good rating finds only ~4 of the true top 10).")
    A("* The script-engine comparison uses the engine's published metric (points per game, season-to-date); its other metrics are untouched.")
    A("")
    A("Reproduce: `cd research/projection_foundation && python -m cfb.defense_validate && python -m cfb.defense_doc && python -m cfb.defense_shadow`; tests: `python -m pytest tests/test_cfb_defense_ratings.py`.")
    open(paths.PF_ROOT / "DEFENSE_RATINGS.md", "w").write("\n".join(L) + "\n")
    print("wrote DEFENSE_RATINGS.md")


if __name__ == "__main__":
    main()
