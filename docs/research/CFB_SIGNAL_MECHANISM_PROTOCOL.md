# Football Signal Discovery Lab — Wave 2C (CFB): signal-mechanism study — PROTOCOL

**Status: PRE-REGISTERED.** This file is committed alone, before any mechanism output is computed.
Results go to `docs/research/CFB_SIGNAL_MECHANISM_RESULTS.md` and
`data/scripting/validation/signal_mechanisms/` (artifact `cfb_signal_mechanisms/1.0.0`). An observation that is not
pre-registered here is reported as **POST_HOC** and is never re-labelled.

## 0. What this study is and is not

* **Purpose.** Explain *why* the Wave-1 CFB findings behave as they do:
  * why rushing beyond efficiency adds margin information the closing spread misses;
  * why passing beyond efficiency has the opposite residual;
  * why CONTROL predicts winners but not covers;
  * why the 2026 rushing feature is shifted;
  * why a STRONG CONTROL with a skeptical market is rare;
  * and, secondarily, how the totals market absorbs pace, defensive suppression and the scoring environment.
* **Explanatory only.** This study does not create:
  * a betting rule, filter, threshold, exclusion or recommendation;
  * a stake, price or production edge.

  It does not touch:
  * `CFB-PROS-001` or `CFB-PROS-002`, their thresholds, standardisation or `PRIMARY_60_180`;
  * CONTROL, V1/V2, the Script Engine, Value Watch, SIFT, staking or the router;
  * the prospective ledgers or the Wave-2 capture / settlement.
* **No promotion.** Any subgroup that looks actionable is written to
  `docs/research/CFB_MECHANISM_FUTURE_HYPOTHESES.md` and is not tested.
* **Contamination.** Every historical number here was produced from data Wave 1 already used to discover these
  signals, and the 2026 rows were used by Wave 2A. Nothing here is independent evidence of an edge. The prospective
  Wave-2 streams have no settled sample, and none is read.

## 1. Provenance at registration

| Item | Value |
|---|---|
| Source main | `e1310b00de305aaddfe5ce62598fe1ea5e915ae9` (fetched 2026-10-09). Wave 1 `45a52771`, Wave 2 `a4ad99c8` / `c5f33fa5`, Wave 2A `8ee19ef7` (head `1a9b6ee3`) are all ancestors. |
| Wave-2 candidates | `signal_discovery_wave2/candidates.json`: canonical SHA-256 `bf19c972…6e1ce5` (the pinned registration); file SHA-256 `dbc97b65…bea80` |
| Wave-2 code | `wave2.py` `f8f235b2…18ea`; `wave2_cycle.py` `b729facf…462e`; protocol `79f748db…63c1` |
| Wave-1 artifacts | `evaluation_report.json` `682a4286…6ddd`; `evaluation_rows.jsonl.gz` `3f6c9642…91eb`; `football_signal_registry.json` `10475346…af83`; `hypotheses_set1.json` `597b6658…3874`; `hypotheses_set2.json` `6fd9e20d…cfb9`; `stage_a_screen.json` `f5fe9708…e30`; `features/features_manifest.json` `99b5ca81…ea80` |
| Wave-2A artifacts | `extract_manifest.json` `4ce8d89b…a10d`; `history_report.json` `5550d3c9…f14c`; `replay_membership_2026.jsonl.gz` `ee46fa79…a040`; `replay_report_2026.json` `e70cb71d…e6fe`; `replay_rows_2026.jsonl.gz` `c60e90ea…f7bea`; `spread_captures_2026.jsonl.gz` `dde61573…6ac2`; `winner_quotes_supplement_2026.jsonl.gz` `c89dcd68…7619`; protocol `e204e2f7…2080` |
| CFBD cache | `research-data@292f3aac` `data/research_cache/v2/{2014..2026}` |
| 2026 football log | main `8173fd5e` (the Wave-2A pin) `data/football/2026/team_games.jsonl` + `schedule.json` |
| 2026 sportsbook | `research-sidecar` `data/research/live_sidecar/espn_odds/2026.jsonl` (DraftKings via ESPN; the last fetch strictly before kickoff) |

**Integrity anchor, checked before registration.** Re-running the Wave-1 join gives:
* 8,786 eligible rows;
* the frozen PROS-001 rule (`defdiff.rush_success_rate`, `(x − 0.13804024626610417) / 1.4218539928759784`,
  |z| ≥ 1) gives n 2,713, mean ATS residual +1.4003870, 1,408–1,260–45.

These equal Wave 1 and Wave 2A exactly. The analysis re-checks them on every run and refuses to continue if they
differ.

## 2. Data (what can be used; nothing is imputed)

| Layer | Historical 2014–2025 | 2026 |
|---|---|---|
| Pregame features | Wave-1 feature tables (production builder, 04:00 ET cutoff, CFBD-derived `TeamGame` rows) | the same builder (`wave2_cycle.feature_for`) on the ESPN-derived log pinned at `8173fd5e`, checked against the committed Wave-2A membership values |
| CONTROL | production claim on every feature row (`control_side`, `control_strength`) | the Wave-2A CONTROL replay (82 STRONG) |
| Spread / total | CFBD `/lines` consensus (Wave-1 `market_lines`), untimestamped close; opener 2021+ (spread; total opener from `overUnderOpen`) | Kalshi PRIMARY_60_180 spread center (Wave-2A rows); DraftKings spread / total (sidecar) |
| Outcomes (read only after membership is fixed) | CFBD final score and quarter line scores; per-team box (plays, first downs, possession time, third / fourth downs, turnovers, rush / pass); CFBD success counts (garbage time excluded); drives (results, plays, yards-to-goal) | the ESPN log's box and play-log counts and drive totals; the ESPN schedule score |
| Public / visible proxies | CFBD pregame Elo; AP poll by week; record and scoring margin to date (prior games only) | — |

**Unavailable, declared, not imputed:**
* historical explosive-play counts (CFBD `explosiveness` is a PPA magnitude, labelled as such where shown);
* QB identity and injuries before 2026;
* play-by-play short-yardage and goal-to-go;
* 2026 CFBD box / advanced data (the 2026 cache holds 8 completed games);
* ESPN logs for any historical season (the ESPN API is not reachable from this environment).

## 3. Constructs (fixed now; outcome-free and market-free)

* **Home perspective.** `margin = home − away`; `spread_home` < 0 means home favoured; `exp_margin = −spread_home`;
  `ats_resid = margin + spread_home`; `total_resid = total − market total`.
* **Residualised dimensions.**
  * `rush_res` = `net.rushing − b·net.sustained_efficiency`, and `pass_res` likewise for `net.passing`.
  * b is the OLS slope over all eligible 2014–2025 rows (a feature-on-feature regression, with no outcome and no
    market).
  * Both are standardised by their 2014–2025 SD: `rush_res_z`, `pass_res_z`.
* **Frozen rush-edge side (FRS).** The exact PROS-001 rule, imported from `wave2` (`DSC001_*`, `Z_THRESHOLD`).
* **Residual rush / pass sides (descriptive).** The side favoured when |rush_res_z| ≥ 1 (RRS), or |pass_res_z| ≥ 1
  (PRS).
* **CONTROL.** Production `control_side` / `control_strength`. **Magnitude** = `net.sustained_efficiency` signed
  toward the CONTROL side.
* **Rush offense / defense split.**
  * `rush_off_diff` = home − away `off_q.rush_success_rate`.
  * `rush_def_diff` = home − away `def_q.rush_success_rate` (the frozen feature).
  * The same for pass success: `pass_off_diff`, `pass_def_diff`.
* **Totals features.**
  * pace = `possession_environment`;
  * defensive suppression = `def_quality_sum`, plus the production claim;
  * scoring environment = `off_quality_sum`, plus the production `scoring_env_claim`.
* **Game-level mechanism outcomes** (post-game, home − away unless noted):
  * quarter margins Q1–Q4, 1H, 2H (Q3 + Q4), OT;
  * plays; possession seconds; first downs; third-down conversion; turnovers;
  * drives; points per drive; scoring opportunities;
  * **empty-drive rate**: drives ending in punt, turnover, downs, or end of half without points;
  * three-and-outs: ≤ 3 plays and a punt;
  * red-zone trips and red-zone TD rate: a drive reaching ≤ 20 yards to goal;
  * success rate and rush / pass success rate.

## 4. Pre-registered primary tests (the formal family)

* **Common settings:**
  * historical games with a valid consensus spread (or total, for T8–T10);
  * OLS with an intercept and a neutral-site indicator;
  * standardised features;
  * **season-clustered bootstrap**: seed 20261011, 2,000 draws, resampling whole seasons;
  * p-value = 2 × the share of draws beyond 0;
  * **Benjamini–Hochberg across T1–T11**;
  * effect sizes and intervals lead the report; a p-value never decides alone.

| Id | Mechanism | Statistic | Supports the mechanism when |
|---|---|---|---|
| T1 | Rushing adds **late separation** | Coefficient on `rush_res_z` for the **2H** margin minus that for the **1H** margin, each from `margin_part ~ rush_res_z + pass_res_z + net.sustained_efficiency + exp_margin` | > 0 |
| T2 | Rushing adds **drive sustainability** | Coefficient on `rush_res_z` for the **empty-drive-rate differential** (same covariates) | < 0 (fewer own empty drives than the opponent) |
| T3 | **Rush defense** drives the residual | In `ats_resid ~ rush_def_diff_z + rush_off_diff_z + net.sustained_efficiency`: b_def − b_off | > 0 |
| T4 | Market **overweights passing** | Market weight − football weight on `pass_res_z`, from `exp_margin ~ X` and `margin ~ X`, X = [efficiency, pass_res_z, rush_res_z]. This equals −b in `ats_resid ~ X`. | > 0 |
| T5 | Market **underweights rushing** | The same for `rush_res_z` | < 0 |
| T6 | Passing is **more volatile** | SD(side ATS residual \| PRS) − SD(side ATS residual \| RRS) | > 0 |
| T7 | CONTROL magnitude **is priced** | Within CONTROL games, the slope of the CONTROL-side ATS residual on CONTROL magnitude | CI contains 0, and \|slope\| is small relative to the margin slope (reported) |
| T8 | **Pace absorbed** | Slope of `total_resid` on pace (with `def_quality_sum` and `off_quality_sum`) | CI contains 0 |
| T9 | **Defensive suppression absorbed** | Slope of `total_resid` on `def_quality_sum` (same model) | CI contains 0 |
| T10 | **Scoring environment absorbed** | Mean `total_resid`, ELEVATED minus SUPPRESSED claim | CI contains 0 |
| T11 | The close **absorbs** the rush signal better than the opener | For FRS sides, 2021+: mean residual vs the opener minus mean residual vs the close | > 0 |

**T12 — the 2026 shift (decision rule; no p-value).**
* Compare the 2026 frozen-feature distribution (mean, SD, share with |z| ≥ 1) with the same statistics in every
  historical season over the **same week window** and the same eligibility.
* **"Consistent with an early-season / composition effect"** requires both:
  * the 2026 SD is ≤ the largest historical matched-window SD, or within 10 % of it;
  * the 2026 mean is inside the historical matched-window range once FBS-vs-FCS games are separated.
* Otherwise: **"unexplained by early season."**
* **Source mismatch** is judged by §6 parity.

## 5. Pre-registered descriptive analyses (no p-values; effect sizes and intervals only)

1. **Rushing (M1):**
   * score timing (Q1, half, Q3, final, 2H, Q4) for FRS / RRS sides against matched controls (same side-spread bin
     and home / away, |z| < 0.5);
   * lead preservation at the half (leading / tied / trailing);
   * drive and box channels;
   * residual distributions for FRS, RRS, PRS, CONTROL and all-sides (mean, median, SD, p10/25/75/90, skew,
     kurtosis, P(residual ≥ +14), P(margin ≥ 21));
   * matched comparison: strata of efficiency quintile × side-spread bin × home / away, comparing the top and bottom
     `rush_res_z` terciles within a stratum.
2. **Passing (M2):**
   * the information-overlap matrix: raw and partial correlations, and incremental R² of the spread and of the margin
     for efficiency, pass, rush, pass / rush defense, CONTROL magnitude and spread;
   * **market-line decomposition** Models A–D, **walk-forward by season** (fit < s, predict s), for the spread and
     for the margin;
   * the **football-weight vs market-weight table** (spread panel: efficiency, pass_res, rush_res, rush_def_diff;
     total panel: pace, defensive suppression, scoring environment);
   * volatility (variance, upsets, tails, turnover sensitivity);
   * visibility: AP rank, points per game, prior margin, recent wins, pregame Elo against pass / rush quality, and
     whether the spread's pass weight shrinks once these proxies are added.
3. **CONTROL (M3):**
   * the tier table: win, spread, margin, residual mean / SD, and moneyline-implied win probability 2021+;
   * saturation curves by magnitude bin (win probability, market-expected margin, actual margin, residual);
   * loss modes and cover-failure modes, compared on post-game channels.
4. **2026 shift (M4):**
   * the distribution by week (2026 vs every historical season, matched windows);
   * FBS / FCS composition;
   * |z| against prior games and SE;
   * within-season feature stability (correlation of the week-k value with the team's last regular-season pregame
     value);
   * **source parity**:
     * the code-level success definitions;
     * identity / score parity on the 2026 games present in both sources;
     * league-level raw-rate distributions by source over the same weeks.
5. **STRONG CONTROL (M5):**
   * STRONG vs non-STRONG on visible proxies;
   * a logistic P(STRONG) from visible proxies (pregame Elo difference, AP ranks, scoring margin and record to date)
     with walk-forward AUC;
   * the correlation of magnitude with the spread;
   * a case table for every historical (70) and 2026 (5) skeptical-market row.
6. **Totals (M6–M8):** market-total slope vs actual-total slope by feature; decomposition into plays, points per
   play, points per drive, scoring opportunities and finishing.
7. **Market:**
   * opener vs close and line movement (2021+) for FRS, PRS and CONTROL (spread), and pace (total);
   * disagreement geometry: binned feature strength × side spread;
   * the market-as-benchmark walk-forward (football-only, market-only and football + market for the margin and the
     total).
8. **Matchup vs team strength:** offense, defense and the offense × opponent-defense-weakness interaction, for rush
   and pass.
9. **Reliability:**
   * season-to-season autocorrelation of each team's final pregame value;
   * odd/even split-half reliability of raw per-game rates, Spearman–Brown corrected.
10. **2026 consistency:** every signal against the DraftKings close and (spread only) the Kalshi center.
11. **The CFB_INFORMATION_ABSORPTION_MATRIX.**
12. **Case studies.** Deterministic rankings computed after membership is fixed:
    * the 5 largest positive and 5 largest negative FRS residuals;
    * the 5 most negative PRS residuals;
    * every STRONG skeptical row;
    * the 5 CONTROL wins with the most negative residual.

## 6. Negative controls

* **NC1.** Rush residual with a random sign per game (seed 20261011): the T5 statistic should be ≈ 0.
* **NC2.** Rush residual shuffled across games within season: ≈ 0.
* **NC3.** `net.finishing` and `net.disruption` (no Wave-1 spread residual), substituted into the T4/T5 model: ≈ 0.
* **NC4.** The frozen feature against the 1Q margin net of the spread share, as a timing placebo: reported, not judged.

## 7. Interpretation and confidence

* **Confidence grades:**

  | Grade | Requires |
  |---|---|
  | **HIGH** | Primary test q < 0.05; the same sign in ≥ 9 of 12 seasons; coherent secondary diagnostics; the negative controls null |
  | **MODERATE** | The estimate in the predicted direction, with a CI excluding 0 or q < 0.10, and partial secondary support |
  | **LOW** | Direction only; the CI includes 0 |
  | **UNRESOLVED** | Data unavailable, or contradictory evidence |

* **Graded at minimum:**
  * rushing adds late separation;
  * rushing adds drive sustainability;
  * rush defense drives the residual;
  * the market underweights rushing;
  * the market overweights passing;
  * passing is higher variance;
  * CONTROL magnitude is priced;
  * the market recognises STRONG CONTROL;
  * the 2026 shift is early season;
  * the 2026 shift is a source mismatch;
  * pace is absorbed;
  * defensive suppression is absorbed;
  * the scoring environment is absorbed.
* **Required answer.** "Should a Wave-2 rule change?" is answered **NO** unless an implementation or
  research-integrity defect is found.

## 8. Integrity tests (`tests/test_cfb_signal_mechanisms.py`)

* **Wave 2 / 2A unchanged:**
  * Wave-2 code and candidates are unchanged (pinned hashes);
  * the Wave-2A artifacts are untouched.
* **Imported, not restated:**
  * CONTROL comes from the production claim fields;
  * the rush constants are imported from `wave2` and hash-verified.
* **No leakage or contamination:**
  * no future game enters a feature;
  * no final score enters a market model;
  * the market spread is never a football input;
  * the early-season replay uses prior games only;
  * membership is unchanged when outcomes are doctored.
* **Orientation:**
  * opener / close orientation is correct;
  * the home / away sign is correct;
  * source-parity joins are stable.
* **Identity:** FBS / FCS identity is correct.
* **Isolation:** no prospective-ledger write.
* **Reproducibility:** a deterministic rerun gives the same artifact hash.
