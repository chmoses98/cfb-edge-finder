# CFB projection engine — future hypotheses (UNTESTED, observations only)

Every entry below is an **untested idea** recorded during Wave 2D. None is a rule, a model change or a
recommendation.

**To test one, all of the following are required:**
* its own pre-registered protocol, committed before any result;
* a separate model-repair decision.

**Contamination:** each observation comes from 2014–2025 data that has already been examined (Wave 1, Wave 2C, Wave
2D Track A), or from the examined 2026 replay window.

**Entry format.** Each entry gives:
* **Observation:** what was seen.
* **Mechanism:** a plausible explanation.
* **Proposed future test:** the frozen test it would need.
* **Contamination warning.**
* **Recommended sample.**

---

## C2D-PF1: live ratings never see the current season

**Observation**
* The live path fits ratings from `--history-seasons 2022 2023 2024 2025` only, with the football state fetched
  2026-09-03.
* No 2026 game ever enters the ratings, so a matchup's projection is identical in every 2026 week.
* Historically, the same architecture with strictly-prior in-season rows (P0-INSEASON) has margin MAE 0.27–0.88
  lower than the live configuration (P0-LIVE) in every season 2015–2025 (about 0.4–0.5 since 2022).

**Mechanism**
* The C.2 model was validated as an in-season walk-forward model.
* The live football state never adds current-season `TeamGameLine` rows.

**Proposed future test**
* A model-repair protocol comparing P0-LIVE with P0-INSEASON prospectively on the rest of 2026, or on 2027:
  * paired absolute-error difference;
  * the frozen C.2 and talent components unchanged;
  * the residual pool rebuilt as live.

**Contamination warning**
* The historical gap was measured in this wave.
* The fix touches the official model and must go through its own promotion decision.

**Recommended sample**
* At least 300 FBS-vs-FBS games.

## C2D-PF2: the current projection is over-dispersed

**Observation**
* The calibration slope of actual on the P0-LIVE margin is 0.77, so projected margins are about 30 % too wide.
* The favourite-oriented bias reaches −5.3 points when |projection| ≥ 21.
* P0-INSEASON has slope 0.83.
* This is POST_HOC and descriptive.

**Mechanism**
* The frozen C.2 linear correction (a = 1.34) was fit on in-season walk-forward margins.
* Applied to stale ratings, it over-amplifies.

**Proposed future test**
* A pre-registered refit of the margin correction against the configuration that is actually live (or against
  P0-INSEASON once PF1 is decided).
* Walk-forward by season; metric: MAE plus the calibration slope.

**Contamination warning**
* This was seen while diagnosing the Track A G6 failure.

**Recommended sample**
* Historical walk-forward plus one prospective season.

## C2D-PF3: rush information jointly with recalibration

**Observation**
* The run-defense feature predicts current-model error by +3.3 points per SD.
* Walk-forward, P1 improves MAE by 0.17 and P3 by 0.36.
* Both widen already over-wide projections and fail G6.

**Mechanism**
* The rush term carries real discrimination; P0's dispersion is wrong.
* An additive term cannot fix both.

**Proposed future test**
* Only **after** PF1/PF2 are decided: a single frozen candidate that refits the margin correction and the rush
  coefficient together, walk-forward, with G6 unchanged.
* Prospective evidence from `CFB-MODEL-PROS-001` is the first input.

**Contamination warning**
* Strongly contaminated: the feature, the failure mode and the fix were all seen on the same data.

**Recommended sample**
* `CFB-MODEL-PROS-001` PRIMARY_REVIEW (n 400) first.

## C2D-PF4: uncertainty-scaled rush feature (C2C-F3)

**Observation**
* Extreme early-season z values (≤ 2 prior games) are less reliable (Wave 2C).
* In Track A, P1's gain is smallest in weeks 3–5 (−0.06).

**Proposed future test**
* The `C2C-F3` shadow stream (Track B) measures it.

**Contamination warning**
* Week-dependent behaviour was seen in this wave.

**Recommended sample**
* Weeks 1–5 of two prospective seasons.

## C2D-PF5: FCS-specific rushing information (C2C-F4)

**Observation**
* FCS-involved games carry extreme feature values.
* Track A excluded them by design (δ = 0).

**Proposed future test**
* A separate FCS-involved residual study of the current FCS model.

**Contamination warning**
* Small n; no FCS result was examined in this wave.

**Recommended sample**
* Two seasons of FCS-involved games.

## C2D-PF6: forced-pass / turnover-risk component

**Observation**
* Wave 2C: run defense works through opponent pass volume and interceptions.
* Track A did not use any turnover term.

**Proposed future test**
* `CFB-MECH-PROS-001` (Track B) gathers the mediators.
* Any turnover-risk projection component needs its own protocol.

**Contamination warning**
* The mechanism was inferred from post-game mediators.

**Recommended sample**
* `CFB-MECH-PROS-001` primary review.

## C2D-PF7: 2026 transfer of the run-defense relationship

**Observation**
* In the 2026 retrospective window (n 226), the slope of live P0 error on `x_rd` is +0.93 per SD, against about +3.6
  historically.
* P1 was worse by +0.13 MAE there.

**Mechanism**
Three possibilities:
* early-season noise;
* the ESPN-derived (vs CFBD) feature source;
* stale 2026 ratings changing what the error contains.

**Proposed future test**
* `CFB-MODEL-PROS-001` measures it prospectively.

**Contamination warning**
* 2026 has already been examined.

**Recommended sample**
* n 400.

## Carried forward, not operationalised (Wave 2C list)

* Opener-specific market work (C2C-F6).
* Elo disagreement within STRONG CONTROL (C2C-F5).
* Record over-reliance (C2C-F7).
* Scoring-environment totals over-adjustment (C2C-F8).

---

# Wave 2E additions (observations only; untested)

Source: `CFB_BASE_MODEL_REPAIR_RESULTS.md`. All are contaminated by the 2014–2025 walk-forward already examined. Each
needs its own protocol.

## C2E-PF1: in-season ingestion with C.2 retired

**Observation**
* B1 fixes ingestion (MAE −0.43 in all 8 folds) but stays over-extended (slope 0.84).
* The post-hoc layer "B1 raw + talent" (C.2 removed, no new fit) had slope 0.93 and MAE 14.11. It was the best
  uncalibrated stack, but it was not in the registry.

**Proposed test**
* Freeze B1 without the C.2 artifact as one candidate.
* Gates as Wave 2E.

**Sample**
* Prospective season plus walk-forward.

## C2E-PF2: talent decay by week

**Observation**
* Talent is applied at full weight all season.
* The over-extension lives in weeks 4+ (slope ≈ 0.72–0.79); weeks 1–3 have slope ≈ 1.0.

**Proposed test**
* Pre-register a single weight schedule, w(gp) = k / (k + gp), tied to the existing carryover form.
* No tuning on outcomes.

## C2E-PF3: calibration drift / recency window

**Observation**
* B2's 4-season window over-shrinks recent seasons: 2023–2025 slope 1.15 (the G9 failure).
* The C.2 refit a fell from 1.30 (2018) to 0.98 (2025).

**Proposed test**
* A shorter frozen window (e.g., 2 seasons), declared before results.

## C2E-PF4: operational freshness

**Observation**
* The engine has been fail-closed since about 2026-09-17: the football state went hard-stale and the CFBD quota
  (1,000 / month) ran out.

**Proposed test**
* A design decision, not a statistical test: refresh budget, cadence and current-season source (the ESPN bridge passed
  the source-contract check).

## C2E-PF5: joint base + rush recalibration

**Observation**
* `x_rd` still predicts error on a calibrated base (+3.54 points per SD).
* Added additively, it over-extends favourites (G6).

**Proposed test**
* Only after PF1–PF3: one frozen joint fit of the margin scale and the rush coefficient.

## Carried forward from the Wave 2E brief

* Early-season total repair.
* Uncertainty-scaled rush.
* FCS-specific rush.
* A turnover-risk structural component.
* Elo disagreement.
* Opener-specific signal.
* Record over-reliance.
* Scoring-environment totals adjustment.
