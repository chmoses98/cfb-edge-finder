# CFB base-projection repair + run-defense reintegration — Wave 2E protocol (PRE-REGISTERED)

**Label:** `RETROSPECTIVE_MODEL_REPAIR`.

This file is committed **alone**, before any Wave-2E candidate result. Nothing below changes after results are seen;
any deviation is reported as a deviation.

| Item | Value |
|---|---|
| Starting main | `c45f52c575987c41610dcd3aafbe39c4506dc996`. The 3 commits since the Wave-2D merge `cb8b61c7` are data-only bot commits. |
| Written | 2026-10-10T06:41Z |
| Order | (A) current-season ingestion → (B) margin calibration of the complete stack → (C) run-defense retest on the frozen repaired base. Never tuned together. |

## 0. Known before this protocol (contamination disclosure)

**From Wave 2D (`integration_report.json`, SHA-256 `0effb387…2fb`), already seen:**
* P0-INSEASON pooled MAE is 0.27–0.88 better than P0-LIVE every season.
* POST_HOC calibration slopes: P0-LIVE 0.766, P0-INSEASON 0.825.
* `x_rd` slope on P0-LIVE error: +3.30 per SD.

**Not yet seen:**
* the per-layer calibration;
* B2 and B3;
* the source-contract check;
* the 2026 B-candidates;
* the rush increment on a repaired base.

**The talent β** (`TALENT_BETA` 0.018993) was fit on 2021–2023, so it is in-sample for those seasons.

## 1. Live-pipeline facts established before this protocol (trace, not results)

**The model is retired from the live betting path.**
* `docs/MODEL_RETIREMENT_2026.md`: RUN CFB consumes only the Kalshi market catalog (`docs/RUN_CFB_CONTRACT.md`: "The
  repository NEVER provides the football projection").
* `research-capture.yml`'s schedule is commented out (hibernated).
* The engine's remaining consumers:
  * `research_scan_and_capture.py` (manual dispatch and probes);
  * the Wave-2D Track-B `run_defense` P0 (research).
* The Wave-2D statement that RUN CFB prices through this engine is **corrected here**.

**Even when dispatched, the engine has been FAIL-CLOSED since about 2026-09-17.**
* The last research-data heartbeat (2026-09-25) records:
  * `FOOTBALL_STATE_STALE_HARD`;
  * CFBD quota exhausted (1,000 / month).
* History was last fetched 2026-09-03, and `HISTORY_HARD_MAX_HOURS = 336` (14 days).

**Current-season games never enter the ratings.**
* `research_scan_and_capture --history-seasons` defaults to 2022–2025.
* `football_state.build_football_state` fetches CFBD games and advanced stats **only for the history seasons**. The
  schedule season contributes schedule rows only, with no advanced stats.
* `FootballState.to_scan_inputs().lines_loader` builds `TeamGameLine`s only for the history seasons.
* So no current-season `TeamGameLine` ever exists, and `GameProjectionCache`'s strictly-before filter has nothing
  from 2026 to admit.
* There is no as-of bug: the filter `ln.as_of.is_strictly_before(as_of)` is correct.

## 2. Models

All components use production code: `fit_fbs_efficiency_ratings` (λ 10, FCS λ 4 pooled, matchup pace),
`project_game` deterministic expected points, `fit_linear_margin`, `talent_margin_delta`.

The historical inputs are the committed Wave-2D study input `p0_games_2014_2025.jsonl.gz` (SHA-256 `c0c40840…28da`).

| Id | Definition |
|---|---|
| **P0** (P0-LIVE-OLD) | Ratings from the 4 prior seasons only, **no season-S rows** (the live behaviour). C.2 linear correction fit by `fit_linear_margin` on the B1 **raw** FBS-vs-FBS margins of seasons max(2015, S−4)…S−1, which is how the live artifact was produced. Then the talent delta (2019, 2021–2025). |
| **B1** (B1-INSEASON) | Identical to P0, except that ratings for a game in (S, w) also use season-S rows strictly before week w (`AsOf` contract). Same C.2 correction, same talent, same variance. |
| **B2** | B1, then a new **complete-stack** linear calibration: actual ≈ a·m_B1 + b, where m_B1 = raw + C.2 delta + talent. Fit by `fit_linear_margin` on B1 final FBS-vs-FBS margins (regular season and postseason) of seasons max(2015, S−4)…S−1. |
| **B3** | B1 with the C.2 correction **removed**, then one linear calibration: actual ≈ a·(raw + talent) + b. Same fit window and fit function. |

**Applying the calibrations:**
* FBS-vs-FBS only. FCS games keep B1 exactly.
* Applied as a zero-sum score shift (home +δ/2, away −δ/2), so the B1 total is unchanged exactly.

**The total in B1:**
* B1's expected total changes relative to P0, because the ratings and pace see more data.
* The **total model is unchanged**; the change is a data effect.
* Reported, and gated only for non-degradation (G10b).

**Variance:**
* σ is unchanged in every candidate: production scales, residual covariance from B1 raw residuals of the prior 4
  seasons.
* Realised residual SD and 50/80/90 % coverage are reported.
* No variance change in this wave. Coverage outside [0.85, 0.95] at 90 % becomes a future-hypothesis flag only.

**No candidate reads a spread, opener, close, Kalshi price or any market field.**

## 3. Population and folds

* **Base population:** FBS-vs-FBS **regular-season** games with final scores, where P0 and B1 are computable.
* **Primary folds:** 2018–2025 (8 seasons; each has a full 4-season prior window and ≥ 3 seasons of calibration
  training).
* **Also reported:** 2016–2017.
* **2026:** retrospective sanity only (G14). Nothing is refit on it.
* **Segments:**
  * week 1, weeks 2–3, weeks 4–5, weeks 6+;
  * eras 2014–2019, 2020–2022, 2023, 2024, 2025;
  * talent-active vs inactive seasons;
  * home / away / neutral.
* **FBS-vs-FCS** games are reported separately and never pooled.
* **Expectation check:** week-1 B1 equals P0 except where week-0 games exist. Any other week-1 difference is
  investigated.

## 4. Metrics (definitions frozen)

* **Margin accuracy:** MAE, RMSE, bias (mean actual − projection).
* **Winner:** Brier and log loss for P(home margin > 0) = 1 − Φ((0.5 − μ) / σ).
* **Coverage:** 50 / 80 / 90 % Normal intervals.
* **Calibration slope and intercept:** pooled OLS of actual on the model's own projected margin.
* **Favourite-direction error** (repository convention, `modeling/diagnostics.py`): (actual − proj) × sign(proj).
  Positive means the favourite won by more than projected.
* **FAV_TAIL:** mean favourite-direction error where the model's **own** |proj| ≥ 14.
* **DOG_TAIL:** P(favourite-direction error ≤ −14) − P(favourite-direction error ≥ +14), over all games. Positive
  means the projected underdog beats the projection by 14+ more often than the favourite exceeds it; 0 when the
  centre is calibrated.
* **Bin table:** favourite-direction bias by own |proj| bins [0,3) [3,7) [7,14) [14,21) [21,28) [28,∞).
* **Inference:** paired game-level differences, bootstrap 95 % CI clustered by season-week (seed 20261012, 2,000
  draws).

## 5. Layer diagnosis (§13–15; descriptive, out of sample)

For both P0 and B1, at these stages:

| Stage | Definition |
|---|---|
| raw | raw ratings margin |
| raw + talent | raw + talent delta |
| C.2 | C.2-corrected margin |
| C.2 + talent | the current final projection |
| selected base | the repaired base, once selected |

**For each stage:** slope, intercept, MAE, RMSE, FAV_TAIL and DOG_TAIL, on the primary folds.

**C.2 audit:**
* the per-fold refit (a, b);
* the frozen artifact (a 1.3413, b 0.8117) applied to the 2022–2025 B1 and P0 raw margins;
* its out-of-sample slope.

## 6. Source-contract check (gates the live current-season source)

**Why a second source is needed.**
* Historical ratings use the CFBD advanced `offense.plays` field.
* Live production cannot refresh CFBD for the current season: the quota is exhausted and the path is hibernated.
* The repository's continuously refreshed football source is the production ESPN game log, where box `plays` =
  rush_att + pass_att.

**Pre-registered check:**
* **Bridge (measured, not assumed):** CFBD advanced plays − CFBD box (rush_att + pass_att) = +0.74 mean (SD 4.1)
  per team-game, 2025, n 1,776.
* **Source-contract replay (SC):** recompute B1 historically with the **current-season** rows' plays replaced by
  CFBD box rush_att + pass_att (the ESPN definition). Prior seasons stay advanced.
* **Accept the ESPN-defined current-season source if** the pooled |ΔMAE(SC − B1)| ≤ 0.02 **and**
  |Δslope| ≤ 0.01 (primary folds).
* **Identity and orientation:** ESPN game id = CFBD id (Wave 2C: 522/522 orientation, 8/8 scores). Team slugs come
  from the football state's CFBD schedule row for the same id. Final points come from the production log.

**If SC fails,** the live current-season source must be CFBD (exact parity), and live ingestion is blocked until CFBD
refreshes.

## 7. Base gates (vs P0, pooled primary folds unless stated)

| Gate | Rule |
|---|---|
| G1 | Current-season ingestion leakage-safe and functional. Tests prove: rows strictly before the as-of; the target and later weeks excluded; a later week sees an earlier completed game; the training count rises; ratings change. |
| G2 | ΔMAE ≤ +0.02 |
| G3 | ΔRMSE ≤ +0.02 |
| G4 | ΔBrier ≤ +0.0005 and Δlog loss ≤ +0.0015 |
| G5 | Calibration slope in **[0.90, 1.10]** **and** \|1 − slope\| ≤ \|1 − slope_P0\| − 0.05 |
| G6 | \|FAV_TAIL\| ≤ \|FAV_TAIL_P0\| (non-worsening; "material improvement" = a reduction ≥ 0.5, reported) |
| G7 | \|DOG_TAIL\| ≤ \|DOG_TAIL_P0\| + 0.01 |
| G8 | Fold stability: ΔMAE ≤ +0.05 in ≥ 6 of 8 folds **and** fold slope in [0.85, 1.15] in ≥ 6 of 8 folds |
| G9 | Recent seasons 2023–2025 pooled: ΔMAE ≤ +0.02 and slope in [0.90, 1.10] |
| G10 | (a) the calibration layer preserves the B1 total exactly (max \|Δ\| ≤ 1e-9); (b) B1 total MAE − P0 total MAE ≤ +0.10 |
| G11 | FBS-vs-FCS: B-candidate ΔMAE vs P0 ≤ +0.25; FCS calibration δ = 0 |
| G12 | No market field in any model input (tests) |
| G13 | No leakage: every fold coefficient is from strictly earlier seasons (asserted and tested) |
| G14 | 2026 sanity: candidate MAE − P0 MAE ≤ +0.50 on 2026 FBS-vs-FBS finals. Current-season rows from the ESPN log via §6, the frozen C.2 artifact, and the production calibration artifact (fit on 2022–2025). |

**Slope tolerance justification.** The season-level slope SE is about 0.045 (n ≈ 700, residual SD ≈ 18, projection SD
≈ 15). ±0.10 is about 2.2 season SEs, and very wide against the pooled SE (≈ 0.016). A slope inside it is
indistinguishable from 1 at season scale. The required improvement of ≥ 0.05 is about one season SE.

**Base selection.**
* Among B1, B2 and B3 passing **all** of G1–G14, pick the lowest pooled MAE.
* Within 0.02, prefer the simpler stack: B3 (one calibration), then B1 (no new fit), then B2.
* **PROMOTE_BASE** if one exists, otherwise **KEEP_CURRENT_BASE**.
* B1 is always reported as the isolated ingestion effect.

## 8. Run-defense retest (only after the base is selected and frozen)

**BASE_REPAIRED:**
* the selected base;
* if none passes, the best-MAE candidate whose slope is closest to 1 is used **as a research diagnostic only**, and
  rush promotion is then impossible.

**Candidates (exact Wave-2D definitions):** `x_rd`, `x_ro`, `x_rr` via `signal_discovery.rush_projection`.

| Id | Increment |
|---|---|
| R1 | BASE + β·`x_rd` |
| R2 | BASE + β_rd·`x_rd` + β_ro·`x_ro` |
| R3 | BASE + β·`x_rr` |

**Coefficients:**
* No intercept.
* Walk-forward: fit on seasons 2015…S−1, target actual − BASE margin.
* FBS-vs-FBS only. A missing feature means δ = 0.
* Zero-sum shift.
* The old β (3.5923) is **not** reused.

**Reports:**
* the `x_rd` residual slope on BASE error (points per SD, season-cluster CI, season signs);
* the incremental R² over the BASE margin;
* the partial correlation;
* all compared with Wave 2D (+3.30).

**Rush gates:** the **Wave-2D G1–G12, unchanged**, with BASE_REPAIRED in place of P0-LIVE.
* **G6 is identical:** in no |BASE margin| bin [0,7) [7,14) [14,21) 21+ with n ≥ 200 may the absolute
  favourite-oriented bias increase by more than 0.25; home and neutral site bias likewise.
* G11 practical: ΔMAE ≤ −0.10.
* G12 robustness: also vs B1 when BASE ≠ B1.

**Rush decisions:**
* **PROMOTE_RUSH:** all pass, and the base was promoted.
* **SHADOW_RUSH:** G1, G2, G5, G6, G7, G8, G9 and G10 pass, but not everything (or the base was not promoted).
* **REJECT_RUSH:** otherwise.

**G6 is not lowered.**

## 9. Promotion mechanics

### If PROMOTE_BASE

**Version:**
* A new model version, `0.6.0-in-season-ratings` (B1), or `0.6.0-in-season-recalibrated` (B2 / B3).
* `0.5.0-early-season-talent-prior` and 0.4.0 are never reused for changed arithmetic.

**Current-season ingestion (production):**
* Prior seasons come from the CFBD football state, unchanged.
* Current-season rows come from the production ESPN game log via §6, if SC passed.
* The as-of contract is unchanged.

**Telemetry:**
* rating as-of;
* current-season training rows;
* last included kickoff and week;
* teams with current-season evidence;
* model version.

**Health guard:**
* If week > 1, completed prior-week FBS games exist, and current-season rows = 0 → `CURRENT_SEASON_DATA_MISSING`.
  The new version fails closed: no projection under it.
* If the log is older than 48 hours while completed games exist after its last observation →
  `CURRENT_SEASON_DATA_STALE`, also fail closed.

**Cache:** the ratings cache key becomes (as-of, digest of the permitted history rows), so changed history always
rebuilds.

**Calibration artifact (B2 / B3):** `cfb_base_margin_calibration/1.0.0`, frozen (a, b) fit on 2022–2025, the
production analogue of the S = 2026 fold.

**Kept as-is:**
* The Track-B `run_defense` P0 stays the frozen 0.5.0 object (prior seasons only), pinned by a test.
* `CFB-MODEL-PROS-001`, `CFB-PROS-003` and `CFB-MECH-PROS-001` are not altered.

**New stream `CFB-MODEL-PROS-002` (BASE_REPAIR_FORWARD_CONFIRMATION):**
* Compares OLD_BASE 0.5.0 with NEW_BASE.
* d = |old − actual| − |new − actual|.
* Reviews at n = 50 / 100 / 200 / 400.
* Also tracks RMSE, winner Brier, calibration slope and FAV_TAIL.
* Frozen pregame, inside the research conductor, append-only.
* Activation 2026-10-13T12:00Z, no backfill.

**Inspection:** a current-slate OLD vs NEW margin table, listing every |Δ| ≥ 0.5 with its source (in-season ratings /
recalibration / both). Inspection only.

### If rush is promoted or shadowed

**New stream `CFB-MODEL-PROS-003` (REPAIRED_BASE_PLUS_RUSH):** BASE_REPAIRED vs BASE_REPAIRED + rush, paired MAE,
same review sizes.

### Unchanged in every case

Totals model, FCS model and tiers, variance, CONTROL, Script Engine, V2, Value Watch, SIFT, staking, router, the
RUN CFB contract, and every Wave-2 rule and ledger.

## 10. Market (descriptive only, after freezing)

* Close-line MAE and correlation.
* Whether the repaired model moves toward the market.
* Large-disagreement counts.
* The market is never used to fit or select.
