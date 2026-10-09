# CFB mechanism study (Wave 2C): future hypotheses (UNTESTED)

* **Status:** every entry below is an **untested idea**. None is a rule, filter, signal or recommendation.
* **Origin:** each was suggested by the retrospective, discovery-contaminated mechanism study
  (`CFB_SIGNAL_MECHANISM_RESULTS.md`).
* **What it would take:**
  * a newly committed protocol, frozen *before* any evaluation data exists;
  * a prospective window that has not been looked at.
* **Must not be:**
  * evaluated on 2014–2025 or on the Wave-2A 2026 replay rows;
  * pooled with them.
* **Wave 2 is unaffected:** `CFB-PROS-001` and `CFB-PROS-002` are not modified by anything here.

## Entry format

Each entry gives:

* **Observation:** what the study saw.
* **Mechanism:** the plausible football mechanism.
* **Proposed frozen test:** the exact future test.
* **Contamination warning.**
* **Recommended sample.**

---

## C2C-F1: rush defense as the frozen feature, residualised on efficiency

**Observation**
* With efficiency held fixed:
  * the rush-defense difference has an ATS residual weight of +0.69 per SD [+0.38, +1.03], 10/12 seasons;
  * the market moves the line the wrong way (−0.91 per SD).
* Rush offense contributes about half as much (T3 +0.61, q 0.004).

**Mechanism**
* Stopping the run forces the opponent into obvious passing, which leads to more interceptions and less possession.
* These effects are poorly reflected in points-based ratings.

**Proposed frozen test**
* **Feature:** `def_diff_rush_res` = `defdiff.rush_success_rate` residualised on `net.sustained_efficiency`.
* **Coefficients and SD:** fitted once on 2014–2025 and frozen in the protocol.
* **Side:** the better-rush-defense side when |z| ≥ 1.
* **Close:** PRIMARY_60_180.
* **Metric:** one-sided mean ATS residual > 0, compared head-to-head against PROS-001 on the same games.
* **Reporting:** no stake.

**Contamination warning**
* The construct was chosen *because* it explained the PROS-001 residual on the same data.
* Its historical estimate is optimistically biased.

**Recommended sample**
* ≥ 300 qualifying prospective games (≈ 2 seasons) for ±1.8 points of SE.

## C2C-F2: interception channel / opponent forced-pass forecast

**Observation**
* About 28 % of the rush-defense ATS weight runs through the turnover differential (1.18 → 0.85).
* This is mostly interceptions: −0.13 per SD of `rush_res`, while fumbles are +0.01.
* CONTROL losses are driven by turnovers (−1.23).

**Mechanism**
* Forced passing volume raises interception exposure.
* Whether the market prices expected opponent pass volume is unknown.

**Proposed frozen test**
* **Forecast:** pre-register a pregame forecast of opponent pass attempts from rush-defense quality.
* **Check:** whether the residual ATS outcome is monotone in the forecast, using quintiles frozen in the protocol.
* **Validation:** compare actual interceptions against the forecast.

**Contamination warning**
* The mediator analysis used post-game turnovers and is descriptive only.
* Turnovers are noisy and partly luck.

**Recommended sample**
* One full prospective season of all FBS games (≈ 800).

## C2C-F3: standard-error-scaled early-season z

**Observation**
* Mean |z| is 1.15 in 2026 when either team has ≤ 2 prior games, against 0.69 with ≥ 7 historically.
* Week-2 values correlate only about 0.46 with end-of-season values.

**Mechanism**
* Small-sample team metrics are noisy, and standardising by the full-season SD treats noise as signal.

**Proposed frozen test**
* **Shrink:** shrink each team-metric input toward its prior-season value, with a weight n / (n + k).
* **Weight constant:** k is fixed in the protocol from historical within-season reliability.
* **Comparison:** whether shrunk-feature qualifiers have a better prospective ATS residual than unshrunk ones in
  weeks 1–5.

**Contamination warning**
* The reliability curves were measured on the discovery seasons.
* k must not be tuned on outcomes.

**Recommended sample**
* Weeks 1–5 of two prospective seasons.

## C2C-F4: FCS-involved games analysed separately

**Observation**
* FCS-involved games carry rush-defense differences of +1.8 to +5.1 and always qualify.
* They explain the 2026 distribution shift.

**Mechanism**
* Cross-division opponent adjustment is thin: it rests on few common opponents.
* Extreme values reflect the competitive gap, which the market prices directly.

**Proposed frozen test**
* **Split:** pre-register the prospective PROS-001-equivalent residual split into FBS-vs-FBS and FCS-involved.
* **Exclusion:** no exclusion applied to the live rule.
* **Report:** both splits only.

**Contamination warning**
* The study did not inspect FCS residuals for selection, but any exclusion chosen now would be post hoc.

**Recommended sample**
* All prospective qualifiers over two seasons; there are only about 20–40 FCS rows per season.

## C2C-F5: STRONG CONTROL where Elo disagrees

**Observation**
* 70 historical STRONG games were market-skeptical (the CONTROL side not favoured by 3 or more).
* They went 42–27–1, +5.25.
* The 34 of them where Elo disagreed with CONTROL averaged +7.2 (POST_HOC).
* They concentrate in weeks 1–5.

**Mechanism**
* Preseason priors (Elo and the market) lag in-season efficiency evidence in the early weeks.

**Proposed frozen test**
* Count STRONG CONTROL with a pregame Elo difference against the side and the market not favouring it by ≥ 3.
* Track the prospective mean ATS residual.
* Descriptive only until n ≥ 60.

**Contamination warning**
* This is a tiny, post hoc subgroup chosen after seeing its outcome.
* It is the classic look-best subset: **do not act on it.**

**Recommended sample**
* At least 3 prospective seasons; the rate is about 6 per season.

## C2C-F6: opener-relative evaluation of the rush and CONTROL signals

**Observation**
* The close absorbs about a fifth of the frozen rush residual (T11 +0.33, 5/5 seasons) and half of CONTROL's
  (+0.96 → +0.48).

**Mechanism**
* Sharper money moves the line toward efficiency and rushing information during the week.

**Proposed frozen test**
* For prospective PROS-001 and CONTROL rows, also record the earliest available quote.
* Report the residual against both the first and the PRIMARY_60_180 quote.
* Purely as a capture-time diagnostic.

**Contamination warning**
* Kalshi liquidity at the open may differ from sportsbook openers.
* The historical openers are consensus figures for 2021+ only.

**Recommended sample**
* All prospective qualifiers with an early quote.

## C2C-F7: market over-reliance on win–loss record

**Observation**
* Holding efficiency and Elo fixed, the win-percentage difference to date has a residual weight of −0.46 (POST_HOC).

**Mechanism**
* Record is salient; close wins and losses inflate or deflate perceived strength.

**Proposed frozen test**
* **Construct:** the record residual given efficiency, fixed in the protocol.
* **Measure:** the prospective ATS residual slope.
* **Test:** one-sided < 0.

**Contamination warning**
* A post hoc finding, and the variable overlaps with the existing closeness and V2 relationships.

**Recommended sample**
* One prospective season of all FBS games.

## C2C-F8: over-adjustment to scoring environment in totals

**Observation**
* The market total moves +5.90 per SD of offense quality against an actual +5.33.
* SUPPRESSED claims finish +0.65 above the market total; ELEVATED claims finish −0.31 below it.
* T10 p 0.08; the CI includes 0.

**Mechanism**
* Salient offense ratings may lead totals to over-extrapolate.

**Proposed frozen test**
* Measure the prospective totals residual (actual − close) by the production `scoring_env_claim`.
* Test the ELEVATED − SUPPRESSED difference, one-sided < 0.

**Contamination warning**
* Not significant after FDR; recorded only as a direction to watch.

**Recommended sample**
* Two prospective seasons.
