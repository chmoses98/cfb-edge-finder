# CFB rush projection integration — Wave 2D Track A — RESULTS

**Label:** `RETROSPECTIVE_MODEL_INTEGRATION`. This is not independent discovery validation.

| Item | Value |
|---|---|
| Protocol | `docs/research/CFB_RUSH_PROJECTION_INTEGRATION_PROTOCOL.md`, committed **alone** at `9d70580d` (2026-10-10T05:05:03Z), SHA-256 `29d4bfc0…8f62` |
| Starting main | `1e40ecbc` |
| Report | `data/scripting/validation/rush_projection/integration_report.json`, schema `cfb_rush_projection_integration/1.0.0`, SHA-256 `0effb387…2fb`; a rerun is byte-identical |
| Inputs | `p0_games_2014_2025.jsonl.gz` (10,134 games); `p0_games_2026.jsonl.gz` (274 games); `build_manifest.json` |

Units are points of home margin per SD of the frozen feature. Δ is candidate minus P0, so a negative ΔMAE is an
improvement.

## Decision: **REJECT** (by the frozen rule)

**The feature does explain the current model's errors.**
* The run-defense feature (`x_rd`, the CFB-PROS-001 z) predicts P0's own margin error by +3.3 points per SD
  [2.9, 3.8], in 8 of 8 primary seasons.
* Added walk-forward:
  * P1 improves pooled margin MAE by **0.17** [0.08, 0.25];
  * P3 (run defense plus rush offense) improves it by **0.36** [0.25, 0.46];
  * winner Brier improves too.

**Both fail pre-registered gate G6, so neither is promotable or shadow-eligible.**
* G6 forbids worsening the favourite-oriented bias in any projected-margin bin by more than 0.25 points.
* The current model already **over-projects favourites**: its calibration slope is 0.77, so projected margins are
  about 30 % too wide.
* The rush term is positively correlated with team strength (r 0.29 with the P0 margin). It pushes projections
  further out, and the oriented bias worsens by 0.2–3.3 points in every bin.

**P2 (overall rushing residual)** carries essentially no information beyond the current model (ΔMAE −0.003, CI
spans 0).

**Consequences:**
* The production model version and projections are **unchanged**.
* No other transformation, threshold or feature was tried.
* The Wave-2D Track B prospective stream `CFB-MODEL-PROS-001` records the frozen P1 correction as a research-only
  comparison, to see whether the accuracy gain and the tail cost persist on new games.

## C. Starting production model (traced on main `1e40ecbc`)

**Path.**
* `research-capture.yml` runs `scripts/research_scan_and_capture.py`, which calls
  `GameProjectionCache.get_or_build`.
* That builds:
  * ridge points-per-play ratings (λ 10, FCS λ 4 pooled, matchup pace);
  * `project_game` (residual scale 0.85, FCS uncertainty 0.35, early-season scale 0.30, QB unknown);
  * the frozen C.2 linear correction `c2-margin-linear-v1-2022-2025` (a 1.3413, b 0.8117, FBS-vs-FBS);
  * the talent prior `talent-margin-prior-v1` (β 0.018993, FBS-vs-FBS).
* Version: `0.5.0-early-season-talent-prior` (talent cache ACTIVE, 137 teams).
* Kalshi prices come from the Normal margin of `to_game_distribution()`, with every margin delta included.

**Reproduction.**
* The rebuilt live object re-prices **446 live 0.5.0 moneyline observations (55 games)**.
* The maximum difference is 2.2 × 10⁻¹⁶.

**Live finding (not changed here).** The live ratings use only the 2022–2025 history: `--history-seasons` default,
with the football state fetched 2026-09-03. **No 2026 game ever enters the ratings**, so a matchup's projection is
identical in every week. This is reported as `C2D-PF1` in `CFB_PROJECTION_FUTURE_HYPOTHESES.md`.

## E. Wave-2C feature reproduction

| Check | Result |
|---|---|
| `x_rd` | Exactly (`defdiff.rush_success_rate` − 0.13804) / 1.42185, the PROS-001 constants imported from `wave2` |
| `x_rr` | Exactly `mechanisms.construct(...)["rush_res_z"]` with the Wave-2C residualisers |
| `x_ro` | Wave-2C `rush_off_diff` with its Wave-2C standardisation |
| Join | 8,567 Wave-2C rows joined to the CFBD corpus; orientation verified by final score on every row (0 mismatches) |
| Pins | `mechanisms.py`, `wave2.py`, `wave2_cycle.py`, the candidates file and the Wave-2C artifacts are pinned by SHA-256 in `tests/test_cfb_rush_projection.py` |

## F. Current-model residual diagnostic (P0-LIVE, 2018–2025, n 5,218)

Slope of `actual − P0 margin` on the feature (no intercept), season-cluster bootstrap:

| Feature | Points per SD | 95 % CI | Seasons > 0 | Corr with P0 error |
|---|---|---|---|---|
| `x_rd` run defense | **+3.30** | [2.86, 3.77] | 8 / 8 | 0.17 |
| `x_ro` rush offense | +3.41 | [2.59, 4.19] | 8 / 8 | 0.18 |
| `x_rr` rushing residual | +0.82 | [−0.04, 1.55] | 6 / 8 | 0.04 |

Against P0-INSEASON (the same model plus in-season rows), `x_rd` is still +2.84 per SD [2.46, 3.27].

## G. Double counting (P0-LIVE, primary folds)

| | `x_rd` | `x_rr` |
|---|---|---|
| Corr with P0 margin | 0.29 | −0.04 |
| Corr with offense-rating difference | 0.20 | −0.04 |
| Corr with defense-rating difference | 0.29 | 0.00 |
| Corr with talent delta | 0.14 | −0.08 |
| Corr with C.2 delta | 0.22 | −0.02 |
| Corr with expected plays | 0.01 | 0.01 |
| R² of x on all P0 components | 0.095 | 0.008 |
| Partial corr with P0 error given P0 margin | **0.22** | 0.04 |
| Incremental R² of P0 error over P0 margin | **0.049** | 0.0015 |

**Reading:**
* Only about 10 % of the run-defense signal is already in the current model.
* The rushing *residual* (net of efficiency) is nearly orthogonal to P0, and to P0's errors.

## H. P0 control reproduction (walk-forward)

| Season | n | P0-LIVE MAE | P0-LIVE RMSE | P0-LIVE bias | P0-INSEASON MAE |
|---|---|---|---|---|---|
| 2015 | 660 | 14.96 | 19.30 | −0.01 | 14.11 |
| 2016 | 655 | 15.88 | 20.26 | +0.65 | 15.13 |
| 2017 | 668 | 15.82 | 19.63 | −0.19 | 14.94 |
| 2018 | 676 | 15.16 | 19.27 | −0.61 | 14.49 |
| 2019 | 679 | 14.65 | 18.59 | −0.34 | 14.16 |
| 2020 | 425 | 15.30 | 18.94 | −1.94 | 14.88 |
| 2021 | 672 | 15.50 | 18.85 | −1.19 | 15.23 |
| 2022 | 671 | 14.14 | 17.96 | −0.40 | 13.74 |
| 2023 | 688 | 14.00 | 17.65 | −0.09 | 13.54 |
| 2024 | 703 | 14.73 | 18.67 | +0.95 | 14.21 |
| 2025 | 705 | 14.31 | 18.12 | +0.29 | 13.83 |

* This is the same scale as the documented C.2 backtests (margin MAE 14.4–14.6).
* P0-LIVE, with no in-season rows, is 0.27–0.88 MAE worse than the in-season architecture in every season (0.41–0.52
  since 2022).

## I–K. Candidates (P0-LIVE control, pooled primary folds 2018–2025, n 5,219)

| | P1 run defense | P2 rushing residual | P3 run defense + rush offense |
|---|---|---|---|
| ΔMAE [95 % CI] | **−0.168** [−0.252, −0.078] | −0.003 [−0.017, +0.013] | **−0.357** [−0.459, −0.248] |
| ΔRMSE | −0.252 | −0.009 | −0.513 |
| Bias, P0 → candidate | −0.33 → −0.16 | −0.33 → −0.32 | −0.33 → −0.03 |
| ΔBrier (winner) | −0.0051 | −0.0001 | −0.0096 |
| ΔLog loss | −0.0116 | −0.0002 | −0.0222 |
| ΔSpread-threshold Brier | −0.0033 | −0.0001 | −0.0066 |
| Mean \|δ\| | 2.97 | 0.36 | 3.62 |
| Final β (2015–2025) | 3.59 | 0.65 | 3.40 (rd), 3.15 (ro) |
| vs P0-INSEASON ΔMAE | −0.123 [−0.190, −0.051] | −0.003 | −0.252 [−0.331, −0.170] |

P3 was valid: Wave 2C froze both the `rush_def_diff` and the `rush_off_diff` standardisation.

## L. Season by season (P0-LIVE control)

**P1 run defense:**

| Season | n | P0 MAE | P1 MAE | Δ | P0 RMSE | P1 RMSE | Δ | P1 bias | ΔBrier | β |
|---|---|---|---|---|---|---|---|---|---|---|
| 2016* | 655 | 15.88 | 15.70 | −0.18 | 20.26 | 19.97 | −0.29 | +0.95 | −0.0071 | 3.98 |
| 2017* | 668 | 15.82 | 15.23 | −0.59 | 19.63 | 19.00 | −0.63 | +0.19 | −0.0117 | 3.79 |
| 2018 | 676 | 15.16 | 14.96 | −0.20 | 19.27 | 18.89 | −0.38 | −0.50 | −0.0070 | 4.32 |
| 2019 | 679 | 14.65 | 14.60 | −0.05 | 18.59 | 18.44 | −0.15 | −0.19 | −0.0039 | 4.25 |
| 2020 | 425 | 15.30 | 15.00 | −0.31 | 18.94 | 18.46 | −0.48 | −1.79 | −0.0064 | 3.98 |
| 2021 | 672 | 15.50 | 15.16 | −0.34 | 18.85 | 18.58 | −0.27 | −0.99 | −0.0072 | 4.03 |
| 2022 | 671 | 14.14 | 14.08 | −0.07 | 17.96 | 17.83 | −0.13 | −0.22 | −0.0051 | 3.93 |
| 2023 | 688 | 14.00 | 14.02 | +0.02 | 17.65 | 17.58 | −0.07 | +0.19 | −0.0036 | 3.77 |
| 2024 | 703 | 14.73 | 14.53 | −0.21 | 18.67 | 18.43 | −0.24 | +1.08 | −0.0030 | 3.59 |
| 2025 | 705 | 14.31 | 14.07 | −0.24 | 18.12 | 17.77 | −0.36 | +0.46 | −0.0052 | 3.57 |

\* Burn-in folds.

**P2 ΔMAE by season:**

| Season | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| ΔMAE | +0.016 | +0.016 | −0.002 | +0.025 | −0.000 | −0.023 | −0.022 | −0.050 | −0.000 | +0.049 |

* P2's β ranged from −0.30 to +0.79.

**P3 ΔMAE by season:**

| Season | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| ΔMAE | −0.09 | −0.76 | −0.17 | −0.22 | −0.42 | −0.52 | −0.16 | −0.34 | −0.48 | −0.56 |

* P3 improves in every fold.

## M. Recent seasons (ΔMAE)

| Era | n | P1 | P2 | P3 |
|---|---|---|---|---|
| 2014–2019 (2016–2019 evaluated) | 2,678 | −0.26 | +0.01 | −0.31 |
| 2020–2022 | 1,768 | −0.23 | −0.02 | −0.36 |
| 2023 | 688 | +0.02 | −0.05 | −0.34 |
| 2024 | 703 | −0.21 | −0.00 | −0.48 |
| 2025 | 705 | −0.24 | +0.05 | −0.56 |
| 2026 sanity (P0-LIVE as live, n 226) | 226 | **+0.13** | −0.09 | +0.03 |

In 2026 the slope of live P0 error on `x_rd` is only +0.93 per SD, against about +3.6 historically. 2026 is not an
untouched holdout, and nothing was refit on it.

## N. Early vs late season (primary folds)

| Block | n | P0 MAE | P1 ΔMAE | P2 ΔMAE | P3 ΔMAE |
|---|---|---|---|---|---|
| Weeks 1–2 | 329 | 14.01 | −0.15 | +0.06 | −0.16 |
| Weeks 3–5 | 1,155 | 14.08 | −0.06 | +0.00 | −0.14 |
| Weeks 6+ | 3,735 | 14.94 | −0.20 | −0.01 | −0.44 |

## O. Coefficient stability (P1 β by training fold)

| Fold | 2016 | 2017 | 2018 | 2019 | 2020 | 2021 | 2022 | 2023 | 2024 | 2025 |
|---|---|---|---|---|---|---|---|---|---|---|
| β (P0-LIVE) | 3.98 | 3.79 | 4.32 | 4.25 | 3.98 | 4.03 | 3.93 | 3.77 | 3.59 | 3.57 |

* **Sign:** positive in 10 of 10 folds.
* **Magnitude:** range 3.57–4.32. It drifts down about 0.7 since 2019 but does not collapse.
* **Leave-one-season-out pooled ΔMAE:** −0.14 to −0.20 for every left-out season, so no single season drives it.
* **Against P0-INSEASON:** β 2.83–3.35, so roughly 0.8 per SD of the LIVE β stands in for missing in-season
  information.
* **P2:** unstable (β −0.30 to +0.79).

## P. Talent-prior interaction (P1, primary folds)

| Seasons | n | ΔMAE | ΔRMSE |
|---|---|---|---|
| Talent-active (2019, 2021–2025) | 4,118 | −0.15 | −0.21 |
| Talent-inactive (2018, 2020) | 1,101 | −0.24 | −0.42 |

* The gain is smaller but intact once the talent prior is active.
* The comparison is always against the model *with* the prior wherever production has it.

## Q. FBS/FCS

* δ is exactly 0 for all 1,274 FCS-involved rows (G9).
* FCS rows are outside the population, and P0 FCS handling is untouched.

## R–T. Margin metrics, winner calibration, totals

* **Margin metrics:** see I–K above.
* **Interval coverage, P0 → P1 (P3):**

  | Nominal | P0 | P1 | P3 |
  |---|---|---|---|
  | 50 % | 0.519 | 0.522 | 0.528 |
  | 80 % | 0.813 | 0.818 | 0.828 |
  | 90 % | 0.908 | 0.917 | 0.919 |

* **Totals:** the candidate total equals the P0 total for every game. The maximum absolute difference is **0.0**
  (G7); the shift is home +δ/2, away −δ/2.
* **Variance:** σ is identical for P0 and every candidate.

## U. Practical significance

* **Threshold:** pooled ΔMAE ≤ −0.10, pre-registered.
* **Cleared:** P1 (−0.17) and P3 (−0.36).
* **Not cleared:** P2 (−0.003).

## V. Gate scorecard (P0-LIVE control)

| Gate | P1 | P2 | P3 |
|---|---|---|---|
| G1 pooled ΔMAE < 0, CI < 0 | PASS (−0.168) | FAIL | PASS (−0.357) |
| G2 ΔRMSE ≤ +0.02 | PASS | PASS | PASS |
| G3 fold majority and LOSO | PASS (8/8 ≤ +0.02; 7/8 < 0) | FAIL | PASS (8/8 < 0) |
| G4 recent seasons | PASS | FAIL | PASS |
| G5 winner Brier / log loss | PASS | PASS | PASS |
| **G6 tail / site bias ≤ +0.25** | **FAIL** | PASS | **FAIL** |
| G7 totals identical | PASS | PASS | PASS |
| G8 no leakage | PASS | PASS | PASS |
| G9 FCS δ = 0 | PASS | PASS | PASS |
| G10 2026 sanity ≤ +0.50 | PASS (+0.13) | PASS (−0.09) | PASS (+0.03) |
| G11 practical ≤ −0.10 | PASS | FAIL | PASS |
| G12 robust vs P0-INSEASON | PASS (−0.123) | FAIL | PASS (−0.252) |

**G6 detail.** Favourite-oriented bias (actual − projection toward the P0 favourite), by |P0 margin| bin:

| Bin | n | P0 | P1 | P3 |
|---|---|---|---|---|
| 0–7 | 2,261 | −1.21 | −1.42 | −1.58 |
| 7–14 | 1,610 | −2.71 | −3.45 | −3.77 |
| 14–21 | 800 | −3.84 | −5.06 | −5.94 |
| 21+ | 548 | −5.30 | −7.36 | −8.65 |

Neutral-site bias also moved from −0.05 to +0.73 (P1) and +1.09 (P3), but on n 133, below the n ≥ 200 floor.

**POST_HOC (descriptive only; never fed the decision):**
* Calibration slope of actual on projected margin: P0 0.766, P1 0.734, P3 0.731.
* Against the in-season architecture: 0.825 → 0.787.

The rush feature adds discrimination, but P0's projections are already too wide, and an additive rush term widens
them further.

## W. Decision

**REJECT.**
* P1 and P3 fail G6, so they are neither promotable nor shadow-eligible.
* P2 fails G1, G3, G4, G11 and G12.

## AB. Market comparison (descriptive; the close never enters P0 or a candidate)

FBS-vs-FBS 2018–2025 with a CFBD consensus close, n 5,219:

| | Market | P0 | P1 | P3 |
|---|---|---|---|---|
| MAE vs actual | **12.19** | 14.69 | 14.53 | 14.34 |
| Corr with market | — | 0.70 | 0.74 | 0.78 |
| Mean \|model − market\| | — | 8.08 | 7.89 | 7.51 |
| Disagreements ≥ 7 points | — | 2,558 | 2,479 | 2,378 |
| Corrections toward the actual result | — | — | 55 % | 57 % |
| Corrections toward the market | — | — | 58 % | 62 % |
| Cover-probability Brier at the close line | — | 0.286 | 0.283 | 0.280 |

**Reading:**
* The candidate moves toward both the outcome and the market, and creates fewer model–market disagreements.
* The current model remains 2.5 MAE points worse than the close.

## Integrity

* No market input to P0 or any candidate (the features carry no market field; tests).
* Coefficients walk-forward on strictly earlier seasons (asserted, and tested by doctoring the future).
* Old prospective streams untouched (pinned).
* No backfill.
* FCS untouched.
* Totals unchanged.
* No betting rule.
* **Production impact: none.**
