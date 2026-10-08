# Archetype V2 — Sealed-Holdout Protocol (Wave 2)

Status: **PRE-REGISTERED.** Committed before any 2014-2020 game was replayed, predicted or scored. No 2014-2020
file has been extracted from the cache at the time of this commit.

| Item | Value |
|---|---|
| Base main | `f574e0db3be383697f1f0cd128e2aaffd5e85aba` (Wave 1 merged, PR #98) |
| Implementation commit | `a68da9c2af13ec1bbbe5c0c51d97e1b5a9aacc0f` (`src/cfb_edge_finder/archetype_research/v2/`), plus one reporting-only addition in this protocol commit (`compare.py` also returns the V1 confidence/evidence-score audit). No claim, fit or criterion code changed. |
| Frozen development parameters | `data/scripting/validation/archetype_v2_frozen_parameters.json` |
| **Frozen parameters SHA-256** | **`683d075d99efdfed16f7fb35a9fd58234808d1a354f8fb8e3352c64e972bf7aa`** |
| Development report (in-sample) | `data/scripting/validation/archetype_v2_development_report.json` |
| V2 version | `cfb-archetype-v2-candidate/1.0.0` |
| Production methodology (unchanged) | `cfb-script-engine/1.3.0` |

The holdout runner (`scripts/run_archetype_v2_study.py`) refuses to predict or reveal unless this file exists, is
marked PRE-REGISTERED and names the hash above, and the frozen file still matches it.

## 1. Season roles

| Block | Seasons | Use |
|---|---|---|
| DEVELOPMENT | 2021, 2022, 2023, 2024, 2025 | The only data V2 was designed on and fitted on (Wave 1 rows) |
| SEALED HISTORICAL HOLDOUT | 2014, 2015, 2016, 2017, 2018, 2019, 2020 | Scored once, after this commit |
| PROSPECTIVE | 2026 ledger | Never an input to V2; settled-row count reported only |

All seven holdout seasons are evaluated, **including 2020** (the COVID season, with its shortened and conference-only
schedules). No season may be dropped after scoring. Development and holdout are never pooled for any criterion. An
all-years descriptive table may be shown after the holdout verdict, labelled descriptive.

## 2. Data, replay and exclusions (identical to Wave 1)

* **Source.** CFBD cache on `research-data` (`data/research_cache/v2/<season>/`). The files read are `games`,
  `games_teams`, `advanced_regular_nogarbage`, `drives_regular`, `advanced_postseason` and `drives_postseason`. They
  are converted by production `scripting.cfbd.team_games_from_cfbd`.
* **Never read:** lines, Elo, win probability, rankings, ratings, talent or recruiting.
* **Replay.** For each game, `history = gamelog.before(all_rows, data_cutoff(kickoff))` and only `history` goes to
  the unmodified `football.build_content`. The pregame record is frozen and hashed. The V2 claims are computed from
  that record and hashed. Then:
  * a per-game manifest of (pregame_hash, v2_hash) is written and committed **before any target result is read**;
  * the reveal re-checks every hash.
* **Exclusions (pre-registered; outcome-independent).** Not completed; no box-score rows; identity/orientation
  mismatch; missing score; either team with no prior game before the cutoff; sustained-efficiency profile undefined;
  scoring baseline undefined. Every excluded game is counted with its reason.
* **Explosive plays.** CFBD has no explosive-play counts in any season. **EXPLOSIVE_UPSET is
  `UNTESTED_HISTORICALLY_DATA_UNAVAILABLE`.** It is not altered, not retired and not scored, and no explosive
  quantity is approximated or fabricated.

## 3. Exact V2 claims (read only from production findings in the frozen pregame record)

Dimensions are independent: a game may carry any combination, and no exclusivity is imposed.

| Dimension | Claim | Rule |
|---|---|---|
| Direction × strength | `HOME_CONTROL_MODERATE`, `HOME_CONTROL_STRONG`, `AWAY_CONTROL_MODERATE`, `AWAY_CONTROL_STRONG` | `S_SUSTAINED_EFFICIENCY_ADVANTAGE` present (production thresholds 1.0 / 2.0 and noise gate), tier = its strength. No second edge, no evidence-score selection. FAVORITE_PULLS_AWAY is absorbed into the STRONG tier. |
| Closeness | `CLOSENESS` | `EVEN_MATCHUP` with margin authority **or** `NARROW_EFFICIENCY_GAP` with margin authority (production `closeness_grants_margin`). Claims only "raised chance of a one-score result": no side, winner or environment. |
| | `CLOSENESS_EVEN` (component, evaluated separately) | `EVEN_MATCHUP` with margin authority |
| | `CLOSENESS_NARROW` (component, reported) | `NARROW_EFFICIENCY_GAP` with margin authority. It is the same evidence as a MODERATE efficiency edge (development: 808 = 808 games), so it is reported, not separately judged. |
| Scoring environment | `ELEVATED_SCORING_ENVIRONMENT` | **Activated by** `HIGH_SCORING_ENVIRONMENT` or `BOTH_OFFENSES_EFFICIENT`. **Strengthened by** both present, or HIGH_SCORING STRONG. **Supported (reported only) by** `HIGH_POSSESSION_ENVIRONMENT`. |
| | `SUPPRESSED_SCORING_ENVIRONMENT` | **Activated by** `LOW_SCORING_ENVIRONMENT`. **Strengthened by** `BOTH_DEFENSES_CONTROL` present, or LOW_SCORING STRONG. **Supported by** `LOW_POSSESSION_ENVIRONMENT`. |
| | (no claim) | Elevated and suppressed activators both present ⇒ `MIXED`, no claim |
| Pace (own dimension) | `PACE_HIGH` / `PACE_LOW` | `HIGH_POSSESSION_ENVIRONMENT` / `LOW_POSSESSION_ENVIRONMENT`. Never activates a scoring claim and never becomes a numerical total band. |
| Defensive suppression (own dimension) | `DEFENSIVE_SUPPRESSION` | `BOTH_DEFENSES_CONTROL`, independent of any GRIND exclusivity |
| Disruption (per side) | `S_DISRUPTION_EDGE` | `S_DISRUPTION_ADVANTAGE` present. The volatility condition (opponent `OFFENSE_TURNOVER_PRONE`, own `DEFENSE_TURNOVER_RELIANT`, or `HIGH_VARIANCE_MATCHUP`) is **recorded, not required**. |
| — | `EXPLOSIVE_UPSET` | `UNTESTED_HISTORICALLY_DATA_UNAVAILABLE` |

* **No claim authorizes a side/margin statement except CONTROL.** Environment, pace, suppression and closeness state
  no winner.
* **Data-quality confidence is carried beside the claims and never changes them:** the production HIGH/MEDIUM/LOW
  tier, minimum prior games, and adjustment stability. **Outcome strength** is the MODERATE/STRONG tier.

### Why these definitions (development 2021-2025 only)

| Evidence | n | Result |
|---|---|---|
| One-score rate, EVEN_MATCHUP resolved | 862 | 46.5% (base 38.1%) |
| One-score rate, NARROW resolved | 808 | 37.3% |
| One-score rate, union | 1,670 | 42.0% |
| Mean within-season total percentile, HIGH_SCORING | 685 | 0.609 (matched +0.048) |
| BOTH_OFFENSES_EFFICIENT | 234 | 0.612 (+0.036) |
| HIGH_POSSESSION | 582 | 0.586 (+0.044) |
| LOW_SCORING | 621 | 0.356 (−0.079) |
| BOTH_DEFENSES_CONTROL | 224 | 0.335 (−0.082) |
| LOW_POSSESSION | 615 | 0.417 (−0.051) |
| LOW_SCORING without BOTH_DEFENSES_CONTROL | — | baseline-matched +0.004 |
| LOW_SCORING with BOTH_DEFENSES_CONTROL | — | baseline-matched −0.100 (hence "strengthens") |
| Disruption edge, win rate | 908 | 71.9% (vs 50.9% expected) |
| Disruption + volatility vs without, TD label | 258 / 650 | 14.0% vs 12.8% (difference CI −3.5 to +6.5 pts) |

## 4. Empirical margin distributions and frozen intervals (DEVELOPMENT ONLY — historical, not probabilities)

* **Supported-side margin.** Lead side for CONTROL. Quantiles use numpy linear interpolation.
* **Intervals.** Central-50 = [p25, p75]; central-80 = [p10, p90]. Coverage on the integer margin is inclusive:
  lo ≤ m ≤ hi.

| Tier | n | Win | Mean | p10 | p25 | p50 | p75 | p90 | >0 | ≥3 | ≥7 | ≥10 | ≥14 | ≥17 | ≥21 | ≥24 | ≥28 | **Frozen central-50** | **Frozen central-80** | Dev in-sample cov 50 / 80 |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| HOME_CONTROL_MODERATE | 404 | 80.0% | 12.93 | −7.0 | 3.0 | 12.0 | 24.0 | 34.0 | 80.0 | 76.0 | 65.6 | 56.9 | 48.8 | 41.8 | 34.4 | 26.5 | 19.6 | **[3.0, 24.0]** | **[−7.0, 34.0]** | 52.5% / 81.7% |
| HOME_CONTROL_STRONG | 628 | 90.1% | 22.76 | 1.0 | 8.0 | 23.0 | 35.0 | 48.0 | 90.1 | 87.4 | 78.3 | 72.9 | 67.0 | 61.9 | 55.4 | 49.5 | 41.2 | **[8.0, 35.0]** | **[1.0, 48.0]** | 50.8% / 80.6% |
| AWAY_CONTROL_MODERATE | 404 | 66.6% | 7.02 | −13.7 | −4.0 | 7.0 | 18.0 | 30.0 | 66.6 | 63.4 | 52.7 | 42.1 | 32.9 | 27.5 | 22.3 | 17.8 | 13.4 | **[−4.0, 18.0]** | **[−13.7, 30.0]** | 52.0% / 80.0% |
| AWAY_CONTROL_STRONG | 378 | 81.0% | 15.14 | −5.3 | 3.0 | 14.5 | 27.0 | 37.0 | 81.0 | 75.7 | 67.7 | 59.0 | 52.4 | 47.9 | 38.1 | 31.8 | 24.9 | **[3.0, 27.0]** | **[−5.3, 37.0]** | 50.8% / 80.2% |

No other strata are fitted. Holdout scoring reads these intervals from the frozen file and has no fitting path. The
evaluation module does not import the fit module (a test enforces this).

## 5. Metrics (computed within each block; base rates are that block's own)

* **CONTROL**, per tier:
  * the supported-side distribution in §4 (n, win rate with Wilson 95% interval, mean, p10-p90, P(>0, ≥3, ≥7, ≥10,
    ≥14, ≥17, ≥21, ≥24, ≥28));
  * win-rate lift over the block's home (or away) win rate;
  * coverage of the frozen intervals;
  * coverage of the V1 bands 7-24 and 17-45;
  * all of the above by season.
  * STRONG − MODERATE win-rate difference (Newcombe 95% interval) and median difference.
* **CLOSENESS, CLOSENESS_EVEN and CLOSENESS_NARROW:**
  * P(|m| ≤ 8), P(≤ 7) and P(≤ 3), with Wilson intervals;
  * median |m|;
  * lift over the block's unconditional one-score rate, by season;
  * reference: games with no efficiency edge, with and without EVEN.
* **Environments** (ELEVATED, SUPPRESSED, their strengthened and base-only subsets, ELEVATED with pace support,
  PACE_HIGH, PACE_HIGH without elevated scoring, PACE_LOW, DEFENSIVE_SUPPRESSION, DEFENSIVE_SUPPRESSION without
  suppressed scoring):
  * mean within-season total percentile, with a 2,000-resample bootstrap 95% interval (seed 20261008);
  * P(top third) for UP claims or P(bottom third) for DOWN claims, with its lift over 1/3;
  * extreme quartile;
  * mean actual minus baseline;
  * the baseline-matched percentile difference across season × baseline-total-quintile strata, weighted by
    flagged count, with a game bootstrap interval;
  * mean within-season total-plays percentile, with a bootstrap interval;
  * realized research labels (Wave 1 definitions): PACE_DRIVEN_OVER for PACE_HIGH, DEFENSIVE_SUPPRESSION for
    BOTH_DEFENSES_CONTROL;
  * all of the above by season.
* **Disruption** (unit = game × side): win rate against the expected win rate of the units' home/away mix; the
  realized TURNOVER_DISRUPTION label (Wave 1 definition) and its lift over the per-side base; the realized mechanism
  (own sacks + TFL > opponent's) against its per-side base; margin distribution; by season; with vs without
  volatility (Newcombe intervals); with the side's own efficiency edge; with no efficiency edge on either side.
* **No-claim shares** (no directional, no closeness, no environment, no V2 claim at all):
  * entropy of the |margin| bucket distribution (0-3, 4-8, 9-16, 17-24, 25+);
  * |margin| and total quantiles;
  * one-score rate.
* **Data quality vs outcome strength:** CONTROL win rate by confidence tier, prior-games bucket (1-2, 3-4, 5+) and
  adjustment stability, against MODERATE/STRONG.
* **V1 evaluation:** Wave 1 analyses on the same holdout records, giving the V1 confidence/evidence-score audit.
  Wave 1's non-monotonicity "repeats" if V1 label-match PPV is not monotonically HIGH ≥ MEDIUM ≥ LOW for at least two
  of HOME_CONTROL, AWAY_CONTROL, FAVORITE_PULLS_AWAY and PACE_DRIVEN_OVER, among tiers with n ≥ 30.

## 6. Season-stability rule

A season is **evaluable** for a claim when the claim has at least this many units there:

| Claim family | Minimum units per season |
|---|---|
| CONTROL | 15 |
| Closeness | 20 |
| Environment / pace | 20 |
| Defensive suppression | 10 |
| Disruption | 15 |

A season rule **passes** when at least 5 of the 7 holdout seasons are evaluable and at least 5 seasons pass. Fewer
than 5 evaluable seasons is INCONCLUSIVE, which does not pass.

## 7. Success criteria (sealed holdout)

* **CONTROL.** Each of the four tiers must pass C1 and C2, and C3 must hold for both HOME and AWAY.
  * **C1:** Wilson 95% lower bound of the pooled tier win rate is above the block's home (or away) win rate.
  * **C2:** the tier win rate is above that season's home (or away) win rate in at least 5 seasons.
  * **C3 (ordering):** for HOME and for AWAY, STRONG has a higher pooled win rate **and** a higher median margin than
    MODERATE, and STRONG has the higher win rate in at least 5 seasons where both tiers have n ≥ 15.
* **CONTROL_INTERVALS.** Holdout coverage of the frozen intervals must be within these limits:

  | Interval | Calibrated (tolerance) | Hard limit, every tier |
  |---|---|---|
  | central-50 | 42-58% | 35-65% |
  | central-80 | 72-88% | 65-93% |

  At least 3 of 4 tiers must be calibrated on both intervals, and no tier may fall outside the hard limits.
* **CLOSENESS / CLOSENESS_EVEN** (each judged separately):
  * **K1:** one-score lift ≥ 1.10 and Wilson lower bound above the block's one-score base.
  * **K2:** lift > 1 in at least 5 seasons.
* **ELEVATED / SUPPRESSED** (each judged separately):
  * **E1:** the bootstrap interval of the mean total percentile excludes 0.5 on the predicted side.
  * **E2:** extreme-third lift ≥ 1.15 and Wilson lower bound above 1/3.
  * **E3:** the mean percentile is on the predicted side in at least 5 seasons.
  * **E4 (reported, not required):** the baseline-matched difference interval excludes 0 on the predicted side,
    which means the claim is incremental beyond the descriptive baseline.
* **PACE_HIGH:**
  * the plays-percentile interval excludes 0.5 above;
  * the realized PACE_DRIVEN_OVER label lift is ≥ 1.20 with Wilson lower bound above base;
  * the plays-percentile direction is correct in at least 5 seasons.
* **PACE_LOW:** the plays-percentile interval excludes 0.5 below, and the direction is correct in at least 5 seasons.
* **DEFENSIVE_SUPPRESSION:**
  * **S1:** bottom-third lift ≥ 1.15 with Wilson lower bound above 1/3.
  * **S2:** the mean-percentile interval lies below 0.5.
  * **S3:** realized suppression-label lift ≥ 1.20 with Wilson lower bound above base.
  * **S4:** the mean percentile is below 0.5 in at least 5 seasons (n ≥ 10).
* **DISRUPTION:**
  * **D1:** Wilson lower bound of the win rate is above the side-mix expected win rate.
  * **D2:** TD-label lift ≥ 1.20 with Wilson lower bound above the per-side base.
  * **D3:** mechanism Wilson lower bound is above the per-side base.
  * **D4:** TD lift > 1 in at least 5 seasons.
  * **Volatility adds value** only if the with-minus-without TD-label difference interval lies above 0.

## 8. Decision rules (fixed)

The **other families** are:

* CLOSENESS or CLOSENESS_EVEN
* ELEVATED **and** SUPPRESSED
* PACE_HIGH
* DEFENSIVE_SUPPRESSION
* DISRUPTION

**Verdict:**

| Verdict | Condition |
|---|---|
| V2 GENERALIZES STRONGLY ON SEALED HOLDOUT | CONTROL and CONTROL_INTERVALS pass, and at least 3 of the 5 other families pass |
| V2 PARTIALLY GENERALIZES | CONTROL passes, or at least 2 other families pass |
| V2 FAILS SEALED HOLDOUT | otherwise |

**Recommendation:**

| Recommendation | Condition |
|---|---|
| V2 DESERVES A FORMAL PRODUCTION-MIGRATION REVIEW | CONTROL and CONTROL_INTERVALS pass |
| V2 DESERVES LIMITED FOLLOW-UP | CONTROL passes, or at least 2 other families pass |
| DO NOT PROMOTE V2 | otherwise |

* Only families that pass are carried to Wave 3. A failed family is reported as failed and is not redefined.
* Production is not changed under any outcome.

## 9. Exploratory items (no effect on the verdict)

* **Inefficient-win family:** a winner by 9-24 who lost the efficiency battle (development base 7.0%). The single
  indicator pre-selected on development is **FINISHING_ADVANTAGE for either side with no sustained-efficiency edge
  for either side** (development 9.7% vs 7.0%, n 404). The holdout reports its rate and lift. It is not a claim.
* **Confidence / evidence-score audit:** see §5.
* **V1 vs V2 comparison (§10):** reported descriptively; it is not a criterion.

## 10. V1 vs V2 comparison metrics (holdout)

* **Redundancy:** share of V1 PULLS_AWAY scripts co-published with the same-side CONTROL; V1 scripts per game; V2
  CONTROL tiers are exclusive by construction; share of CLOSENESS_NARROW games that are MODERATE control.
* **Coverage:** V1 any-script and no-script rates; V2 any-claim, no-claim and directional-claim rates.
* **Directional accuracy:** win rate of V1 PRIMARY scripts that have a winner lean, against V2 CONTROL claims.
* **Stable lift:** V1 archetypes whose lift CI is above 1 with lift > 1 in at least 5 seasons; V2 families passing.
* **One-score discrimination:** V1 scripts stating the ±8 band, against V2 CLOSENESS and CLOSENESS_EVEN.
* **Scoring discrimination:**

  | V1 | V2 |
  |---|---|
  | SHOOTOUT, PACE_DRIVEN_OVER, GRIND, SUPPRESSION | ELEVATED, SUPPRESSED, PACE, DEFENSIVE_SUPPRESSION |

  Compared on mean percentile and baseline-matched difference.
* **Margin description:** V1 7-24 and 17-45 band coverage, against V2 absolute coverage error from nominal.
* **Abstention:** V1 no-script rate and |margin| entropy, against the V2 no-claim rate and entropy.

## 11. What this protocol forbids

* looking at 2014-2020 outcomes before this commit;
* changing any definition, threshold, tolerance, stratum or exclusion after holdout scoring;
* dropping a season;
* pooling 2014-2025 for model selection;
* using 2026 outcomes for anything but a separate descriptive count;
* calling any frequency a probability or betting edge;
* changing production.
