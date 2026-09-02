# CFB MODEL V2 — RESEARCH REPORT

Research branch: `claude/cfb-model-v2-research-krupoc`. Production `0.5.0-early-season-talent-prior` is unchanged and remains the live, frozen benchmark. No 2026 outcome was used to fit or select anything in this report (see §3 and §6 for the firewall).

## V2 VERDICT

**A. V2 CLEARLY BETTER THAN 0.5.0 — READY FOR PRODUCTIONIZATION (as a projection model). NOT a demonstrated betting edge.** V2-MARGIN, V2-WINNER and the uncertainty layer improve every acceptance metric in every test season; V2-TOTAL improves every season but remains the weakest channel. V2 is 0.4 points behind the closing market on margins, 0.3 on totals, and shows no against-the-spread edge. Evidence, spec and plan follow; the verdict is restated at the end.

---

## 1. Why V1 / 0.5.0 is limited

Code-verified trace of the live path (`research_scan_and_capture.py` → `football_state` cache → `GameProjectionCache` → `fit_fbs_efficiency_ratings` → `build_expanding_residual_pool` → `project_game` → `apply_margin_correction` + talent delta → `to_game_distribution` → `projections.distribution` normal CDF → `ladder_pricing`). What the model actually knows at kickoff of a 2026 game:

| Input | Live status |
|---|---|
| Scores and play counts, 2022–2025 FBS games | used, **pooled with equal weight across the four seasons** (no recency decay) |
| 2026 completed games | **never enter the ratings** — `history_seasons` is fixed to 2022–2025, so the model does not learn during 2026 at all |
| Talent composite (2026) | margin shift only, `0.018993 × Δtalent` |
| Neutral-site flag | correct (HFA forced to 0) |
| Returning production, coaching change, recruiting, conference, rest, travel, weather, QB identity, injuries | not used (`percent_passing_ppa` is `None` live, so every game gets the same 1.20× "unknown QB" inflation) |

Structural findings that motivate a rebuild rather than another patch:

1. **Season carryover is inert.** `games_played` counts all corpus rows (≈50 per team), so the "prior-season blend" weight is ≈0.93 at Week 1 and `prior_season_ratings=None` is passed live. A 2026 Week-1 rating is a four-season equal-weight pooled fit shrunk 7% toward zero. This is the mechanism behind the documented Week-1 margin compression that `a=1.34` in the margin correction and the talent prior both partially compensate for.
2. **No in-season learning in 2026** (above). Weeks 2–15 are priced from the same pooled 2022–2025 fit as Week 1, plus the talent delta.
3. **The live moneyline is the closed-form Normal on the corrected mean**, not the validated empirical simulated win probability (`prob_home_win()` is never called on the Kalshi path). The continuity correction is applied on top of Kalshi's half-point strikes (`P(margin > 4.5)` is priced as `1 − Φ((5.0 − μ)/σ)`), a systematic ≈1-point-of-probability shift against every YES rung near the mean.
4. **Provenance claims `calibration=platt`; no probability calibration runs live.** No config hash is stamped on canonical rows.
5. **Uncertainty is nearly constant**: `EARLY_SEASON_UNCERTAINTY_SCALE` contributes 1.02× instead of the intended 1.30×, and the residual pool is one global bootstrap pool scaled by fixed multipliers.
6. `seed=0` is shared across all games in a week, so simulated residual draws are rank-coupled across games.
7. Totals were never repaired: Week-1 total over-projection (+2.9) and ECE 0.052.

Reproduced control on the model-repair inputs (2021–2025, FBS-vs-FBS, 4,000 sims, live parity settings — `scripts/v2_control_predictions.py`): Week-1 margin MAE 15.96 / bias −6.3 (0.4.0) and 14.12 / −4.0 (0.5.0), Weeks 4+ 13.8–14.4 depending on season. These match the documented numbers.

## 2. Free data inventory

Acquired on a GitHub-hosted runner by `scripts/fetch_v2_research_cache.py` (workflow `preseason-research-fetch.yml`, `mode=v2`), read-only, whole-season pulls. Persisted gzipped on the `research-data` branch under `data/research_cache/v2/` (25.6 MB, 219 files, manifest with per-endpoint row counts, schema fingerprints, timing notes and the full call log).

Cost: **422 metered CFBD calls** in one run (`/info` unmetered before/after: 768 → 358 remaining of 1,000 for September). `/games/teams` rejects year-only requests (HTTP 400) and fell back to per-week pulls (17 calls/season), which is where 204 of the 422 went. **Operational flag: 358 calls remain for the rest of September; the collector's own usage is ≈15–25/day (heartbeat-observed), so the month is tight. No further quota was spent by this research.**

| Endpoint | Seasons | Rows | Timing class | V2 use |
|---|---|---|---|---|
| `/games` | 2014–2026 | 11,262 | scores POSTGAME (targets); schedule/venue/neutral/conference/**pregame Elo** pregame | targets, situational, external Elo benchmark |
| `/stats/game/advanced` (regular, postseason, garbage-time-excluded) | 2014–2025 | 50,464 | POSTGAME for its own game | rolling efficiency state (strictly prior games only) |
| `/games/teams` (box score) | 2014–2025 | 10,369 | POSTGAME | rolling box state (turnovers, havoc, 3rd down, penalties) |
| `/drives` | 2014–2025 | 258,557 | POSTGAME | finishing drives (points per opportunity), field position, tempo |
| `/lines` | 2014–2025 | 12,761 | pregame but market-derived | **EVALUATION ONLY** |
| `/rankings` | 2014–2026 | 185 weeks | week-1 poll = preseason poll (verified: 2024 wk1 top-5 = Georgia, Ohio State, Oregon, Texas, Alabama; wk2 differs) | preseason poll points |
| `/teams/fbs` | 2014–2026 | 1,704 | pregame | season-scoped conference, venue location/elevation/timezone |
| `/recruiting/teams` | 2014–2026 | 2,790 | signed in the prior cycle | 4-class recruiting average |
| `/talent` | 2015–2026 | 2,413 | settled in prior cycle (2014 absent) | talent composite |
| `/player/returning` | 2014–2026 | 1,691 | preseason S, describes S−1 | continuity features |
| `/coaches` | 2014–2026 | 1,839 | identity only (W/L dropped) | coaching change, tenure |
| `/stats/season/advanced` | 2014–2025 | 1,566 | POSTGAME for S; **S−1 only** | not used by the winner (state already covers it) |
| `/ratings/sp` | 2014–2025 | 1,578 | END-OF-SEASON S; **S−1 only** | tested, no incremental value (§8) |
| `/player/portal` | 2021–2026 | 18,892 | `transferDate` is an event date; ratings/destinations may be revised | **descriptive only, not a feature** (timing unproven) |
| `/venues` | static | 852 | static | travel distance, altitude, dome |

Classified UNAVAILABLE or unsafe and not used: roster/QB identity (retroactively revised), injuries (no structured source), pregame weather forecasts (only realised conditions exist), `/plays` (too expensive for the free tier and not needed), CFBD weekly `/ratings/elo` (redundant with the `/games` pregame Elo). The AP/Coaches poll and the `/games` pregame Elo are the two externally-computed pregame signals; both are timing-safe by construction but Elo is treated as an external benchmark/optional input rather than a core V2 dependency because its live availability for upcoming games is not guaranteed (only 245 of 888 2026 schedule rows carried it at fetch time).

## 3. Final historical dataset

`scripts/v2_build_dataset.py` → `data/research_cache/v2_work/dataset.parquet` (+ `.meta.json` with `dataset_version`, state config, cache `fetched_at`, feature-column hash). Build time ≈50 s from the cache; no CFBD calls.

* One row per FBS-involved game, 2015–2025 completed (10,393 rows incl. 888 masked 2026 schedule rows; 8,325 FBS-vs-FBS completed rows are the evaluation population), postseason included with the production `postseason_week_rank` ordering.
* Team keys are CFBD school names (consistent across every endpoint); `home_slug`/`away_slug` map to the production registry ids (bare "Miami" → `miami-fl` explicitly; the production resolver refuses it and V1 therefore cannot rate Miami (FL) games — 72 of them over 2019–2025).
* **Targets**: home/away points, margin, total, home_won. **2026 targets are NaN by construction** — the builder never reads a 2026 score.
* **Team state** (231 pregame feature columns): for every distinct (season, week) as-of, one weighted ridge solve with a multi-column right-hand side gives opponent-adjusted offense/defense strengths for 26 metrics (scoring, PPA, success rate, explosiveness, pass/rush, standard/passing downs, line yards, plays, drives points-per-drive, points-per-opportunity, turnover rate, three-and-outs, starting field position, seconds per play, third-down rate, penalties, havoc, sacks, takeaways) from games **strictly before** the as-of, with season-decay weights and a pooled FCS pseudo-team. Week-1 rows have zero current-season games by assertion.
* **Preseason** (dated S−1 or earlier): talent, recruiting classes S…S−3, returning production splits, coaching change/tenure, prior-season and two-seasons-ago single-season strengths and record, end-of-S−1 SP+, preseason AP/Coaches poll points, FBS-newcomer flag.
* **Situational**: neutral, conference game, week, postseason, rest days, travel distance (team home venue → game venue), venue elevation/dome, kickoff hour, FCS opponent.
* **Market (evaluation only, `mkt_*`)**: consensus closing spread (home-margin convention), total, vig-removed moneyline probability, opening spread; never a feature.

Sanity checks recorded in the build log: efficiency differentials correlate 0.55–0.58 with margin, points-per-opportunity 0.52, CFBD Elo difference 0.60, market spread 0.66.

## 4. Feature families

| Family | Columns (per side, home and away) | Source |
|---|---|---|
| Structural strength | opponent-adjusted margin strength, scoring offense/defense, league HFA (season-decayed walk-forward ridge) | `/games` |
| Efficiency state | PPA, success rate, explosiveness, pass/rush PPA and success, standard/passing-down success, line yards, plays, garbage-time-excluded PPA/SR | `/stats/game/advanced` |
| Drive state | points per drive, points per opportunity, turnover rate, three-and-out rate, average starting yards-to-goal, seconds per play | `/drives` |
| Box state | third-down rate, penalty yards, havoc (TFL+PD+INT), sacks, takeaways | `/games/teams` |
| Evidence | games this season, decay-weighted games, `early_w = exp(−min_games/4)` | derived |
| Preseason | talent, 4-class recruiting average and current class, returning PPA/passing/rushing/receiving/usage, coach change and tenure, S−1 and S−2 single-season margin strength, S−1 points for/against and win %, S−1 SP+ (overall/off/def), preseason AP and Coaches points, FBS newcomer | preseason endpoints |
| Early-season interactions | preseason differential × `early_w` for talent, recruiting, prior strength, SP+, poll, returning PPA, returning passing, coach change (plus k=2 and k=8 decay variants) | derived |
| Situational | week, postseason, conference game, rest difference and short-week flags, travel distance (difference and away), venue elevation and dome, kickoff hour | `/games`, `/teams/fbs`, `/venues` |
| Long-memory strength (`_L`) | margin, scoring and PPA strengths with a slow (0.85) season decay | derived |
| External | CFBD pregame Elo difference | `/games` |

Matchup transforms follow the state's sign convention: for metric m, home expectation = off_h − def_a, away expectation = off_a − def_h; `diff_m` (home − away) drives margin, `sum_m` drives total. Named feature sets (hashed) are in `research/v2/features.py`: `struct` (3), `struct+pre` (38), `eff` (32), `eff+pre` (66), `eff+pre+sit` (77), `full` (150), `full+elo` (151), `elo_only` (2), total-oriented `tot_struct`, `tot_eff`, `tot_eff+pre`, `tot_full`, plus the ablation and long-memory sets.

## 5. Model families tested

* Baselines: zero model (training means), **closing market consensus** (evaluation only), CFBD-Elo-only ridge, structural-only ridge.
* Ridge regression (standardised, alpha chosen on the inner validation season from {1, 10, 100, 1000}) on every feature set, for margin, total, and separate home/away points.
* LightGBM regression (L2 and L1 objectives; early-stopped on the inner validation season, refit on all training seasons at the chosen iteration; a small-tree variant).
* Ridge + LightGBM residual stack.
* Winner classifiers: logistic regression and LightGBM classifier on `full`.
* Training-row recency weights (0.85, 0.70 per season), FBS-vs-FCS rows in training, chronological affine recalibration, and NNLS chronological ensembles.
* Team-state hyper-parameters as dataset variants: season decay 0.20–0.70, ridge strength 3–12, long-memory features.

## 6. Chronological validation design

Rolling origin, declared before any candidate ran: test seasons **2017, 2018, 2019, 2021, 2022, 2023, 2024, 2025**; every fold trains on all completed FBS-vs-FBS games with season < Y (2015 onward), selects hyper-parameters on season Y−1 only, refits on all seasons < Y, and predicts every FBS-vs-FBS game of Y. 2020 is never a test season (COVID scheduling) but remains training evidence. 2015–2016 are training-only. The control comparison (§19) uses the 3,675 games of 2021–2025 the model-repair mission also evaluated. Every fold is reported separately (§7, §16); Week 1, Weeks 1–3, Weeks 4+, postseason, neutral and conference/non-conference segments are reported for every candidate in `registry.jsonl`.

State-configuration selection rule (declared 2026-09-02 06:40 UTC, before the 0.20 and within-season-decay variants had been evaluated): among the tested season-decay / week-decay dataset variants, choose the configuration that minimises pooled margin MAE + pooled total MAE over the eight test seasons, provided no single test season is worse than the 0.50 baseline by more than 0.10 MAE; differences below 0.02 are ties and the tie-break is the slower decay (more history retained). Margin and total may use different state configurations only if the pooled gain exceeds 0.05 on the channel concerned.

Uncertainty: paired per-game (or per-contract, game-clustered) differences with a 2,000-resample bootstrap over whole games; "improves/degrades" means the 95% interval excludes zero.

## 7. Full candidate leaderboard

Every run (86 candidates across five rounds plus ensembles, all on identical folds) is in `docs/v2/LEADERBOARD.md`, rendered from the experiment registry (`registry.jsonl`, archived on `research-data` under `data/research/v2/`). Each registry record carries the candidate spec and its hash, the feature-set hash, the dataset feature hash, the chosen hyper-parameters per fold, per-season and per-segment metrics, and runtime. Nothing was deleted; failed and rejected candidates are all there.

Headline rows (pooled over the 8 test seasons, FBS-vs-FBS, 6,266 games; the market row is on its own 5,900-game subset and is compared on common games in §18b):

| candidate | family | features | margin MAE | RMSE | bias | fav-tail bias | Wk1 MAE | Wk4+ MAE | winner LL |
|---|---|---|---|---|---|---|---|---|---|
| zero model | training mean | 0 | 16.232 | 20.731 | −0.25 | — | 18.84 | 15.78 | 0.679 |
| closing market (own subset) | — | — | 12.188 | 15.453 | −0.07 | −0.23 | 12.14 | 12.11 | 0.548 |
| CFBD Elo only, ridge | ridge | 2 | 13.123 | 16.572 | −0.24 | −0.98 | 14.65 | 12.78 | 0.549 |
| structural ridge (decay 0.5) | ridge | 3 | 13.336 | 16.827 | −0.19 | −1.40 | 14.71 | 13.04 | 0.558 |
| struct+pre ridge (decay 0.5) | ridge | 38 | 12.776 | 16.178 | −0.23 | 0.00 | 12.40 | 12.68 | 0.539 |
| eff+pre ridge (decay 0.5) | ridge | 64 | 12.833 | 16.184 | −0.14 | +0.83 | 12.44 | 12.71 | 0.540 |
| full ridge (150 features) | ridge | 150 | 12.872 | 16.240 | +0.21 | +1.27 | 12.58 | 12.74 | 0.541 |
| full LightGBM | GBM | 150 | 13.038 | 16.408 | −0.12 | −0.70 | 12.71 | 12.91 | 0.548 |
| home/away points ridge → margin | ridge×2 | 150 | 12.907 | 16.258 | +0.45 | +1.61 | 12.64 | 12.78 | 0.542 |
| struct+pre ridge, **state decay 0.25** | ridge | 38 | 12.676 | 16.039 | −0.15 | +0.28 | 12.46 | 12.58 | 0.534 |
| eff+pre ridge, state decay 0.25 | ridge | 64 | 12.669 | 15.997 | −0.14 | +0.66 | 12.40 | 12.56 | 0.533 |
| **V2-MARGIN: equal-weight mean of the two rows above** | ridge pair | 38+64 | **12.635** | **15.976** | −0.15 | +0.28 | 12.39 | 12.53 | **0.534** |
| struct+pre+Elo ridge (external Elo added) | ridge | 39 | 12.661 | 16.021 | −0.11 | +0.24 | 12.38 | 12.55 | 0.534 |

Totals (pooled):

| candidate | total MAE | bias | Wk1 | Wk4+ |
|---|---|---|---|---|
| zero model | 14.098 | +1.93 | 13.92 | 14.17 |
| closing market (own subset) | 12.768 | −0.29 | 12.67 | 12.77 |
| tot_struct ridge (decay 0.5) | 13.298 | +1.13 | 13.23 | 13.32 |
| tot_eff ridge (decay 0.5) | 13.305 | +0.75 | 13.17 | 13.34 |
| tot_full LightGBM | 13.311 | +1.40 | 13.31 | 13.35 |
| tot_eff ridge, state decay 0.25 | 13.124 | +0.67 | 13.15 | 13.15 |
| **V2-TOTAL: the row above + chronological affine recalibration** | **13.107** | **−0.06** | 13.15 | 13.14 |

Three things the leaderboard says clearly. (1) Model family barely matters: every ridge/GBM/stack/points-decomposition on the same information lands within 0.3 points; LightGBM never beat ridge, the ridge+GBM residual stack found nothing (best iteration 10–17 trees, validation MAE unchanged), and the home/away points decomposition is worse than predicting margin directly. (2) Information matters: preseason features are worth ≈0.55 points pooled and ≈2.3 points in Week 1 (`struct` 13.34 → `struct+pre` 12.78; Wk1 14.71 → 12.40); the team-state decay is worth another ≈0.1 pooled and ≈0.1 on totals. (3) The market is ≈0.4 points better than the best V2 on margins and ≈0.3 on totals (§18b); the CFBD Elo adds ≈0.02 and is not required.

## 8. Best margin model

**V2-MARGIN** = equal-weight mean of two standardised ridge regressions on home margin, fit on FBS-vs-FBS completed games from 2015 with alpha chosen on the last training season from {1, 10, 100, 1000} (100 or 1000 is chosen in every fold), medians for missing features, over the team state with season decay 0.25, ridge 6 (FCS pseudo-team 2), no within-season decay:

* member A, `struct+pre` (38 features): structural margin-strength difference and HFA, neutral, 21 preseason differentials, 6 evidence terms, 8 early-season interactions;
* member B, `eff+pre` (64 features): member A plus 23 opponent-adjusted efficiency/drive/box differentials and 3 HFA terms.

Spec, feature lists and hashes: `docs/v2/V2_SPEC.json` (spec sha256 `d55b5ba0…`). Pooled 12.635 / RMSE 15.98 / bias −0.15 / favourite-tail bias +0.28 / Wk1 12.39 / Wk4+ 12.53; better than the single best member in 8 of 8 seasons.

Preseason family ablations (struct+pre ridge, decay 0.5, pooled / Week 1 MAE; base 12.776 / 12.399):

| removed family | pooled | Week 1 | verdict |
|---|---|---|---|
| talent + recruiting | 12.836 (+0.06) | 13.27 (+0.87) | keep — the Week-1 workhorse |
| prior-season strength/record (S−1, S−2) | 12.972 (+0.20) | 12.39 (0.00) | keep — the largest pooled contributor |
| returning production | 12.824 (+0.05) | 12.62 (+0.22) | keep |
| early-season interactions | 12.859 (+0.08) | 12.79 (+0.39) | keep |
| coaching change/tenure | 12.801 (+0.03) | 12.45 (+0.05) | keep (small) |
| FBS-newcomer flag | 12.834 (+0.06) | 12.32 (−0.08) | keep (2017 fold) |
| S−1 SP+ | 12.784 (+0.01) | 12.23 (−0.17) | no incremental value; retained in the frozen set only because the ablated set was not re-selected (tie) |
| preseason polls | 12.778 (0.00) | 12.41 (+0.01) | no value |
| decay speed k=2 / k=8 instead of 4 | 12.780 / 12.779 | 12.46 / 12.41 | insensitive |

Learned prior decay (refit of member A on 2015–2025; implied margin points per one-sd preseason differential as current-season evidence accumulates, g = min games played):

| field | sd of diff | g=0 | g=1 | g=2 | g=4 | g=8 | g=12 |
|---|---|---|---|---|---|---|---|
| talent | 182 | 2.27 | 1.75 | 1.34 | 0.77 | 0.22 | 0.02 |
| recruiting (4-class avg) | 52 | 2.49 | 2.02 | 1.66 | 1.15 | 0.66 | 0.48 |
| returning PPA share | 0.34 | 1.05 | 0.78 | 0.58 | 0.30 | 0.02 | −0.08 |
| S−1 SP+ (partial, collinear with S−1 strength) | 15.4 | 5.14 | 4.33 | 3.71 | 2.84 | 2.00 | 1.69 |
| S−1 strength (partial; negative because the decayed state already carries S−1) | 10.1 | −3.59 | −3.19 | −2.88 | −2.44 | −2.02 | −1.86 |
| coach change | 0.59 | −1.94 | −1.75 | −1.59 | −1.38 | −1.18 | −1.10 |

The talent and returning-production effects are gone by 8 games; the prior-strength cluster persists because the state's 0.25 season decay under-weights last season on its own. No decay weight was hand-set: `early_w = exp(−g/4)` is the only functional-form choice and k is insensitive.

State season-decay sweep (struct+pre ridge / tot_eff ridge pooled MAE): 0.70 → 13.276 / 13.494; 0.50 → 12.776 / 13.305; 0.35 → 12.684 / 13.181; **0.25 → 12.676 / 13.124**; 0.20 → 12.695 / 13.105; within-season week decay 0.9 → 12.704 at season decay 0.35 and 12.683 at 0.25 (both worse or tied); ridge 3 or 12 not evaluated (dropped for CPU). By the pre-declared rule (§6) 0.25 and 0.20 tie and the slower decay wins. Long-memory (0.85-decay) strength features improve Week 1 by 0.15–0.25 but worsen pooled MAE by 0.04–0.05 and were not adopted.

## 9. Best total model

**V2-TOTAL** = standardised ridge on the game total with the `tot_eff` set (scoring sum/HFA/mean, 23 efficiency/drive/box **sums**, pace, evidence, neutral; 60 features) on the same decay-0.25 state, followed by a chronological affine map y = a + b·pred fit on prior out-of-sample seasons (b ≈ 0.85–0.91: raw ridge totals are over-dispersed). Pooled 13.107 / bias −0.06 / Wk1 13.15 / Wk4+ 13.14; better than the 0.50-decay ridge in 8 of 8 seasons. Preseason sums add nothing to totals (`tot_eff+pre` 13.36 vs `tot_eff` 13.31); GBMs add nothing; points decomposition is worse (13.33). Totals are the weakest channel: 0.99 better than the zero model, 0.42 better than 0.5.0 on common games, 0.28 worse than the closing market.

## 10. Best winner model

Winner probability from V2-MARGIN and the conditional scale model (§11): pooled log loss 0.534 / Brier 0.180 on the 8 test seasons, reliability within 0.02 in every decile (0.951 predicted → 0.964 observed in the top bin). Direct classifiers were not better (logistic on `full` 0.545, LightGBM classifier 0.549 on the same folds), so the winner channel is derived, not separately fitted — one fewer model to freeze.

## 11. Uncertainty model

Residual scale is modelled as log sd = b0 + b1·|pred margin| + b2·early_w + b3·FCS + b4·pred total (margin) and log sd = b0 + b1·pred total + b2·early_w + b3·|pred margin| (total), fit by Gaussian likelihood on out-of-sample residuals of seasons < Y only. Findings: heteroskedasticity is real but mild — total level is the main driver (sd rises ≈0.4% per point of projected total on both channels), |projected margin| slightly *reduces* margin sd (−0.08% per point), and early-season games are **not** wider once the preseason features are in the mean (early_w coefficient −0.016). A Student-t was profiled on standardised residuals: it never beat the Normal for margins and only marginally for totals (df ≈ 30), so Normal tails are used. Empirical-quantile pricing was implemented and gave the same Brier to the fourth decimal.

#### Uncertainty by test season (conditional-scale model fit on prior out-of-sample residuals)

| season | n | resid sd margin | model sd margin | cov90 margin | resid sd total | model sd total | cov90 total | spread Brier | spread ECE | total Brier | total ECE | winner LL |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 2018 | 772 | 16.534 | 16.165 | 0.877 | 17.079 | 16.274 | 0.881 | 0.1204 | 0.0058 | 0.1708 | 0.0494 | 0.5130 |
| 2019 | 774 | 15.799 | 16.404 | 0.919 | 16.267 | 17.487 | 0.928 | 0.1137 | 0.0112 | 0.1708 | 0.0180 | 0.5061 |
| 2021 | 770 | 16.194 | 16.244 | 0.900 | 16.396 | 17.092 | 0.917 | 0.1208 | 0.0036 | 0.1740 | 0.0261 | 0.5440 |
| 2022 | 776 | 15.782 | 16.170 | 0.919 | 15.972 | 16.719 | 0.932 | 0.1167 | 0.0205 | 0.1746 | 0.0169 | 0.5580 |
| 2023 | 792 | 15.627 | 15.966 | 0.917 | 16.070 | 16.232 | 0.905 | 0.1154 | 0.0098 | 0.1779 | 0.0072 | 0.5313 |
| 2024 | 798 | 16.028 | 15.810 | 0.891 | 16.215 | 16.018 | 0.901 | 0.1190 | 0.0084 | 0.1764 | 0.0262 | 0.5553 |
| 2025 | 808 | 15.476 | 15.967 | 0.903 | 15.781 | 16.353 | 0.912 | 0.1151 | 0.0069 | 0.1800 | 0.0287 | 0.5383 |

Latest scale model (fit for 2025): margin log-sd coefficients {'const': 2.57093, 'abs_pred_margin': -0.00085, 'early_w': -0.01573, 'fcs_involved': 0.0, 'pred_total_level': 0.00398} (Student-t df: None); total {'const': 2.27559, 'pred_total_level': 0.01005, 'early_w': 0.02198, 'abs_pred_margin': -0.00265} (df: 30).

90% interval coverage is 0.88–0.92 on both channels in every season, from a scale model that never saw the season it is applied to. The V1 fixed multipliers (1.20 unknown-QB × 1.02 early × 0.85 global) are replaced by this two-line model.

## 12. Spread probability calibration

#### Spread contract calibration, pooled 2018-2025 (120,780 contracts / 5,490 games, game-equal weighted)

Brier 0.1173, log loss 0.3668, ECE 0.0058; 90-95% events hit 0.917, 95%+ events hit 0.978.

| bin | n | predicted | observed | gap |
|---|---|---|---|---|
| 0.00-0.05 | 37033 | 0.0170 | 0.0176 | 0.0005 |
| 0.05-0.10 | 14577 | 0.0732 | 0.0694 | -0.0039 |
| 0.10-0.20 | 18552 | 0.1463 | 0.1404 | -0.0059 |
| 0.20-0.40 | 22601 | 0.2913 | 0.2803 | -0.0110 |
| 0.40-0.60 | 14276 | 0.4925 | 0.4814 | -0.0110 |
| 0.60-0.80 | 8731 | 0.6913 | 0.6851 | -0.0062 |
| 0.80-0.90 | 2837 | 0.8471 | 0.8326 | -0.0145 |
| 0.90-0.95 | 1137 | 0.9246 | 0.9173 | -0.0072 |
| 0.95-1.00 | 1036 | 0.9759 | 0.9778 | 0.0019 |

- week_1: Brier 0.1120, ECE 0.0097, 95%+ hit 0.9622
- weeks_4_plus: Brier 0.1173, ECE 0.0073, 95%+ hit 0.9779

Contract probabilities use `1 − Φ((T − μ)/σ)` at half-point strikes and `T + 0.5` only at integer thresholds (this also removes V1's half-point double correction). The 0.95–1.00 bin is calibrated (0.976 → 0.978) — the exact failure mode of the Week-0 review.

## 13. Total probability calibration

#### Total contract calibration, pooled 2018-2025 (76,860 contracts / 5,490 games, game-equal weighted)

Brier 0.1750, log loss 0.5251, ECE 0.0074; 90-95% events hit 0.936, 95%+ events hit 0.962.

| bin | n | predicted | observed | gap |
|---|---|---|---|---|
| 0.00-0.05 | 1041 | 0.0295 | 0.0519 | 0.0224 |
| 0.05-0.10 | 2160 | 0.0770 | 0.0986 | 0.0216 |
| 0.10-0.20 | 6707 | 0.1524 | 0.1588 | 0.0064 |
| 0.20-0.40 | 15441 | 0.3002 | 0.2945 | -0.0058 |
| 0.40-0.60 | 15214 | 0.5006 | 0.4901 | -0.0105 |
| 0.60-0.80 | 17718 | 0.7050 | 0.7064 | 0.0014 |
| 0.80-0.90 | 11952 | 0.8518 | 0.8609 | 0.0091 |
| 0.90-0.95 | 5487 | 0.9231 | 0.9358 | 0.0128 |
| 0.95-1.00 | 1140 | 0.9607 | 0.9623 | 0.0016 |

- week_1: Brier 0.1791, ECE 0.0174, 95%+ hit 0.8571
- weeks_4_plus: Brier 0.1739, ECE 0.0088, 95%+ hit 0.9645

The affine recalibration is what fixes the middle bins (without it total ECE is 0.015 and the 0.20–0.60 bins over-predict "over" by 2–3 points). Week-1 total ECE is 0.017 (V1: 0.052).

## 14. Early-season performance

Week 1 (FBS-vs-FBS, 8 seasons, n≈376): V2-MARGIN 12.39 MAE / bias −0.25 vs 0.5.0's 14.12 and 0.4.0's 15.96 on the 2021–2025 subset (§19: V2 wins Week 1 in 4 of 5 common seasons; 2025 Week 1 is the exception, 13.6 vs 13.2, n=47). Weeks 1–3: 12.79. Spread Brier in Week 1 0.112 with ECE 0.010 and 95%+ events hitting 0.962; winner log loss in Week 1 is not worse than the season average. Weeks 2–3 are the hardest regular-season weeks for V2 (12.96), as the preseason terms fade before the state has evidence.

## 15. Late-season performance

Weeks 4–15: 12.53 MAE, bias −0.1 to −0.5 (slight under-projection of home teams late in the season), spread ECE 0.007. Postseason (bowls/CFP, n=335): 13.63 with bias +0.76 — bowls are the worst segment for every candidate and for the market; opt-outs and motivation are not observable in free data.

## 16. Stability by year

Margin MAE by test season for V2-MARGIN vs the best single member and the market:

| season | 2017 | 2018 | 2019 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|
| V2-MARGIN | 12.77 | 12.98 | 12.52 | 13.09 | 12.48 | 12.40 | 12.64 | 12.22 |
| struct+pre ridge (0.25) | 12.82 | 12.99 | 12.57 | 13.13 | 12.52 | 12.48 | 12.69 | 12.24 |
| struct+pre ridge (0.50) | 13.04 | 13.13 | 12.63 | 13.27 | 12.47 | 12.50 | 12.82 | 12.38 |
| closing market | 12.18 | 12.62 | 12.16 | 12.54 | 12.02 | 12.12 | 12.07 | 11.82 |
| V2-TOTAL | 14.20 | 13.41 | 12.98 | 12.94 | 12.83 | 12.96 | 12.83 | 12.86 |
| market total | 13.71 | 12.92 | 12.83 | 12.57 | 12.06 | 12.78 | 12.74 | 12.54 |

No season is an outlier; the ranking of candidates is the same in every season, and the gap to the market is 0.3–0.6 in every season.

## 17. Biggest failure modes

#### Failure analysis

Margin residual (projected − actual) by week bucket:

| week_bucket | n | mae | bias |
|---|---|---|---|
| wk1 | 376 | 12.39 | -0.42 |
| wk2-3 | 782 | 12.95 | -0.01 |
| wk4-8 | 2176 | 12.52 | 0.07 |
| wk9-15 | 2597 | 12.53 | -0.46 |
| post | 335 | 13.70 | 0.84 |

By projected favourite size (signed bias > 0 = favourite over-projected):

| fav_bucket | n | mae | bias | signed_fav_bias |
|---|---|---|---|---|
| 0-3 | 1140 | 12.57 | -0.12 | 0.19 |
| 3-7 | 1422 | 12.45 | -0.07 | 0.57 |
| 7-14 | 1851 | 12.61 | -0.10 | -0.07 |
| 14-21 | 1037 | 13.05 | 0.20 | -0.11 |
| 21-28 | 475 | 12.72 | -0.94 | 0.68 |
| 28+ | 341 | 12.39 | -0.75 | 0.90 |

Total residual by projected total level:

| tot_bucket | n | mae | bias |
|---|---|---|---|
| <45 | 386 | 11.74 | -1.60 |
| 45-52 | 1663 | 12.37 | 0.06 |
| 52-58 | 2123 | 12.98 | 0.86 |
| 58-64 | 1422 | 13.61 | 0.93 |
| 64+ | 672 | 15.23 | 2.28 |

Teams most under-projected (negative = team did better than projected) / over-projected:

| team | n | bias | mae |
|---|---|---|---|
| James Madison | 47 | -9.40 | 17.12 |
| Ohio | 97 | -4.80 | 12.19 |
| Kansas State | 95 | -4.58 | 12.70 |
| Notre Dame | 105 | -4.17 | 13.03 |
| Kennesaw State | 24 | -4.14 | 13.67 |
| Navy | 95 | -4.03 | 13.64 |
| Sam Houston | 36 | 5.46 | 12.20 |
| Massachusetts | 86 | 5.31 | 13.67 |
| Toledo | 98 | 3.99 | 13.26 |
| Maryland | 93 | 3.77 | 15.54 |
| Florida International | 92 | 3.62 | 16.73 |
| Tulsa | 91 | 3.20 | 12.75 |

Largest margin errors:

| season | week | home | away | pred | actual |
|---|---|---|---|---|---|
| 2018 | 13 | Duke | Wake Forest | 12.1 | -52 |
| 2022 | 13 | Liberty | New Mexico State | 23.6 | -35 |
| 2023 | 17 | Florida State | Georgia | -2.9 | -60 |
| 2017 | 12 | Georgia Southern | South Alabama | -4.6 | 52 |
| 2023 | 17 | Syracuse | South Florida | 11.0 | -45 |
| 2017 | 10 | Iowa | Ohio State | -24.5 | 31 |
| 2018 | 17 | Army | Houston | 1.0 | 56 |
| 2022 | 1 | Hawai'i | Vanderbilt | 1.0 | -53 |

Top ridge standardised coefficients (refit on seasons < 2025):

| feature | coef |
|---|---|
| diff_dr_pts_per_drive | 2.831 |
| str_margin_diff | 2.715 |
| diff_pts_for | 2.715 |
| early_x_sp_prev_rating_diff | 2.222 |
| pre_prev_pf_diff | -1.991 |
| diff_o_sr | 1.721 |
| early_x_recruit_avg4_diff | 1.351 |
| diff_o_pass_sr | -1.279 |
| pre_recruit_avg4_diff | 1.140 |
| early_x_talent_diff | 1.103 |
| h_games | -1.083 |
| pre_ret_usage_diff | 1.071 |

Persistent team-level residuals exist (James Madison −10.6, Ohio −5.3, Kansas State −4.4 under-projected; Sam Houston +5.9, UMass +5.6 over-projected): FBS newcomers and programs on multi-year trajectories that a 0.25-decay state plus one-season priors track late. Within-season residuals are mildly persistent (correlation 0.10 with a team's season-to-date mean residual) but the tested within-season week decay made things worse, so the exploitable part is small. The largest single errors are blowouts in rivalry/late-season games (Duke–Wake Forest 2018, Liberty–NMSU 2022, FSU–Georgia 2023 bowl) that no pregame feature anticipates. Neutral-site games carry a +1.1 home-side bias (the "home" designation at neutral sites is not neutral); conference games are 0.4 easier than non-conference.

## 18. Ensemble findings

Chronological NNLS ensembles over persisted out-of-sample predictions: linear members (struct+pre, eff+pre, full) 12.765 vs 12.776 best member on the 0.50 state; adding LightGBM members 12.785 (worse); adding the external Elo 12.672. On the 0.25 state the two-member equal-weight mean gives 12.635 vs 12.669/12.676 (learned NNLS weights 12.633 — indistinguishable, so the deterministic equal weight is frozen). Total ensembles gain 0.06. Residual boosting on top of ridge finds nothing. Conclusion: complementary signal is tiny; V2 uses the one ensemble that wins every season and nothing else.

## 18b. Market as benchmark, not a feature (Phase 11)

On the 3,771 FBS-vs-FBS games of 2021–2025 with closing spread, total and moneyline:

| | V2 | closing market | paired Δ (V2 − market) |
|---|---|---|---|
| margin MAE | 12.545 | 12.115 | +0.431 [0.303, 0.564] |
| total MAE | 12.887 | 12.609 | +0.279 [0.171, 0.391] |
| winner log loss | 0.5617 | 0.5475 | market better |
| opening spread MAE | — | 12.246 | V2 (12.531) does not beat the opener either |

| season | n | V2 margin | mkt margin | V2 total | mkt total | V2 LL | mkt LL |
|---|---|---|---|---|---|---|---|
| 2021 | 721 | 13.03 | 12.49 | 12.92 | 12.63 | 0.5629 | 0.5497 |
| 2022 | 708 | 12.44 | 12.08 | 12.92 | 12.23 | 0.5906 | 0.5892 |
| 2023 | 772 | 12.42 | 12.13 | 12.98 | 12.80 | 0.5433 | 0.5232 |
| 2024 | 787 | 12.68 | 12.11 | 12.87 | 12.83 | 0.5614 | 0.5424 |
| 2025 | 783 | 12.19 | 11.78 | 12.75 | 12.51 | 0.5531 | 0.5369 |

V2 and the market correlate 0.93 on margins. Where they disagree by 7+ points (n=363), the market is right (12.42 vs 14.77 MAE) and V2's side of the disagreement wins 48.5% of the time. Picking against the closing spread whenever V2 differs by ≥3 points goes 850–834 (50.5%), i.e. no edge. Blending V2 into the market never lowers MAE below the market alone. **V2 contains no demonstrable information beyond the closing line; it is a much better football model than 0.5.0, not a betting edge.**

## 19. V2 vs 0.5.0

#### V2 vs 0.5.0 on the 3,675 common games (2021-2025)

| family | V2 Brier | 0.5.0 Brier | 0.4.0 Brier | V2 ECE | 0.5.0 ECE | V2 95%+ hit | 0.5.0 95%+ hit | delta Brier (V2-0.5.0) |
|---|---|---|---|---|---|---|---|---|
| spread | 0.1166 | 0.1308 | 0.1325 | 0.0076 | 0.0149 | 0.979 | 0.986 | -0.0143 [-0.0162, -0.0123] improves |
| total | 0.1762 | 0.1830 | 0.1830 | 0.0100 | 0.0138 | 0.953 | 0.913 | -0.0068 [-0.0086, -0.0049] improves |

- week_1 spread: V2 0.1120 vs 0.5.0 0.1253, delta -0.0133 [-0.0201, -0.0063] improves
- week_1 total: V2 0.1842 vs 0.5.0 0.1883, delta -0.0041 [-0.0104, +0.0021] flat
- weeks_4_plus spread: V2 0.1179 vs 0.5.0 0.1320, delta -0.0141 [-0.0162, -0.0120] improves
- weeks_4_plus total: V2 0.1754 vs 0.5.0 0.1825, delta -0.0070 [-0.0092, -0.0049] improves

- margin MAE delta V2−0.5.0: -1.377 [-1.602, -1.146] (improves)
- total MAE delta V2−0.5.0: -0.462 [-0.616, -0.312] (improves)
- winner log loss: V2 0.5370 (Brier 0.1815) vs 0.5.0 closed-form 0.6002 (Brier 0.2079) vs 0.4.0 simulated 0.6047; delta -0.0632 [-0.0736, -0.0524]

| season | n | margin_mae_v2 | margin_mae_050 | wk1_margin_mae_v2 | wk1_margin_mae_050 | total_mae_v2 | total_mae_ctrl |
|---|---|---|---|---|---|---|---|
| 2021 | 721 | 13.04 | 14.59 | 13.68 | 15.73 | 12.82 | 13.04 |
| 2022 | 723 | 12.46 | 13.22 | 12.12 | 14.85 | 12.66 | 13.14 |
| 2023 | 739 | 12.14 | 13.41 | 11.26 | 12.73 | 13.01 | 13.76 |
| 2024 | 741 | 12.75 | 14.26 | 13.46 | 14.00 | 12.76 | 13.17 |
| 2025 | 751 | 12.21 | 13.97 | 13.61 | 13.22 | 12.67 | 13.11 |

Every acceptance criterion (§14 of the mission) is met on the common 2021–2025 games: margin MAE (−1.3, every season), total MAE (−0.4, every season), winner probability (log loss 0.600 → 0.537), spread calibration (Brier −0.014, ECE 0.015 → 0.007), total calibration (Brier −0.007, ECE 0.014 → 0.010). The one flat cell is Week-1 total Brier (−0.003 [−0.010, +0.003]).

## 20. Whether any V2 component deserves promotion

* **V2-MARGIN — promote.** Wins every season against 0.5.0 by 0.7–1.7 points, repairs the Week-1 compression without a hand-fitted correction, and its spread probabilities are calibrated into the 95%+ tail.
* **V2-WINNER — promote** (derived from V2-MARGIN + scale model): log loss 0.538 vs 0.600.
* **V2-TOTAL — promote as an improvement, not as a solved channel.** −0.42 MAE and −0.007 Brier vs 0.5.0 in every season, calibrated after the affine step, but still 0.28 behind the market and flat in Week 1. It should carry a "weakest channel" flag in provenance and the total ladder should not be treated as edge-bearing.
* **Uncertainty layer — promote** (replaces four fixed multipliers with a two-line conditional scale; 90% coverage 0.88–0.92 out of sample).
* Not promoted: external CFBD Elo (+0.02, availability risk), LightGBM anything, points decomposition, Platt/isotonic layers, long-memory features, within-season decay.

## 21. What remains missing

Quarterback identity and availability, injuries, transfer-portal as-of snapshots, pregame weather forecasts, coordinator changes and opt-outs are all unavailable in free, timing-safe form. The failure analysis says these are exactly where the residual variance lives: the market's 0.4-point advantage is concentrated in the ≥7-point disagreements, which is where roster news would sit. The portal endpoint has event dates and could be made timing-safe with a documented as-of rule, but its ratings/destinations are revisable and it was left out.

## 22. Estimated ceiling with current free data

The evidence puts the free-data ceiling at roughly the current V2: every model family on the same features lands within 0.3 points, the state and preseason sweeps are at their optimum, ensembles add 0.04, and the external Elo (a different, longer-memory encoding of the same scores) adds 0.02. The closing market is 0.43 ± 0.13 points better on margins, 0.28 ± 0.11 on totals and ≈0.014 better in winner log loss; the opening line alone is 0.29 better than V2. Closing that gap needs information that is not in CFBD's free tier, not a better learner.

## Week 0 2026 diagnostic (already-seen evidence, never validation)

V2-MARGIN member A refit on ≤2025 and applied to the eight FBS-vs-FBS games of 2026-08-29/30 has a margin MAE of 17.0 (Virginia–NC State and NDSU–Jacksonville State were 26-point misses in the same direction as the market). Eight games say nothing statistically and this number was computed after every selection above was frozen; it is recorded so nobody has to ask.

---

## V2 VERDICT

**A. V2 CLEARLY BETTER THAN 0.5.0 — READY FOR PRODUCTIONIZATION (as a projection model). NOT a demonstrated betting edge: V2 remains behind the closing market on every channel and shows no against-the-spread edge.**

Promote V2-MARGIN, V2-WINNER and the uncertainty layer as one version, and V2-TOTAL as an explicitly-flagged weakest component (improved, calibrated, still sub-market). Production must not change 0.5.0 observations; V2 starts prospectively at its freeze. The productionization plan and the files it touches are in §23. What would actually be needed to beat the market is roster/availability information the free data does not carry (§21).


## 23. Proposed production architecture (for a separate, reviewed productionization mission)

Nothing below has been implemented in production. It is the narrow, deterministic implementation the evidence supports.

**Keep separate**: data ingestion (CFBD → durable cache), team-state fitting (slow lane), model artifact (frozen coefficients), probability layer (scale model + pricing), market semantics (Kalshi ticker → threshold event, unchanged), execution economics (unchanged).

1. **Slow lane — state + artifact build** (once per day, or after each completed slate): from the durable history (2014→current season, completed games only, `assert_strictly_before` on every row) fit the team state at the current as-of with the frozen `StateConfig`, build the preseason table for the current season from the preseason cache, and write one artifact: `{model_version, config_hash, feature_set_hash, state_config, training_cutoff: (season, week), ridge coefficients/means/stds/fill medians, state offense/defense tables, scale-model coefficients, artifact_sha256}`. Measured cost: history load ≈10 s, one state fit ≈1–7 s (depending on CPU contention), ridge fit 0.05 s, artifact ≈135 KB pickled / 220 KB JSON. Training/refit of the ridge coefficients themselves happens only at the freeze (and then at most once per season, on a declared schedule), never inside the collector.
2. **Fast lane — inference** (every 5-minute scan): load the artifact, build the ≤40 matchup features for each upcoming game from the state tables + preseason table + schedule row (rest, travel, neutral), predict margin and total, compute the conditional sd, and price contracts. Measured: 6 µs per game for the ridge, 0.08 s to build features for a 100-game slate. CFBD requests: 0 (the schedule refresh is unchanged).
3. **Probability layer**: `prob_greater(pred, sd, T)` with the continuity correction applied only to integer thresholds (half-point Kalshi strikes are used as-is; this also fixes the V1 half-point double-correction), winner probability from the same margin distribution, total probabilities from the total model with its own conditional sd. No Platt/isotonic layer (none earned its keep).
4. **In-season learning**: the state is refit as completed games land, so the model updates weekly by construction. The preseason terms decay through `early_w = exp(−min_games/4)` inside the frozen coefficients — no hand-set schedule.
5. **Provenance on every observation**: `model_version="0.6.0-v2-..."`, `config_hash`, `feature_set_hash`, `artifact_sha256`, `training_cutoff`, `state_as_of`. The version is derived from the artifact actually loaded, mirroring how 0.5.0 resolves from whether the talent prior ran.

**Files that would change** (all additive, behind a version switch; 0.5.0 stays byte-identical):
`src/cfb_edge_finder/modeling/v2_state.py` (port of `research/v2/state.py`), `modeling/v2_features.py` (the frozen feature list), `modeling/v2_artifact.py` (load/validate/hash), `modeling/v2_projection.py` (GameDistribution from margin/total + sd, replacing the simulation for V2 rows), `projections/distribution.py` (integer-only continuity correction, behind a flag), `research/football_state.py` (history seasons 2014→current, include the current season's completed games; add advanced/box/drive compaction fields the state needs), `scripts/research_scan_and_capture.py` (artifact loading + version resolution + provenance), `scripts/fetch_v2_research_cache.py` (nightly incremental current-season pull: ≈4 calls per refresh), tests for the leakage assertions, artifact hashing and the version boundary.

**Migration / prospective freeze plan**
1. Freeze the V2 spec and artifact on the research branch (config hash, feature hash, training cutoff = all completed games through 2025 postseason + the 2026 preseason tables; **no 2026 game result enters the artifact**).
2. Productionize behind a new version id in a separate reviewed PR; run the collector in shadow for at least one full slate (V2 rows written as a sidecar, never replacing 0.5.0 rows).
3. Promote by switching the stamped version only after the shadow run reproduces the research projections bit-for-bit for the same as-of; 0.5.0 observations are never rewritten.
4. From the freeze timestamp forward, V2 observations are prospective evidence; the 2026 weeks already played before the freeze are never evaluated as validation.
5. Weekly: refit the state (cheap) with the frozen config; never refit ridge coefficients or the talent/preseason coefficients on 2026 data during the season.

**Quota**: the nightly current-season pull adds ≈4 metered calls/day; the September budget (358 remaining at freeze) covers it, but the collector's own burn should be watched.
