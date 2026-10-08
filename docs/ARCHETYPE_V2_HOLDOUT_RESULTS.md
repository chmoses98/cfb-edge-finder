# Archetype V2 — Sealed-Holdout Results (Wave 2)

**Verdict: V2 GENERALIZES STRONGLY ON SEALED HOLDOUT — PRODUCTION UNCHANGED.**

All ten pre-registered families passed on the sealed 2014-2020 holdout. Three qualifications matter, and none of them
changes the pre-registered verdict:

* **CLOSENESS (the union) passes, but adds nothing over simply having no efficiency edge.** EVEN_MATCHUP carries the
  signal.
* **SUPPRESSED_SCORING_ENVIRONMENT describes low-scoring games, but is not incremental beyond the descriptive
  baseline** on the holdout (E4 failed). ELEVATED, PACE and DEFENSIVE_SUPPRESSION are incremental.
* **DISRUPTION was testable in only 5 of 7 holdout seasons.** CFBD 2014-2015 have no sacks/TFL counts.

Pre-registered recommendation: **V2 DESERVES A FORMAL PRODUCTION-MIGRATION REVIEW** (Wave 3). Nothing was promoted
in this wave.

| Item | Value |
|---|---|
| Base main | `f574e0db3be383697f1f0cd128e2aaffd5e85aba` |
| Implementation commit | `a68da9c2af13ec1bbbe5c0c51d97e1b5a9aacc0f` |
| Protocol + frozen parameters commit | `09366f012b619ef1224a24ceba4f679977439957` |
| Frozen parameters SHA-256 | `683d075d99efdfed16f7fb35a9fd58234808d1a354f8fb8e3352c64e972bf7aa` |
| Holdout prediction manifest commit (pre-reveal) | `037499cb77b51568f0616047402ceead60e660c5` |
| Scoring run | code `037499cb…`, generated 2026-10-08T00:00:31Z |
| V2 version | `cfb-archetype-v2-candidate/1.0.0` |
| Production methodology | `cfb-script-engine/1.3.0` (unchanged) |
| Machine-readable | `data/scripting/validation/archetype_v2_holdout_report.json` |
| Row-level | `data/scripting/validation/archetype_v2_holdout_rows/rows_<season>.jsonl.gz` |
| All tables | `docs/ARCHETYPE_V2_HOLDOUT_TABLES.md` (generated) |
| Comparison | `docs/ARCHETYPE_V1_V2_COMPARISON.md` |

## 1. Integrity chain

1. **Wave 1** was merged first: PR #98 was squash-merged as `f574e0db3`.
2. **Design.** V2 was designed on 2021-2025 Wave 1 rows only, and implemented and tested on synthetic seasons
   (`a68da9c2a`).
3. **Pre-registration.** `fit-dev` froze the development parameters (CONTROL distributions and intervals). They were
   committed with the protocol, which names their hash (`09366f012`), and pushed before any 2014-2020 file was
   extracted.
4. **Stage 1 (pregame only).** The 2014-2020 caches were extracted. Every target was replayed from strictly earlier
   games and frozen, V2 claims were attached, and the per-game (pregame_hash, v2_hash) manifest was committed and
   pushed (`037499cb7`). No target result was read.
5. **Stage 2 (once).** The gate checked that the protocol is marked PRE-REGISTERED, that it names the frozen hash,
   that the frozen file matches it, and that every prediction matches the manifest. Each reveal re-checks the pregame
   hash. Scoring read the intervals from the frozen file; the evaluation module has no fitting path. The run was
   executed once.
6. **Leakage check.** 5,823 / 5,823 holdout targets passed (latest history kickoff + 4h30 < cutoff).

## 2. Sealed holdout coverage

| Season | Scheduled | Completed | Box both | Success-rate data | Drives | Explosive counts | Sacks/TFL counts | Eligible | Excluded | Reasons |
|---|---|---|---|---|---|---|---|---|---|---|
| 2014 | 868 | 868 | 867 | 848 | 851 | 0 | **0** | 744 | 124 | no prior history 123 (both 85, away 35, home 3), no box rows 1 |
| 2015 | 870 | 870 | 870 | 863 | 863 | 0 | **0** | 747 | 123 | no prior history 123 (both 85, away 38) |
| 2016 | 873 | 873 | 872 | 857 | 857 | 0 | yes | 743 | 130 | no prior history 129, no box rows 1 |
| 2017 | 874 | 874 | 874 | 869 | 869 | 0 | yes | 746 | 128 | no prior history 128 |
| 2018 | 884 | 884 | 884 | 881 | 881 | 0 | yes | 748 | 136 | no prior history 136 |
| 2019 | 888 | 888 | 888 | 887 | 887 | 0 | yes | 756 | 132 | no prior history 132 |
| 2020 | 570 | 570 | 568 | 568 | 568 | 0 | yes | 477 | 93 | no prior history 91 (home 22 — the COVID schedule), no box rows 2 |
| **Total** | 5,827 | 5,827 | | | | 0 | | **4,961** | 866 | |

* **Source and fingerprints.** CFBD cache, `research-data` branch. Per-file SHA-256 fingerprints and row
  fingerprints are in the report and the tables doc; for example, the 2014 `games.json.gz` hash begins
  `f93eed152cde1d63`.
* **Confidence mix (data quality, not outcome):**
  * 2014-2015 have **no HIGH tier**. Without sacks/TFL/passes-defended, core coverage falls below 90%.
  * 2020 is 465 LOW / 12 HIGH, because the schedule graph is disconnected.
  * V2 claims do not depend on the tier.

## 3. CONTROL — direction × strength

Supported-side margin. Base home win rate: development 58.6%, holdout 58.4%. Base away win rate: development 41.4%,
holdout 41.6%.

| Tier | Block | n | Win (95% CI) | Lift | Mean | p10 | p25 | p50 | p75 | p90 | ≥1 | ≥7 | ≥14 | ≥21 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HOME MODERATE | dev | 404 | 80.0% (76–84) | 1.36 | 12.9 | −7 | 3 | 12 | 24 | 34 | 80% | 66% | 49% | 34% |
| | **holdout** | 512 | **79.5% (76–83)** | 1.36 | 12.8 | −7.9 | 3 | 13 | 24 | 35 | 79% | 66% | 49% | 34% |
| HOME STRONG | dev | 628 | 90.1% (88–92) | 1.54 | 22.8 | 1 | 8 | 23 | 35 | 48 | 90% | 78% | 67% | 55% |
| | **holdout** | 824 | **91.0% (89–93)** | 1.56 | 23.9 | 2 | 11 | 24 | 38 | 46.7 | 91% | 83% | 71% | 58% |
| AWAY MODERATE | dev | 404 | 66.6% (62–71) | 1.61 | 7.0 | −13.7 | −4 | 7 | 18 | 30 | 67% | 53% | 33% | 22% |
| | **holdout** | 514 | **67.9% (64–72)** | 1.63 | 7.5 | −14 | −3 | 7 | 18.75 | 30 | 68% | 51% | 35% | 23% |
| AWAY STRONG | dev | 378 | 81.0% (77–85) | 1.96 | 15.1 | −5.3 | 3 | 14.5 | 27 | 37 | 81% | 68% | 52% | 38% |
| | **holdout** | 552 | **84.6% (81–87)** | 2.03 | 17.2 | −4.9 | 5 | 17 | 30 | 41 | 85% | 74% | 57% | 43% |

**Holdout win rate by season:**

| Tier | 2014 | 2015 | 2016 | 2017 | 2018 | 2019 | 2020 |
|---|---|---|---|---|---|---|---|
| HOME MODERATE | 78% | 77% | 78% | 83% | 86% | 80% | 71% |
| HOME STRONG | 87% | 88% | 90% | 94% | 93% | 95% | 89% |
| AWAY MODERATE | 67% | 77% | 67% | 66% | 69% | 67% | 53% (n 34) |
| AWAY STRONG | 91% | 86% | 78% | 88% | 80% | 82% | 89% |

* Every tier beats its season's home (or away) base in **7 of 7** seasons.
* Win-rate homogeneity across seasons gives p = 0.61, 0.24, 0.31 and 0.18.

**MODERATE → STRONG ordering generalized**, in both directions and in every season:

| Direction | Win rate, STRONG − MODERATE (95% CI) | Median, STRONG − MODERATE | Seasons with STRONG higher |
|---|---|---|---|
| HOME | +11.5 pts (7.6–15.6) | +11 | 7 of 7 |
| AWAY | +16.7 pts (11.6–21.7) | +10 | 7 of 7 |

**Criteria:**

| Criterion | Result |
|---|---|
| C1, every tier | pass |
| C2, every tier | 7/7 seasons |
| C3, HOME and AWAY | pass |
| **CONTROL** | **PASS** |

## 4. Pre-registered empirical intervals (frozen from 2021-2025)

| Tier | Frozen 50% [p25, p75] | Frozen 80% [p10, p90] | Dev in-sample 50 / 80 | **Holdout 50% coverage** | **Holdout 80% coverage** | Calibrated |
|---|---|---|---|---|---|---|
| HOME MODERATE | [3, 24] | [−7, 34] | 52.5 / 81.7 | 52.5% (48–57) | 78.5% (75–82) | yes |
| HOME STRONG | [8, 35] | [1, 48] | 50.8 / 80.6 | 52.9% (50–56) | 82.2% (79–85) | yes |
| AWAY MODERATE | [−4, 18] | [−13.7, 30] | 52.0 / 80.0 | 53.7% (49–58) | 78.4% (75–82) | yes |
| AWAY STRONG | [3, 27] | [−5.3, 37] | 50.8 / 80.2 | 52.4% (48–56) | 76.4% (73–80) | yes |

* Tolerances were 42-58% for the 50% interval and 72-88% for the 80% interval. All 4 tiers are calibrated, so
  **CONTROL_INTERVALS PASSES**.
* Mean absolute coverage error from nominal is 2.5 points on the holdout (1.1 in-sample).
* Per-season coverage ranges from 42% to 71% (central-50) and 68% to 88% (central-80). The extremes come from small
  seasons, for example 2020 AWAY MODERATE with n 34.
* The one visible drift is AWAY STRONG: the holdout margins ran slightly larger (median 17 vs 14.5), and central-80
  coverage was 76.4%.
* For comparison, the V1 hand-set bands contain the same holdout outcomes much less often:

  | V1 band | Holdout coverage |
  |---|---|
  | 7-24 (CONTROL) | 34.8-42.2% by tier |
  | 17-45 (PULLS_AWAY) | 29.0-53.9% by tier |

## 5. CLOSENESS

Holdout unconditional rates:

| Measure | Value |
|---|---|
| \|m\| ≤ 3 | 15.4% |
| \|m\| ≤ 7 | 32.9% |
| \|m\| ≤ 8 | 35.5% |
| Median \|m\| | 14 |

| Claim | Block | n | ≤8 (95% CI) | ≤7 | ≤3 | Median \|m\| | Lift | Seasons lift > 1 |
|---|---|---|---|---|---|---|---|---|
| CLOSENESS (union) | dev | 1,670 | 42.0% (40–44) | 38.9% | 18.0% | 11 | 1.10 | 5/5 |
| | **holdout** | 1,969 | **41.9% (40–44)** | 38.9% | 18.3% | 11 | **1.18** | **7/7** |
| CLOSENESS_EVEN | dev | 862 | 46.5% (43–50) | 43.4% | 20.9% | 10 | 1.22 | 5/5 |
| | **holdout** | 943 | **46.9% (44–50)** | 43.8% | 21.3% | 10 | **1.32** | **7/7** |
| CLOSENESS_NARROW (reported) | holdout | 1,026 | 37.2% | 34.4% | 15.6% | 14 | 1.05 | — |
| Reference: no efficiency edge | holdout | 2,559 | 41.7% | 38.7% | 18.6% | 11 | 1.18 | — |
| Reference: no edge and not EVEN | holdout | 1,616 | 38.7% | 35.7% | 17.0% | 13 | 1.09 | — |

* Both pre-registered closeness claims **PASS**.
* **Interpretation:**
  * The union's lift equals that of "no efficiency edge" (41.9% vs 41.7%). NARROW is the same evidence as a MODERATE
    efficiency edge: all 1,026 NARROW games are MODERATE-control games.
  * **A standalone closeness claim is justified only as CLOSENESS_EVEN.** It is 46.9% against 38.7% for edge-free
    games without EVEN, it is stable in every season (holdout lift 1.05-1.67), and it is not redundant with CONTROL.

## 6. Scoring environment, pace, defensive suppression (holdout)

* Total percentiles are within-season.
* "Matched" is the baseline-matched percentile difference across season × baseline-total-quintile strata.

| Claim | n | Mean total pct (95% CI) | Extreme third (lift) | Extreme quartile | Actual − baseline | Matched diff (95% CI) | Plays pct | Seasons correct |
|---|---|---|---|---|---|---|---|---|
| ELEVATED | 994 | 0.642 (0.625–0.657) | 54.0% (1.62) | 43.9% | −0.2 | **+0.056 (0.015–0.099)** | 0.616 | 7/7 |
| ELEVATED strengthened | 315 | 0.694 | 61.6% (1.85) | 53.6% | +0.2 | +0.078 (0.043–0.114) | 0.668 | — |
| ELEVATED base-only | 679 | 0.618 | 50.5% | 39.3% | −0.4 | −0.017 (−0.046–0.012) | 0.592 | — |
| SUPPRESSED | 871 | 0.357 (0.340–0.374) | 52.3% (1.57) | 42.5% | +1.4 | **−0.011 (−0.053–0.030)** | 0.401 | 7/7 |
| SUPPRESSED strengthened | 274 | 0.337 | 54.0% | 46.7% | +2.1 | −0.029 (−0.067–0.008) | 0.421 | — |
| PACE_HIGH | 809 | 0.616 (0.597–0.635) | 50.2% (1.51) | 38.7% | +2.1 | **+0.055 (0.033–0.078)** | **0.759** (0.744–0.772) | 7/7 (plays) |
| PACE_HIGH without elevated scoring | 428 | 0.557 | 40.6% | 30.4% | +2.8 | +0.043 (0.015–0.071) | 0.736 | — |
| PACE_LOW | 855 | 0.391 (0.372–0.409) | 48.3% (1.45) | 37.8% | −2.1 | **−0.061 (−0.082 to −0.040)** | **0.254** (0.239–0.267) | 7/7 (plays) |
| DEFENSIVE_SUPPRESSION | 309 | 0.358 (0.328–0.388) | 49.8% (1.50) | 43.0% | −0.1 | **−0.034 (−0.066 to −0.003)** | 0.455 | 7/7 |
| DEFENSIVE_SUPPRESSION without suppressed scoring | 97 | 0.409 | 44.3% | 37.1% | −2.4 | −0.045 (−0.096–0.008) | 0.526 | — |

* **ELEVATED:** passes (E1-E3) and **is incremental beyond the descriptive baseline (E4)**. The strengthened subset
  carries the increment; base-only adds nothing beyond the baseline. This is the same pattern as in development.
* **SUPPRESSED:** passes E1-E3, so it describes low-scoring games. **It is not incremental** on the holdout
  (development matched −0.078; holdout −0.011). What it adds is already in the scoring baseline.
* **PACE_HIGH:** passes. It is a separate, incremental signal on both plays and points.

  | Measure | Value |
  |---|---|
  | Plays percentile | 0.759 |
  | Realized PACE_DRIVEN_OVER label | 45.4% vs base 25.7% (lift 1.76) |
  | Matched total difference | +0.055 |

  **HIGH_POSSESSION should remain its own pace signal.**
* **PACE_LOW:** passes (plays percentile 0.254; matched total −0.061).
* **DEFENSIVE_SUPPRESSION:** **passes**. **Wave 1's apparent lift generalizes:**

  | Measure | Development | Holdout |
  |---|---|---|
  | Bottom third | 54.9% | 49.8% (vs 33%) |
  | Realized suppression label | 34.8% (lift 1.95) | 30.7% vs base 18.5% (lift 1.67) |
  | Seasons below 0.5 | — | 7/7 |

  It is modestly incremental (matched −0.034). It had only 24 published V1 scripts on the holdout (GRIND exclusivity).

## 7. DISRUPTION (unit = game × side)

| Claim | Block | n | Win (95% CI) | Expected | TD label (lift) | Mechanism | Median margin |
|---|---|---|---|---|---|---|---|
| DISRUPTION_EDGE | dev | 908 | 71.9% (69–75) | 50.9% | 13.1% (1.71) | 66.6% | 9 |
| | **holdout** | 786 | **69.0% (66–72)** | 50.2% | 12.1% (2.22†) | 61.6% (base 46.4%) | 10 |
| with volatility | holdout | 190 | 72.6% | 50.6% | 11.1% | 66.8% | 12.5 |
| without volatility | holdout | 596 | 67.8% | 50.1% | 12.4% | 59.9% | 8.5 |
| with the side's own efficiency edge | holdout | 478 | 82.2% | 50.6% | 15.7% | 69.7% | 18 |
| no efficiency edge either side | holdout | 271 | **51.3%** | 49.7% | 7.0% | 51.0% | 1 |

† The pooled per-side TD base (5.5%) includes 2014-2015, where the realized TD label cannot fire (no sacks/TFL).
Disclosed sensitivity restricted to the 2016-2020 seasons that have the data: base 7.8%, claim rate 12.1%, **lift
1.55** (Wilson lower bound 10.0%). D2 still passes.

* **Criteria:**

  | Criterion | Result |
  |---|---|
  | D1 | pass |
  | D2 | pass |
  | D3 | pass |
  | D4 | pass: 5 of 5 evaluable seasons, 2016-2020 (TD lift 1.54, 1.79, 1.50, 1.10, 2.25) |
  | **DISRUPTION** | **PASS** |

* **Volatility adds nothing** (pre-registered test):

  | With minus without volatility | Difference | 95% CI |
  |---|---|---|
  | TD label | −1.4 pts | −6.1 to +4.4 |
  | Win rate | +4.8 pts | −2.8 to +11.9 |

* **Caveat for Wave 3.** Disruption's directional value is almost entirely shared with CONTROL. With no efficiency
  edge on either side, the disruption side wins 51.3% against 49.7% expected. The claim describes a realized mechanism
  (61.6% sacks+TFL edge vs 46.4%) more than it predicts a winner.

## 8. Confidence and evidence score (holdout)

**Wave 1's non-monotonicity repeats**: 4 of the 4 audited V1 archetypes are non-monotone in tier.

| V1 archetype | HIGH | MEDIUM | LOW |
|---|---|---|---|
| HOME_CONTROL | 33.6% | 38.4% | 24.5% |
| AWAY_CONTROL | 30.0% | 32.9% | 32.9% |
| FAVORITE_PULLS_AWAY | 57.3% | 53.6% | 64.3% |
| PACE_DRIVEN_OVER | 46.3% | 47.9% | 45.1% |

* The V1 evidence-score buckets are also non-monotone. HOME_CONTROL by bucket: ≤1 37.9%, 2 38.6%, 3 29.1%, ≥4 32.2%.
* **V2 CONTROL win rate separates data-quality confidence from outcome strength:**

  | Grouping | Group | Win rate | Median margin |
  |---|---|---|---|
  | Outcome strength | MODERATE | 73.7% | 9 |
  | | STRONG | 88.4% | 21 |
  | Data-quality confidence | HIGH | 80.3% | 14 |
  | | MEDIUM | 82.1% | 14 |
  | | LOW | 86.1% | 21.5 |
  | Prior games | 1-2 | 89.0% | 28 |
  | | 5+ | 81.0% | 14 |
  | Adjustment stability | unstable | 85.0% | — |
  | | stable | 81.5% | — |

* **Conclusion:** the production confidence tier is a **data-quality** statement. Low-data games are early-season
  mismatches, so they show *larger*, not less reliable, margins. **Edge strength** (MODERATE/STRONG) is the outcome
  variable. Neither the tier nor the V1 evidence score should be used as an outcome ranking.

## 9. No-claim analysis (holdout)

| Group | Share | \|m\|-bucket entropy | Median \|m\| | One-score | Total SD |
|---|---|---|---|---|---|
| No directional claim | 51.6% | 2.317 | 11 | 41.7% | 18.7 |
| Directional claim | 48.4% | 2.225 | 18 | 28.8% | 18.8 |
| No closeness claim | 60.3% | 2.254 | 17 | 31.2% | 18.6 |
| No environment claim (scoring, pace, suppression) | 43.4% | 2.288 | 15 | 34.7% | 17.2 |
| **No V2 claim at all** | **12.8%** | **2.305** | 14 | 37.3% | 16.8 |
| At least one V2 claim | 87.2% | 2.296 | 14 | 35.2% | 19.0 |
| All games | — | 2.297 | 14 | 35.5% | 18.7 |

* Abstaining from **every** claim (12.8%; V1 no-script 15.0%) does **not** isolate more heterogeneous games: entropy
  is 2.305 vs 2.296.
* Abstaining from the **directional** claim does carry information: those games are closer (41.7% one-score vs
  28.8%).
* Abstention is a coverage decision, not an uncertainty detector, as in Wave 1.

## 10. Exploratory: inefficient wins

The family is a winner by 9-24 who lost the efficiency battle.

| Block | Family rate | Rate within the pre-selected indicator (FINISHING_ADVANTAGE with no efficiency edge) |
|---|---|---|
| Development | 7.0% | 9.7% (lift 1.37) |
| Holdout | 6.7% | 6.1% (**lift 0.92**) |

* **The development indicator did not replicate.**
* In development the family is turnover-driven: the winner had a turnover edge of 2+ in 55% of these games, against
  37% of all 9-24-point wins. Winners also finished better (73% had a better points-per-opportunity).
* **Conclusion (exploratory):** a realized family driven by turnovers and finishing, **not predictably identifiable**
  from the existing pregame findings.

## 11. EXPLOSIVE_UPSET

**UNTESTED_HISTORICALLY_DATA_UNAVAILABLE.** CFBD has no explosive-play counts in 2014-2020 either (0 games in every
season). This is not a failure. Production behavior is untouched.

## 12. 2026 prospective (not used for anything)

The `script-ledger` branch at `8a270a68e`, 2026-10-07 23:19Z, holds 886 rows:

| Row kind | Rows |
|---|---|
| PUBLICATION | 884 |
| FINAL_PREGAME | **2** |
| Settled realized (FINAL_PREGAME) | 1 |
| Settled realized (PUBLICATION) | 1 |

Far too few for any descriptive check. They were not read by V2.

## 13. Deviations

* **D1.** Reporting-only: `compare.py` was extended to return the V1 confidence/evidence audit in the protocol commit
  itself, before any holdout data was opened.
* **D2.** The disruption TD-label lift was additionally computed on 2016-2020 (§7) after the gap was found. It is
  disclosed, it does not replace the pre-registered number, and it does not change the verdict.

No definition, threshold, tolerance, exclusion or season was changed after scoring.
