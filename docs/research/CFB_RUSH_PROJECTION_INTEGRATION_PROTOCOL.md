# CFB rush projection integration — Wave 2D Track A protocol (PRE-REGISTERED)

**Label:** `RETROSPECTIVE_MODEL_INTEGRATION`. The rush feature family was discovered on 2014–2025 (Wave 1 / Wave 2C).
The walk-forward fitting below prevents coefficient leakage and estimates practical model benefit. It is **not** an
independent confirmation of feature *selection*; prospective comparison (`CFB-MODEL-PROS-001`, Track B) provides that.

This file is committed **alone**, before any candidate result exists. Nothing below may be changed after results
are seen; any deviation is reported as a deviation.

| Item | Value |
|---|---|
| Starting main | `1e40ecbcbbc714a0b2a96aec7a8a79c1e4388352` |
| Written | 2026-10-10T05:04Z |
| Question | After the CURRENT production projection has used everything it uses today, does the frozen pregame rush / run-defense information predict the model's remaining margin error? |

## 1. The current production model (traced on starting main, not from documents)

**Live entry path.** RUN CFB / Kalshi pricing goes through `scripts/research_scan_and_capture.py`, invoked by
`.github/workflows/research-capture.yml`, then `kalshi.game_projection_cache.GameProjectionCache.get_or_build`.

**Step 1 — ratings and residual pool.**
* `modeling.ratings.fit_fbs_efficiency_ratings` (ridge λ = 10, FCS λ = 4, pooled FCS, `pace_mode="matchup"`).
* `modeling.score_model.build_expanding_residual_pool`.
* Both are fit on `TeamGameLine` rows from the **history seasons only**: default `--history-seasons 2022 2023 2024 2025`.
* The durable football state `research-data:data/research/football_state/2026.json` records
  `history_seasons=[2022, 2023, 2024, 2025]` and history fetched 2026-09-03T00:35Z.
* **Current-season (2026) games never enter the ratings.** The live projection for a matchup is identical in every
  2026 week. This was verified: Texas–UTSA at week 3 and at week 7 give an identical margin, 31.0986.

**Step 2 — `score_model.project_game`.**
* `prior_season_ratings=None`.
* Residual scale 0.85.
* FCS opponent uncertainty scale 0.35.
* Early-season uncertainty scale 0.30.
* QB state unknown.

**Step 3 — `score_model.apply_margin_correction`.**
* Frozen artifact `c2-margin-linear-v1-2022-2025`: linear, a = 1.3413121461524347, b = 0.8117267938452581.
* Applied to FBS-vs-FBS games only.

**Step 4 — talent prior** (`modeling.talent_prior`).
* `talent-margin-prior-v1`: β = 0.018993 × (home − away `talent_composite`).
* FBS-vs-FBS only.
* Active whenever the preseason cache has the season; the 2026 cache is ACTIVE with 137 teams.

**Version and channel shifts.**
* Model version is `0.5.0-early-season-talent-prior` when the talent prior is active, otherwise
  `0.4.0-milestone-c2-live-margin-correction`.
* Ratings component: `ridge_lambda=10;pace_mode=matchup;residual_scale=0.85;fcs_mode=pooled;calibration=platt;fcs_treatment=pooled-shrinkage-v2;margin_correction_method=linear`.
* Margin shifts move home +δ/2 and away −δ/2, so the total is unchanged.

**Pricing.**
* Kalshi prices come from `CorrectedGameProjection.to_game_distribution()`: a Normal margin whose mean includes every
  margin delta, with SDs from the raw simulation.

**Live reproduction (verified before this protocol).**
* Rebuilding the live corpus from the football-state history and the live talent table, then calling
  `GameProjectionCache`, reproduces live 0.5.0 moneyline prices to the last digit.
* Checked on 3 rows from 2026-09-12 (UTSA@Texas 0.9556261988040299, Arkansas State@TCU 0.9390371977741758,
  UAB@Louisiana 0.7456951467426737).

**Corpus parity.**
* `TeamGameLine` rows rebuilt from the CFBD research cache (`research-data@292f3aac` `data/research_cache/v2/<season>`,
  `games.json.gz` + `advanced_regular.json.gz` + `advanced_postseason.json.gz`) equal the live football-state rows
  exactly for 2022–2025 (1,768 / 1,794 / 1,812 / 1,836 rows; zero differing fields).

## 2. P0 — the control (unchanged production arithmetic, reproduced walk-forward)

### P0-LIVE (primary control)

The live configuration, applied historically for evaluation season S:

* **Ratings:** fit once per season from all `TeamGameLine` rows of seasons max(2014, S−4)…S−1. There are no season-S
  rows, which is the live history window.
* **Projection:** production `project_game`, deterministic expected points (`expected_home_points`,
  `expected_away_points`), with `prior_season_ratings=None`.
* **Margin correction:** `margin_calibration.fit_linear_margin` (the exact function behind the frozen artifact).
  * It is fit on P0-INSEASON **raw** FBS-vs-FBS margins of seasons max(2015, S−4)…S−1.
  * This mirrors how the live artifact was produced: from the in-season walk-forward raw margins of the four prior
    seasons.
  * It is applied with `apply_margin_correction` to FBS-vs-FBS only.
* **Talent prior:** applied exactly as live, `talent_margin_delta`, FBS-vs-FBS only, whenever the production
  preseason cache supplies the season (2019, 2021–2026); otherwise 0.
  * β was fit on 2021–2023, so its use in those seasons is disclosed as in-sample for β.
* **2026:** P0-LIVE is the literal live object (`GameProjectionCache` with the live football state, the frozen
  artifact and the live talent table).

### P0-INSEASON (robustness control, pre-registered)

* Identical to P0-LIVE except that the ratings for a game in (S, w) are fit from seasons max(2014, S−4)…S−1 **plus
  season-S rows strictly before week w**. This is the in-season walk-forward architecture under which the margin
  correction and residual scale were validated in C.2.
* It exists because P0-LIVE contains **no** current-season information. A rush correction fit against P0-LIVE could
  be paid for by generic in-season information rather than rush-specific information.

### Probabilities (secondary metrics only)

* Margin SD σ for season S comes from the production scale formula: per side, `uncertainty_multiplier(UNKNOWN)` ×
  (1 + 0.30 × (1 − w)) × 0.85, where w = games / (games + 4) from the ratings snapshot.
* The paired residual covariance comes from P0-INSEASON raw (home, away) residuals of seasons max(2014, S−4)…S−1.
* P(margin > t) = 1 − Φ((t + 0.5 − μ) / σ), with the production continuity correction.
* σ is **identical for P0 and every candidate** (§15 of the brief: variance unchanged).

## 3. Exact source features (Wave 2C, no new feature)

Features are taken from the committed Wave-2C study input:
* file: `data/scripting/validation/signal_mechanisms/games_2014_2025.jsonl.gz`, SHA-256 `e0443bb1…8626`;
* rows: the Wave-1 production-builder pregame features, 04:00 ET cutoff;
* constructs: `signal_discovery.mechanisms.construct`, source SHA-256 `03d82461…2dfe`;
* residualisers: those recorded in `mechanism_report.json` (SHA-256 `57167ae5…6ab9`).

Production values for 2026 come from the Wave-2 `feature_for` builder on the production ESPN log; the Wave-2A pinned
values are in `games_2026.jsonl.gz`, SHA-256 `7cc71635…1607`.

The candidate features are:

| Name | Exact definition | Orientation |
|---|---|---|
| `x_rd` (run defense) | `frozen_z` = (`defdiff.rush_success_rate` − 0.13804024626610417) / 1.4218539928759784, where `defdiff.rush_success_rate` = `home_def_q.rush_success_rate` − `away_def_q.rush_success_rate` (the CFB-PROS-001 feature) | positive = home has the better run defense |
| `x_rr` (rushing residual) | `rush_res_z` = (`net.rushing` − (0.020161006542193327 + 0.8259469722808191 × `net.sustained_efficiency`)) / 1.0375720528416614 | positive = home rushing edge beyond efficiency |
| `x_ro` (rush offense, P3 only) | (`home_off_q.rush_success_rate` − `away_off_q.rush_success_rate` − 0.1341409189374074) / 1.3944072421206335 (Wave-2C `rush_off_diff`, Wave-2C standardisation) | positive = home has the better rush offense |

**Opponent adjustment** is the production `signal_discovery.features.pregame_features` quality model. That is the
Wave-1 builder; nothing is re-derived.

**Feature hygiene:**
* A missing feature means no correction (δ = 0, P0 unchanged).
* No feature reads any market, outcome or postgame field. Tests enforce this.

## 4. Candidates (only these)

| Id | Projection | Parameters |
|---|---|---|
| P0 | current production (P0-LIVE; P0-INSEASON for robustness) | — |
| P1 | P0 + β_rd · `x_rd` | 1 |
| P2 | P0 + β_rr · `x_rr` | 1 |
| P3 | P0 + β_rd · `x_rd` + β_ro · `x_ro` | 2 (valid: Wave 2C froze both `rush_def_diff` and `rush_off_diff` with exact standardisation) |

**Fitting the correction:**
* **No intercept.** Each feature is a home-minus-away difference, so δ(away, home) = −δ(home, away) and the correction
  is orientation-invariant at neutral sites. An intercept would be a home-field term, which is not the hypothesis.
* **Target:** `current_model_error` = `actual_margin` − P0 margin (home perspective; P0 margin includes the C.2
  correction and the talent delta).
* **Method:** OLS without intercept.
* **Training set:** FBS-vs-FBS regular-season games of seasons 2015…S−1 that have the feature.
* **Refit:** once per evaluation season S; never within a season. No in-sample coefficient is ever applied to a game
  in its own fitting set.

**Application:** δ = β · x, applied as home + δ/2, away − δ/2. The total is unchanged by construction.

**Never in the candidate family:** passing, record, Elo, conference, home/away, underdog, CONTROL, market prices,
interactions, week-dependent coefficients.

## 5. Population, folds, boundaries

**Eligible games:** FBS-vs-FBS **regular-season** games present in both the CFBD corpus and the Wave-2C study input,
with final scores.
* Wave 2C has no postseason rows, so postseason is outside the population.
* Feature-missing games stay in the population with δ = 0.

**Folds:**
* Evaluation seasons **2018–2025** are the primary pooled population.
  * Each has a full 4-season P0 ratings window and ≥ 3 seasons of rush training.
* 2016–2017 are reported as burn-in folds.
* 2015 trains P0 corrections only.
* 2026 is a retrospective sanity check only. It is not untouched, and **no refit is based on it.**

**FCS:**
* Any FCS-involved game gets δ = 0, so P0 is preserved exactly.
* FCS rows are reported but never pooled with FBS-vs-FBS.

**Early season:**
* No shrinkage and no SE scaling of the z-score in P1–P3.
* A shadow-only diagnostic may be reported, but it cannot affect selection.

**Margin only:**
* Total mean, residual SD, covariance and residual pool are unchanged.

## 6. Metrics

**Primary** (FBS-vs-FBS, primary folds, candidate − P0):
* margin MAE;
* margin RMSE;
* margin bias (mean actual − projected).

**Secondary:**
* winner log loss and Brier;
* spread-threshold Brier averaged over t ∈ {−21, −14, −10, −7, −3, 0, 3, 7, 10, 14, 21} (push games excluded per
  threshold);
* 50 / 80 / 90 % margin-interval coverage;
* oriented bias by |P0 projected margin| bin ([0, 7), [7, 14), [14, 21), 21+), where **favourite tail** = |proj| ≥ 14
  and **underdog side** = the same bins seen from the projected underdog;
* home / away / neutral bias.

**Inference:** game-level paired differences, with a percentile bootstrap (seed 20261010, 2,000 draws) clustered by
season-week.

**Reports:**
* season-by-season table (season, n, P0 MAE, candidate MAE, Δ, P0 RMSE, candidate RMSE, Δ, bias, winner-Brier Δ);
* era blocks 2014–2019, 2020–2022, 2023, 2024, 2025;
* weeks 1–2, 3–5 and 6+;
* talent-prior-active seasons (2019, 2021–2025) vs inactive seasons (2018, 2020);
* coefficient by training fold;
* the double-counting diagnostic: raw and partial correlations of x with P0 components (offense-rating difference,
  defense-rating difference, talent delta, C.2 delta, expected plays, P0 margin), and the incremental R² for P0
  error.

## 7. Acceptance gates (vs P0-LIVE unless stated)

| Gate | Rule |
|---|---|
| G1 | Pooled ΔMAE < 0, and the 95 % CI upper bound < 0 |
| G2 | Pooled ΔRMSE ≤ +0.02 |
| G3 | ΔMAE ≤ +0.02 in ≥ 6 of 8 primary folds, and < 0 in ≥ 5 of 8; leave-one-season-out pooled ΔMAE < 0 for every left-out season |
| G4 | Recent seasons: 2023–2025 pooled ΔMAE ≤ 0, and no single one of 2023, 2024, 2025 has ΔMAE > +0.05 |
| G5 | Winner Brier Δ ≤ +0.0005 and winner log-loss Δ ≤ +0.0015 (pooled) |
| G6 | In no |P0 margin| bin with n ≥ 200 does the absolute oriented bias increase by more than 0.25 points; home, away and neutral \|bias\| not increased by more than 0.25 |
| G7 | Candidate total == P0 total for every game (max \|diff\| ≤ 1e-9) |
| G8 | Leakage tests pass (pregame-only features, walk-forward coefficients, no market fields) |
| G9 | Every FCS-involved δ is exactly 0 |
| G10 | 2026 retrospective sanity (FBS-vs-FBS games with a feature and a final score): candidate MAE − P0 MAE ≤ +0.50 (no catastrophic failure) |
| G11 | **Practical significance:** pooled ΔMAE ≤ −0.10 points |
| G12 | **Robustness to in-season information:** with P0-INSEASON as the control (coefficients refit against P0-INSEASON error, same rules), G1 and G3 also pass |

**Why 0.10 points (G11).** Pooled FBS-vs-FBS margin MAE for this model is about 12–13 points, so 0.10 is about 0.8 %.

This repository's model-repair convention:
* It promoted a change worth > 1 point of week-1 MAE (the talent prior, −1.89 [−2.47, −1.31]).
* It rejected changes whose effect was statistically indistinguishable from zero (spread calibration layer, Brier
  −0.0003) or fold-unstable (total-bias correction, 2 of 4 folds).

A rush correction adds a new live dependency, a play-log-derived feature with its own failure surface. 0.10 points is
the smallest pooled change worth that complexity. Below it, the correct reading is "real football signal, too small to
justify production complexity."

**G12** guards against promoting a stand-in for current-season information that P0-LIVE does not carry.

## 8. Decision rule

| Outcome | Condition |
|---|---|
| **PROMOTE** | A candidate passes every gate G1–G12. If several pass, pick the best pooled ΔMAE; within 0.01, fewer parameters, then P1 before P2. |
| **SHADOW_ONLY** | No candidate is promotable, but at least one passes G1, G2, G5, G6, G7, G8, G9 and G10 (vs P0-LIVE), and its pooled ΔMAE vs P0-INSEASON is < 0. It is chosen by the same tie-breaks and runs in shadow; official projections do not change. |
| **REJECT** | Otherwise. Nothing is tried next: no other transformation, threshold or feature. |

## 9. Promotion mechanics (only if PROMOTE)

* **New model version:** `0.6.0-rush-margin-correction`. The existing version strings are never reused for changed
  arithmetic.
* **Frozen artifact `cfb_rush_margin_correction/1.0.0`:**
  * selected feature(s);
  * β fit by the §4 rule on all primary-population games 2015–2025 (training cutoff AsOf(2026, 0));
  * standardisation constants;
  * fold results;
  * protocol SHA-256;
  * source SHA-256;
  * applicability FBS-vs-FBS.
* **Live behaviour:**
  * Applied after the C.2 correction and the talent delta.
  * Fails closed to P0 when the feature is unavailable, stale (built from a log older than the frozen cutoff) or
    invalid.
  * Explicit telemetry of whether it was applied.
  * No market input.
  * Total preserved.
  * FCS unchanged.
  * P0 kept as a shadow comparator for every game.
* **Inspection:**
  * Current-slate before/after margins.
  * Every movement ≥ 0.5 points is listed.
  * This is inspection only, never a bet.

If SHADOW_ONLY or REJECT, production model versions and projections are unchanged.

## 10. No-market rule

**P0 and the candidates never read:**
* spread, moneyline, total, opener or close;
* Kalshi or sportsbook prices;
* market residuals.

**Markets may be used only afterwards, descriptively:**
* the CFBD consensus close (`m.spread_home`) for 2014–2025;
* Kalshi PRIMARY_60_180 centres for 2026.

## 11. Frozen and untouched

The following must not change:
* Wave 1;
* Wave 2 rules and files (`CFB-PROS-001`, `CFB-PROS-002`, `wave2.py`, `wave2_cycle.py`, candidates);
* Wave-2A and Wave-2C artifacts;
* Script Engine, CONTROL, V2, Value Watch, SIFT;
* research ledgers.

Tests pin them by SHA-256. Any actionable idea goes only to `docs/research/CFB_PROJECTION_FUTURE_HYPOTHESES.md`,
untested.
