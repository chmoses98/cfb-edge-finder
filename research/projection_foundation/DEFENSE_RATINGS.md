# CFB defensive ratings - shadow publication and validation

*Research only (`authority: research_only`, `role: shadow`). The production script engine is unchanged; this file is published to the `projection-data` branch as `ratings/cfb/defense_latest.json` NEXT TO the script engine's own current rank for every team.*

## What is published

`ratings/cfb/defense_latest.json` (`sift.defense_ratings.v1`), rewritten by every live run when its content changes (`defense_<UTC>.json` copies are kept). One row per FBS defence (conference offsets ON; ridge alpha 2.0, conference alpha 0.25, prior-season weight 0.2 per season back over 3 seasons, every non-FBS opponent pooled into one FCS entity).

| field | meaning |
|---|---|
| `team_id, team` | ESPN team id and display name |
| `rating` | points per game allowed against an average offence, relative to the FBS-average defence (negative = better) |
| `se` | posterior SD of that rating (ridge posterior, same construction the production engine uses) |
| `rank` | 1 = fewest points allowed; **null while rank_status is SUPPRESSED** |
| `rank_point, rank_lo, rank_hi` | point rank and the 80% posterior rank interval (always published, also when `rank` is withheld) |
| `p_top10, p_top25` | posterior probability of a top-10 / top-25 defence |
| `rank_status / rank_reasons` | OK, PROVISIONAL (graph not yet one component or < 3 games) or SUPPRESSED (see policy), with the reasons |
| `games_played` | this season's games before `data_asof_utc` |
| `effective_games, carry_share` | information the rating rests on in game-equivalents (prior seasons count 0.2 per game) and the share of it carried from earlier seasons |
| `connected_to_main, component_size` | membership of the largest connected component of this season's FBS-vs-FBS schedule graph |
| `fcs_pooled, fcs_games_played` | true when games against FCS opponents entered this rating (handled through the pooled FCS entity); count of this season's FCS games |
| `script_engine.{rank, universe, rating, raw_points_allowed, se, games, stable, fbs_components, published_when_unstable}` | the production script engine's CURRENT points-allowed defence rank for the same team (recomputed read-only with `adjust.fit_league` on the committed game log), its rating, and whether its own fit is flagged stable |

Top level: `schema`, `role`, `authority`, `season`, `generated_utc`, `data_asof_utc`, `model{id,version,spec}`, `league{fbs_schedule_components, largest_component_share, median_fbs_games, ranks_published, rule}`, `notes`, `teams[]`.

## Rank policy (why ranks can be null)

The production engine published ranks while the FBS schedule graph still had ~85 components after week 1 and ~40 after week 2, i.e. ranked teams that had never been compared with each other. Here `rank` is **withheld** (rank_status SUPPRESSED) while the current-season FBS-vs-FBS graph is disconnected AND the median FBS team has fewer than 4 games, and for any team outside the main component afterwards. Ratings and rank bands are still published so a consumer can show 'somewhere between 20 and 90'. These thresholds are product policy, not fitted values; the production engine's own `stable` rule (one component and median >= 3 games) is recorded next to every team for comparison.

## Validation (all numbers produced by `python -m cfb.defense_validate`, game-clustered paired bootstrap, negative = new rating better)

* Reproduction: the module's fit equals the frozen walk-forward ridge (`D_pts_pred` in the study frame) to 3.3e-13 points on 6 sampled blocks.
* Test: each team's points in its NEXT game (= points allowed by the opposing defence), regular season, FBS-vs-FBS, rating as of the week of the game. The script-engine comparator is the production code replayed week by week (`adjust.fit_league`, season-to-date ridge, lambda = 4 games, points per game): its prediction is `adj_off(team) + adj_def(opp) - mu + h*site`. The engine publishes no adjusted rating before week 2, so week-1 games are excluded from this comparison.

### Next-game points: new rating vs script-engine rating

| split | weeks | games | MAE new | MAE script engine | new minus script engine (MAE) | squared error |
|---|---|---|---|---|---|---|
| development | all | 2743 | 9.575 | 10.091 | -0.517 [-0.608, -0.425] | -13.572 [-16.030, -11.157] |
| development | wk2-4 | 598 | 9.558 | 11.064 | -1.506 [-1.810, -1.216] | -40.407 [-48.833, -32.214] |
| development | wk5-8 | 876 | 9.627 | 9.993 | -0.366 [-0.519, -0.220] | -9.491 [-13.474, -5.634] |
| development | wk9+ | 1269 | 9.546 | 9.701 | -0.154 [-0.237, -0.071] | -3.745 [-5.760, -1.765] |
| validation | all | 1363 | 9.198 | 9.600 | -0.402 [-0.540, -0.277] | -11.946 [-15.445, -8.651] |
| validation | wk2-4 | 312 | 9.106 | 10.189 | -1.083 [-1.529, -0.683] | -33.985 [-46.320, -23.314] |
| validation | wk5-8 | 433 | 9.041 | 9.316 | -0.275 [-0.474, -0.079] | -9.185 [-14.082, -4.272] |
| validation | wk9+ | 618 | 9.356 | 9.502 | -0.146 [-0.251, -0.053] | -2.754 [-5.206, -0.476] |
| holdout | all | 1411 | 9.089 | 9.420 | -0.331 [-0.447, -0.214] | -9.209 [-12.106, -6.154] |
| holdout | wk2-4 | 319 | 9.117 | 10.034 | -0.917 [-1.312, -0.535] | -26.333 [-36.490, -17.075] |
| holdout | wk5-8 | 426 | 9.261 | 9.571 | -0.310 [-0.511, -0.110] | -7.242 [-12.126, -2.479] |
| holdout | wk9+ | 666 | 8.965 | 9.029 | -0.064 [-0.169, +0.039] | -2.265 [-4.803, +0.379] |
| holdout_2025 | all | 714 | 8.752 | 9.205 | -0.453 [-0.628, -0.279] | -13.581 [-18.615, -8.762] |
| holdout_2025 | wk2-4 | 147 | 8.833 | 10.190 | -1.357 [-2.019, -0.714] | -43.688 [-63.713, -25.268] |
| holdout_2025 | wk5-8 | 216 | 8.513 | 8.914 | -0.401 [-0.679, -0.127] | -11.445 [-18.087, -4.768] |
| holdout_2025 | wk9+ | 351 | 8.865 | 8.972 | -0.107 [-0.243, +0.033] | -2.287 [-5.336, +0.877] |

The same comparison with the frozen team-points ridge (no conference offsets) is in `downstream_frozen_D` of the JSON; its all-weeks differences are validation -0.324, holdout -0.246, holdout_2025 -0.406.

### Defence term only (frozen ridge): swap the defence rating inside an otherwise unchanged model (paired MAE difference; negative = the new defence term is better)

| split | weeks | engine model with the NEW defence minus engine model | new model minus new model with the ENGINE's defence |
|---|---|---|---|
| validation | all | -0.064 [-0.180, +0.044] | -0.223 [-0.335, -0.115] |
| validation | wk2-4 | -0.384 [-0.707, -0.047] | -0.625 [-0.949, -0.284] |
| validation | wk5-8 | +0.132 [-0.062, +0.316] | -0.063 [-0.252, +0.114] |
| validation | wk9+ | -0.040 [-0.165, +0.090] | -0.131 [-0.251, -0.010] |
| holdout | all | -0.040 [-0.143, +0.062] | -0.274 [-0.378, -0.174] |
| holdout | wk2-4 | -0.270 [-0.560, +0.002] | -0.480 [-0.759, -0.197] |
| holdout | wk5-8 | +0.001 [-0.187, +0.190] | -0.274 [-0.461, -0.089] |
| holdout | wk9+ | +0.045 [-0.066, +0.166] | -0.175 [-0.286, -0.058] |
| holdout_2025 | all | -0.163 [-0.322, -0.000] | -0.463 [-0.617, -0.304] |
| holdout_2025 | wk2-4 | -0.500 [-1.030, +0.002] | -0.551 [-1.022, -0.090] |
| holdout_2025 | wk5-8 | -0.226 [-0.510, +0.050] | -0.606 [-0.893, -0.332] |
| holdout_2025 | wk9+ | +0.017 [-0.171, +0.201] | -0.339 [-0.528, -0.152] |

Reading: most of the all-weeks gain over the engine is a system effect (carried prior seasons, FCS pooling, the offence term and a jointly fitted home advantage); the defence rating alone is significantly better in weeks 2-4 (where the engine's ranks rest on 1-3 unconnected games) and converges with the engine's from about week 5.

### Rank reliability against independent rest-of-season defensive truth (mean over seasons; Spearman / top-10 overlap)

| split | week | new rho | script engine rho | new top-10 hits | engine top-10 hits | FBS components (mean) | median games |
|---|---|---|---|---|---|---|---|
| development | 2 | 0.63 | 0.37 | 4.5 | 2.2 | 86 | 1 |
| development | 3 | 0.62 | 0.49 | 5.0 | 3.5 | 40 | 2 |
| development | 4 | 0.58 | 0.52 | 4.8 | 3.8 | 3 | 3 |
| development | 6 | 0.54 | 0.54 | 4.5 | 4.8 | 2 | 5 |
| development | 8 | 0.53 | 0.57 | 4.5 | 5.2 | 2 | 6 |
| validation | 2 | 0.62 | 0.35 | 4.5 | 2.0 | 79 | 1 |
| validation | 3 | 0.58 | 0.44 | 4.0 | 3.0 | 33 | 2 |
| validation | 4 | 0.49 | 0.42 | 4.0 | 4.0 | 1 | 3 |
| validation | 6 | 0.44 | 0.44 | 3.5 | 3.0 | 1 | 5 |
| validation | 8 | 0.43 | 0.44 | 3.0 | 2.5 | 1 | 6 |
| holdout | 2 | 0.60 | 0.35 | 5.5 | 2.5 | 88 | 1 |
| holdout | 3 | 0.57 | 0.43 | 6.5 | 3.5 | 40 | 2 |
| holdout | 4 | 0.55 | 0.51 | 6.0 | 4.5 | 2 | 3 |
| holdout | 6 | 0.49 | 0.49 | 5.0 | 4.0 | 1 | 5 |
| holdout | 8 | 0.47 | 0.50 | 5.0 | 4.0 | 1 | 6 |
| holdout_2025 | 2 | 0.67 | 0.32 | 3.0 | 0.0 | 88 | 1 |
| holdout_2025 | 3 | 0.58 | 0.44 | 3.0 | 1.0 | 38 | 2 |
| holdout_2025 | 4 | 0.52 | 0.48 | 3.0 | 2.0 | 4 | 3 |
| holdout_2025 | 6 | 0.48 | 0.54 | 4.0 | 4.0 | 1 | 4 |
| holdout_2025 | 8 | 0.46 | 0.55 | 4.0 | 4.0 | 1 | 6 |

### Conference offsets (decided on development only)

Grid of 9 variants (team ridge alpha x conference alpha), walk-forward on 2016-2019, FBS-vs-FBS next-game MAE vs the module without offsets (baseline 9.6152). Best on development: team alpha 2.0, conference alpha 0.25, MAE 9.5724, difference -0.043 [-0.078, -0.005]; weeks 1-4 -0.126 [-0.223, -0.026]. Rule: adopt only if that development interval excludes 0 (and no later split is significantly worse). **Adopted: True.** Because the best of nine variants was selected, the later splits (not used for the decision) are the real check: validation -0.115 [-0.174, -0.055]; holdout -0.105 [-0.153, -0.059]; holdout_2025 -0.074 [-0.136, -0.012].

| team alpha | conference alpha | dev MAE | vs no offsets (all weeks) |
|---|---|---|---|
| 0.5 | 0.25 | 9.5856 | -0.030 [-0.045, -0.015] |
| 0.5 | 1.0 | 9.5881 | -0.027 [-0.040, -0.014] |
| 0.5 | 4.0 | 9.5952 | -0.020 [-0.029, -0.011] |
| 2.0 | 0.25 | 9.5724 | -0.043 [-0.078, -0.005] |
| 2.0 | 1.0 | 9.5767 | -0.038 [-0.072, -0.002] |
| 2.0 | 4.0 | 9.5904 | -0.025 [-0.058, +0.010] |
| 5.0 | 0.25 | 9.7088 | +0.093 [+0.025, +0.169] |
| 5.0 | 1.0 | 9.7135 | +0.098 [+0.030, +0.174] |
| 5.0 | 4.0 | 9.7299 | +0.115 [+0.046, +0.190] |

### Calibration of the uncertainty

Share of teams whose week-w rating (centred) lies inside the 80% interval of an independent rest-of-season rating (weeks >= w, own SE included), by variance inflation factor; the factor used for publication is the smallest reaching 80% on development: **1.0**.

| split | x1.0 | x1.5 | x2.0 | published rank band vs rest-of-season rank |
|---|---|---|---|---|
| development | 0.82 | 0.87 | 0.91 | 0.65 |
| validation | 0.84 | 0.88 | 0.92 | 0.61 |
| holdout | 0.83 | 0.87 | 0.90 | 0.63 |
| holdout_2025 | 0.79 | 0.84 | 0.88 | 0.61 |

The published 80% **rank** band describes uncertainty in the rating NOW; it contains the (itself noisy) rest-of-season rank for ~60-65% of teams, so it is a lower bound on how much a rank can move, not a forecast interval for the final rank.

## Limits

* Points per game still mix in a team's own pace (study section 7: rank gap vs per-drive correlates +0.34 with own plays); a per-drive defensive rating is future work.
* FCS opponents are one pooled entity: it fixes the flattering effect of playing an FCS team but cannot tell FCS teams apart.
* Rest-of-season truth is itself estimated from few games; rank correlations of ~0.5-0.65 are close to what any rating can reach (even a good rating finds only ~4 of the true top 10).
* The script-engine comparison uses the engine's published metric (points per game, season-to-date); its other metrics are untouched.

Reproduce: `cd research/projection_foundation && python -m cfb.defense_validate && python -m cfb.defense_doc && python -m cfb.defense_shadow`; tests: `python -m pytest tests/test_cfb_defense_ratings.py`.
