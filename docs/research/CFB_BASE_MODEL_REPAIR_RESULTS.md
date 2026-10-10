# CFB base-projection repair + run-defense reintegration — Wave 2E — RESULTS

**Label:** `RETROSPECTIVE_MODEL_REPAIR`.

| Item | Value |
|---|---|
| Protocol | `docs/research/CFB_BASE_MODEL_REPAIR_PROTOCOL.md`, committed **alone** at `16d0e324` (2026-10-10T06:42:13Z), SHA-256 `f446a855…766c` |
| Starting main | `c45f52c5` |
| Report | `data/scripting/validation/base_repair/base_repair_report.json`, schema `cfb_base_model_repair/1.0.0`, SHA-256 `5e7e4b87…b7b`. Build and analysis both rerun byte-identically. |

Unless stated, every number is FBS-vs-FBS regular season over the primary folds 2018–2025 (n 5,618).

## Decisions

| Area | Decision | Reason |
|---|---|---|
| Base model | **KEEP_CURRENT_BASE** | No candidate passes all of G1–G14 (details below) |
| Run defense | **REJECT_RUSH** | Retested on the research-diagnostic base B2; fails G6 again |
| Production | Unchanged | No new model version, no new prospective stream |

**The base candidates:**
* **B1** (current-season ingestion only) beats the current model on every accuracy metric:
  * MAE −0.43 [−0.50, −0.37];
  * RMSE −0.52;
  * Brier −0.0069;
  * slope 0.79 → 0.84.

  It fails G5 and G8 because the stack is still over-extended (slope 0.84, outside [0.90, 1.10]).
* **B2** (complete-stack recalibration) passes 13 of 14 gates, with pooled slope 1.009 and MAE −0.52. It fails G9:
  its 2023–2025 slope is 1.15, so it over-shrinks recent seasons.
* **B3** fails G5 (slope 0.89) and G8.

**Run defense on B2:** the signal is undiminished (+3.54 points per SD). Accuracy improves again (R1 MAE −0.20, R2
−0.38), but the rush term pushes a now well-calibrated base's favourite bins to −1.5 / −2.3, failing the unchanged G6.

## D. Live pipeline trace (starting main `c45f52c5`)

**Correction to Wave 2D.**
* **RUN CFB does not consume this engine.**
  * `docs/MODEL_RETIREMENT_2026.md` retired the projection model from the live betting path.
  * `docs/RUN_CFB_CONTRACT.md`: "The repository NEVER provides the football projection".
  * The live path is the Kalshi market catalog (`kalshi-market-catalog.yml`).
* `research-capture.yml` (the engine's only scheduled consumer) is **hibernated**: its schedule is commented out.

**The engine fails closed even when dispatched.**
* The last research-data heartbeat (2026-09-25) records:
  * `FOOTBALL_STATE_STALE_HARD`: history fetched 2026-09-03, with a 14-day hard limit;
  * `CFBD_QUOTA_EXHAUSTED` (1,000 / 1,000 for the month);
  * no captures since about 2026-09-17.

**Engine graph (when run):**

| # | Stage | Input data | Training window | As-of contract | Version | Current season enters? |
|---|---|---|---|---|---|---|
| 1 | `research-capture.yml` → `research_scan_and_capture.py` | CLI `--history-seasons 2022 2023 2024 2025` | — | run time | — | — |
| 2 | `football_state.resolve_football_state` | CFBD games + advanced for **history seasons only**; schedule season = schedule rows | 2022–2025 | soft 24 h, hard 14 d | `football_state_v1` | **no** (no current-season advanced fetched) |
| 3 | `FootballState.to_scan_inputs().lines_loader` → `build_team_game_lines` | history bundles only | 2022–2025 | captured_at | — | **no** |
| 4 | `GameProjectionCache._ratings_and_pool_for_as_of` → `fit_fbs_efficiency_ratings` | `TeamGameLine`s strictly before (season, week) | pooled 4 seasons | `AsOf` strictly-before (correct) | ridge λ 10, pooled FCS, matchup pace | no (nothing to admit) |
| 5 | `build_expanding_residual_pool` | the same lines, walk-forward | 2022–2025 | strictly before | — | no |
| 6 | `project_game` | ratings, pool | — | — | residual scale 0.85 | — |
| 7 | `apply_margin_correction` | frozen artifact | 2022–2025 (from the C.2 backtest) | as-of ≥ (2026, 0) | `c2-margin-linear-v1-2022-2025` (a 1.341, b 0.812) | — |
| 8 | Talent prior | preseason cache `talent_composite` | β fit 2021–2023 | season S−1 cycle | `talent-margin-prior-v1` | — |
| 9 | `to_game_distribution` → `price_market` | Normal margin and total | — | — | pricing 0.1.0 | — |
| 10 | `resolve_model_version` | talent active? | — | — | `0.5.0-early-season-talent-prior` | — |

## E. Current-season ingestion root cause

Each link was checked against the code:
1. The CLI default history list excludes the schedule season.
2. `build_football_state` fetches CFBD games and advanced stats **only** for the history seasons. The schedule season
   contributes no advanced rows (no `plays`), so no current-season `TeamGameLine` can be built.
3. `lines_loader` iterates the history seasons only.

**Not the cause:**
* the as-of filter (correct);
* the cache, which is per run and keyed by as-of: a test shows a fresh instance with new lines rebuilds.

**Compounding operational causes:** CFBD quota exhaustion and the 14-day hard staleness bound left the engine
fail-closed. It is hibernated anyway (model retirement).

## F–G. Current-season ingestion fix and leakage proof (research implementation)

**Source.** The current-season source is the production ESPN game log, bridged to `TeamGameLine`:
* CFBD id == ESPN id;
* slugs, week, classification and CFBD home designation come from the football state's CFBD schedule;
* points come from the log;
* `plays` = box rush_att + pass_att.

**Source-contract check (pre-registered).**
* CFBD advanced plays − box attempts = +0.74 (SD 4.1) per team-game.
* Replaying B1 with the current season on the box definition changes MAE by −0.015 and slope by +0.0009, inside
  ±0.02 / ±0.01. **Accepted.**

**Leakage.** `AsOf` strictly-before admits only earlier weeks. Tests prove:
* week 1 is identical to the stale model;
* the week-6 training rows rise by exactly the completed games;
* the projection moves with that evidence;
* the target, same-week and future games never alter an earlier snapshot;
* same data reuses, changed data rebuilds;
* neutral-site and CFBD-home orientation are kept;
* FCS rows are pooled as before.

**Week-1 check:** 0 of 331 week-1 games differ between B1 and P0, as expected.

## H–J. P0 vs B1 (walk-forward)

| Model | MAE | RMSE | Bias | Slope | Intercept | Brier | Log loss | FAV_TAIL | DOG_TAIL | 90 % coverage |
|---|---|---|---|---|---|---|---|---|---|---|
| P0 (old live) | 14.62 | 18.40 | −0.20 | **0.791** | 0.72 | 0.2107 | 0.6066 | −3.87 | +0.058 | 0.909 |
| B1 in-season | **14.18** | **17.89** | −0.27 | 0.844 | 0.42 | **0.2038** | **0.5903** | −3.07 | +0.044 | 0.915 |

B1 − P0 ΔMAE = **−0.435 [−0.497, −0.371]**.

**B1 by season (ΔMAE):**

| Season | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|
| ΔMAE | −0.63 | −0.46 | −0.36 | −0.25 | −0.38 | −0.43 | −0.50 | −0.45 |

B1 improves every season.

**By week (MAE, P0 → B1):**

| Weeks | P0 | B1 | Note |
|---|---|---|---|
| Week 1 | 13.77 | 13.77 | identical, as expected |
| Weeks 2–3 | 14.15 | 14.07 | |
| Weeks 4–5 | 13.86 | 13.59 | |
| Weeks 6+ | 14.94 | 14.37 | |

**Totals.** The total model is unchanged, but its inputs update: total MAE 13.88 → 13.66, bias −1.47 → −1.33.

## K. Margin-calibration layer diagnosis (out of sample, primary folds)

| Stage | Slope | Intercept | MAE | RMSE | FAV_TAIL |
|---|---|---|---|---|---|
| P0 raw ratings | 0.981 | 0.44 | 14.63 | 18.44 | −0.60 |
| P0 raw + talent | 0.867 | 0.53 | 14.51 | 18.28 | −2.26 |
| P0 old C.2 | 0.873 | 0.71 | 14.68 | 18.49 | −2.15 |
| **P0 old C.2 + talent (current live)** | **0.791** | 0.72 | 14.62 | 18.40 | −3.87 |
| B1 raw ratings | 1.070 | 0.03 | 14.20 | 17.94 | +1.16 |
| B1 raw + talent | 0.926 | 0.22 | 14.11 | 17.80 | −1.30 |
| B1 old C.2 | 0.949 | 0.33 | 14.22 | 17.95 | −1.12 |
| B1 old C.2 + talent | 0.844 | 0.42 | 14.18 | 17.89 | −3.07 |
| B2 (diagnostic base) | 1.009 | −0.02 | 14.09 | 17.80 | +0.34 |
| B3 | 0.893 | 0.42 | 14.15 | 17.85 | −2.11 |

**What creates the over-extension.**
* The raw ratings are close to calibrated: 0.98 stale, 1.07 in-season.
* Over-extension is created by **two stacked amplifiers**:
  * the C.2 linear stretch (a > 1);
  * the talent delta, applied at full weight all season on ratings that already encode team strength.
* Each alone costs roughly 0.08–0.12 of slope; together they produce 0.79.

## L. Old C.2 artifact audit

**Per-season refits** of the C.2 procedure (in-season raw margins, prior 4 seasons):

| Fold | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| a | 1.66 | 1.39 | 1.30 | 1.23 | 1.17 | 1.14 | 1.08 | 1.04 | 0.98 | 0.98 |

**The trend.** The compression the artifact corrects existed only while history was short. With a full 4-season
window, a ≈ 1.

**The live artifact on 2022–2025 margins.** The artifact (a 1.341, b 0.812) was fit on margins from a C.2 backtest
whose corpus *started* in 2022. Applied to 2022–2025:

| Margins | Raw slope | Raw MAE | With artifact: slope | With artifact: MAE |
|---|---|---|---|---|
| B1 raw | 1.04 | 13.88 | **0.78** | **14.12** |
| P0 raw | 0.93 | 14.33 | 0.69 | 14.68 |

**The artifact is stale for the current architecture: it makes accuracy worse.**

## M. Talent-prior composition

**Talent-active seasons:**
* adding talent moves the B1 raw slope 1.07 → 0.90 and C.2 0.98 → 0.84;
* yet raw + talent has the best unrecalibrated MAE, 13.99.

**Inactive seasons:** C.2 alone gives 0.86.

**Reading:** talent carries real information. The defect is composition: it is stacked on the C.2 stretch and never
decays during the season.

## N. B2 complete-stack recalibration

**Fold (a, b):**

| Fold | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|
| a | 0.91 | 0.88 | 0.87 | 0.81 | 0.85 | 0.82 | 0.80 | 0.78 | 0.78 |

**Production analogue** (2022–2025): a 0.839, b 0.990.

**Pooled results:**
* MAE 14.09 (−0.52 [−0.62, −0.43]);
* slope 1.009;
* FAV_TAIL +0.34;
* DOG_TAIL −0.014.

**Slope by fold:**

| Fold | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|
| Slope | 0.95 | 1.04 | 1.08 | 0.86 | 0.92 | 1.11 | 1.11 | **1.26** |

**Fail: G9** (2023–2025 slope 1.15).
* The window carries older, more over-extended seasons into a period whose raw margins have become calibrated.
* By week: slope 1.20 in weeks 1–3 vs 0.89 in weeks 4–5 and 0.95 in weeks 6+. One global slope cannot fit both.

## O. B3 replacement calibration

**Pooled results:**
* MAE 14.15 (−0.46);
* slope 0.893;
* FAV_TAIL −2.11.

**Slope by fold:**

| Fold | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|
| Slope | 0.84 | 0.86 | 0.93 | 0.76 | 0.84 | 1.03 | 1.01 | 1.17 |

**Fails:** G5 and G8.

## P–T. Season, week, calibration and tails

**MAE by season:**

| Season | P0 | B1 | B2 | B3 |
|---|---|---|---|---|
| 2018 | 15.15 | 14.51 | 14.47 | 14.51 |
| 2019 | 14.55 | 14.09 | 13.97 | 14.16 |
| 2020 | 15.12 | 14.76 | 14.62 | 14.69 |
| 2021 | 15.43 | 15.18 | 14.88 | 15.10 |
| 2022 | 14.07 | 13.69 | 13.53 | 13.58 |
| 2023 | 13.91 | 13.49 | 13.38 | 13.37 |
| 2024 | 14.66 | 14.16 | 14.16 | 14.13 |
| 2025 | 14.25 | 13.80 | 13.93 | 13.90 |

**Calibration slope by week block** (P0 / B1 / B2 / B3):

| Weeks | P0 | B1 | B2 | B3 |
|---|---|---|---|---|
| Week 1 | 1.00 | 1.00 | 1.20 | 1.05 |
| Weeks 2–3 | 1.01 | 1.00 | 1.20 | 1.05 |
| Weeks 4–5 | 0.72 | 0.75 | 0.89 | 0.79 |
| Weeks 6+ | 0.71 | 0.79 | 0.95 | 0.84 |

**The over-extension lives in weeks 4+.**

**Winner calibration:**

| | P0 | B1 | B2 | B3 |
|---|---|---|---|---|
| Brier | 0.2107 | 0.2038 | 0.2037 | 0.2038 |
| Log loss | 0.6066 | 0.5903 | 0.5908 | 0.5903 |

**Favourite-direction bias by own |proj| bin:**

| Model | [0,3) | [3,7) | [7,14) | [14,21) | [21,28) | [28,∞) |
|---|---|---|---|---|---|---|
| P0 | −0.12 | −1.84 | −2.57 | −3.35 | −4.84 | −4.21 |
| B1 | −0.47 | −0.99 | −1.99 | −2.50 | −4.04 | −3.60 |
| B2 | +0.36 | −0.93 | +0.10 | −0.19 | +1.43 | +1.03 |

**Variance (unchanged):** realised residual SD 17.8–18.4 against a mean deployed σ of about 19.0–19.1. 90 % coverage
is 0.909–0.918, inside [0.85, 0.95], so the variance is left alone.

## U. Total invariance

* B2 and B3 shift margins zero-sum, so their total equals B1's total exactly (max |Δ| = 0).
* B1's total changes only through data (G10b pass: total MAE −0.22).

## V. FBS/FCS (never pooled; n 857)

* MAE: P0 17.78 → B1 17.69.
* FCS games carry no calibration (δ = 0).
* The FCS bias (+11.5) is unchanged and remains a separate problem.

## W. Base gate scorecard

| Gate | B1 | B2 | B3 |
|---|---|---|---|
| G1 ingestion contract | PASS | PASS | PASS |
| G2 MAE | PASS (−0.43) | PASS (−0.52) | PASS (−0.46) |
| G3 RMSE | PASS | PASS | PASS |
| G4 winner | PASS | PASS | PASS |
| G5 slope [0.90, 1.10] + improvement | **FAIL** (0.844) | PASS (1.009) | **FAIL** (0.893) |
| G6 FAV_TAIL not worse | PASS | PASS | PASS |
| G7 DOG_TAIL | PASS | PASS | PASS |
| G8 fold stability | **FAIL** | PASS | **FAIL** |
| G9 recent 2023–2025 | PASS | **FAIL** (slope 1.151) | PASS |
| G10 totals | PASS | PASS | PASS |
| G11 FCS | PASS | PASS | PASS |
| G12 no market | PASS | PASS | PASS |
| G13 no leakage | PASS | PASS | PASS |
| G14 2026 sanity | PASS (−0.11) | PASS (−0.19) | PASS (−0.12) |

**2026 sanity** (n 279 FBS-vs-FBS finals, current-season rows via the ESPN bridge, frozen C.2 artifact; production
calibrations fit on 2022–2025):

| | P0 | B1 | B2 | B3 |
|---|---|---|---|---|
| MAE | 13.10 | 12.98 | 12.91 | 12.97 |
| Slope | 0.87 | 0.88 | 1.05 | 1.32 |

## X. Decision

**KEEP_CURRENT_BASE.**
* No model version change.
* No artifact promotion.
* No `CFB-MODEL-PROS-002`.

## Z–AH. Run-defense retest

**Base.** B2, the research diagnostic: the slope closest to 1 and the best MAE.

**`x_rd` residual on B2 error:**
* +3.54 points per SD [3.20, 3.95], 8 of 8 seasons positive.
* Wave 2D: +3.30 on old P0; +2.84 on in-season P0.
* **The repair did not absorb the signal.**

| | R1 (`x_rd`) | R2 (`x_rd` + `x_ro`) | R3 (`x_rr`) |
|---|---|---|---|
| ΔMAE [CI] | −0.199 [−0.265, −0.132] | −0.384 [−0.457, −0.309] | −0.002 [−0.013, +0.009] |
| ΔRMSE | −0.30 | −0.53 | −0.003 |
| ΔBrier | −0.0043 | −0.0075 | −0.0000 |
| Final β | 3.17 | 3.01 / 2.61 | 0.50 |
| 2026 sanity ΔMAE | +0.05 | −0.15 | −0.07 |

**β by fold (R1):**

| Fold | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| β | 3.17 | 0.97 | 2.25 | 2.65 | 2.72 | 2.95 | 3.06 | 3.06 | 3.02 | 3.07 |

The sign is positive and stable from 2018.

**G6 retest** (favourite-oriented bias by |B2 margin| bin, B2 → R1 / R2):

| Bin | n | B2 | R1 | R2 |
|---|---|---|---|---|
| 0–7 | 2,643 | −0.42 | −0.70 | −0.83 |
| 7–14 | 1,563 | −0.06 | −0.71 | −1.01 |
| 14–21 | 668 | −0.47 | −1.51 | −2.15 |
| 21+ | 345 | +0.25 | −1.59 | −2.32 |

**G6 fails for R1 and R2.** On a well-calibrated base, the rush term over-extends favourites by itself.

**Decision: REJECT_RUSH.**

## AK. Market comparison (descriptive; n 5,219 with a CFBD close)

| | Market | P0 | B1 | B2 |
|---|---|---|---|---|
| MAE | 12.19 | 14.69 | 14.23 | 14.11 |
| Corr with market | — | 0.70 | 0.76 | 0.76 |
| Disagreements ≥ 7 | — | 2,558 | 2,269 | 2,182 |
| Moves toward the market | — | — | 77 % | 68 % |
| Moves toward the actual result | — | — | 63 % | 60 % |

The repaired models move toward both the market and the outcome. The engine stays about 1.9 MAE behind the close.

## Integrity

* No market input to any model.
* Fold fits strictly prior (asserted, and tested by doctoring the future).
* The frozen C.2 artifact is reproduced.
* Totals and variance invariant under calibration.
* The Wave-2 / 2D prospective streams and every production file are pinned and unchanged.
* No new automatic betting behaviour.
