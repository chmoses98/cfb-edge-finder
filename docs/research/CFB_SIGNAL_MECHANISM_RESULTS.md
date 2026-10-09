# Football Signal Discovery Lab — Wave 2C (CFB): signal-mechanism study — RESULTS

**Kind: EXPLANATORY, RETROSPECTIVE, DISCOVERY-CONTAMINATED.**

* No rule, filter, threshold, stake or recommendation is created.
* `CFB-PROS-001` / `CFB-PROS-002` and every Wave-2 file are untouched.
* Every historical number comes from the 2014–2025 data that Wave 1 used to discover these signals. Every 2026 number
  comes from the Wave-2A replay window.

| Item | Value |
|---|---|
| Protocol | `docs/research/CFB_SIGNAL_MECHANISM_PROTOCOL.md`, committed alone at **`202c9e91`** (2026-10-09T20:56:19Z), SHA-256 `0106a1fb…4c90` |
| Source main | `e1310b00de305aaddfe5ce62598fe1ea5e915ae9` |
| Artifact | `data/scripting/validation/signal_mechanisms/mechanism_report.json`, schema `cfb_signal_mechanisms/1.0.0`, SHA-256 `57167ae5…6ab9`; a rerun is byte-identical |
| Inputs | `games_2014_2025.jsonl.gz` (8,786 games); `games_2026.jsonl.gz` (397 games); `build_manifest.json` |
| Code | `src/cfb_edge_finder/signal_discovery/mechanisms.py`; `scripts/signal_mechanisms_cfb.py` (`build` → `analyze`); `scripts/signal_mechanisms_cfb_analyze.py`; `tests/test_cfb_signal_mechanisms.py` |
| Statistics | Season-clustered bootstrap (seed 20261011, 2,000 draws; OLS bootstrapped from per-season sufficient statistics); Benjamini–Hochberg over T1–T11 |

**Units and notation:**
* "Per SD" means per one 2014–2025 SD of the feature.
* **Residual** = margin + closing spread from the side's view; > 0 means the side beat the number.
* `rush_res` / `pass_res` = `net.rushing` / `net.passing` with overall efficiency regressed out (protocol §3).
* **FRS** = the frozen PROS-001 side.

## A. Verdict

* **Why rushing adds information.**
  * The run game, and especially **run defense**, is worth more on the scoreboard than its footprint in the visible
    results the market prices from.
  * Holding overall efficiency fixed, a team with a rushing edge:
    * keeps the ball longer (+78 s per SD);
    * runs more and throws less (opponents throw about 6 more passes);
    * **throws fewer interceptions relative to its opponent** (−0.13 per SD);
    * has slightly fewer empty drives.
  * The market moves the spread only about 1.3–1.7 points per SD for this, against 2.0–2.2 points of actual margin.
  * It is not a late-game effect: the edge accrues evenly by half.
* **Why passing shows the opposite residual.** It is mostly an **accounting mirror**, not a separate mispricing.
  * Overall efficiency is built from both pass and rush metrics. With efficiency held fixed, "more passing" means
    "less rushing": the two residuals correlate **−0.78**.
  * Put both in the same model and passing is priced almost exactly: market 1.44 vs football 1.47 points per SD;
    T4 = **+0.00** [−0.59, +0.53].
  * Rushing stays under-weighted: T5 = **−0.72** [−1.17, −0.33], 11/12 seasons, q < 0.001.
* **Why CONTROL predicts winners but not covers.** The market prices CONTROL magnitude almost one-for-one.
  * The spread moves 5.28 points per unit of magnitude; the actual margin moves 5.09.
  * Decile by decile, the market-expected margin tracks the actual margin from +7.6 to +31.7.
  * A strong team wins 90 % of the time, but it is also laid −23.5 on average, and its cover rate is a coin flip.
* **Why 2026 shifted.** Early-season FCS composition, not a source break.
  * FBS-vs-FBS games in 2026 have mean **+0.19** and SD **1.36**, inside the historical same-week ranges (0.02–0.20 and
    1.25–1.44).
  * The overall shift comes from the 9 % of rows involving an FCS team, whose values average **+4.2**.
  * The raw rush success-rate level matches across sources (ESPN 2026 0.412 vs CFBD 0.420).
* **Why Strong-Control skepticism is rare.** STRONG CONTROL is, almost by definition, the obviously better team.
  * Public proxies alone (Elo, record, scoring margin, AP rank) identify it out of sample with AUC **0.84**.
  * 97 % of STRONG games are favoured by 3 or more.
  * The 70 skeptical cases are early-season games (median week 4, median 3 prior games). In half of them the
    preseason-informed Elo rating disagrees with the in-season efficiency read, against 4 % of STRONG games overall.

## B. Repo / branch / PR / SHAs

* **Repository:** `chmoses98/cfb-edge-finder`.
* **Branch:** `claude/rb-receptions-mechanism-y8lhv7`. This is the session-assigned branch, used instead of the
  suggested `research/cfb-signal-mechanisms`.
* **Base:** `e1310b00`.
* **Commits, in order:**
  1. **`202c9e91` — protocol only, before any mechanism output.**
  2. `ddf9db66` — code, study inputs and artifact.
  3. Later commits — tests, results, future hypotheses.
* **PR:** "Research: CFB signal-mechanism study", merged with a merge commit.

## C. Research integrity

* **Wave 2 unchanged.** These are pinned by SHA-256 in `tests/test_cfb_signal_mechanisms.py`:
  * `wave2.py`, `wave2_cycle.py`, `wave2a/replay.py`, `wave2a/history.py`, `signal_lab_wave2a_cfb.py`;
  * the Wave-2 and 2A protocols;
  * the candidates file;
  * all 7 Wave-2A artifacts.

  `W.load_candidates` still validates `bf19c972…`. There is no write path into `research-signals` or a Wave-2
  directory (tested).
* **Anchor.** The build reproduces the Wave-1 join (8,786 rows) and PROS-001 (2,713, +1.4003870, 1,408–1,260–45)
  exactly. It refuses to run otherwise.
* **2026 features.** All **397/397** frozen-feature values, rebuilt with the Wave-2 `feature_for` on the
  Wave-2A-pinned log (`8173fd5e`), equal the committed Wave-2A membership. CONTROL matches 89/89.
* **Walls:**
  * no row's feature history reaches its own kickoff (tested on all rows);
  * the constructs read no market or outcome field (tested by doctoring);
  * the market models are unchanged when final scores are doctored;
  * membership is unchanged when outcomes are doctored;
  * the visible-proxy builder uses strictly prior games (tested).

## D. Data / source audit

| | Historical 2014–2025 | 2026 |
|---|---|---|
| Features | Wave-1 feature tables (CFBD-derived `TeamGame`, production builder, 04:00 ET cutoff) | the same builder on the ESPN log (`espn.summary.boxscore` + `espn.summary.drives`) |
| Success definition | CFBD advanced `successRate` × plays, garbage time excluded | production `espn._successful()` on ESPN play logs |
| Lines | CFBD consensus close (8,776); opener 2021+ (3,777 spread, 3,782 total); moneyline 2021+ | Kalshi PRIMARY_60_180 (66 PROS-001, 45 STRONG); DraftKings via ESPN sidecar, last pre-kick fetch (251 games) |
| Game channels | quarter scores; box (possession, first downs, third downs, turnovers, INT, fumbles, pass / rush attempts); drives (empty, three-and-out, red zone) | box + play-log totals (no quarter scores) |
| Visible proxies | CFBD pregame Elo, AP poll by week, record / margin / points to date | — |
| Unavailable | explosive counts, QB identity, injuries, short-yardage / goal-to-go | 2026 CFBD box / advanced (the cache holds 8 completed games); ESPN API (not reachable here) |

**Source mismatch.** The two sources use different success definitions, and the same game was never available from
both sources for features. The only direct overlap is identity and score: 522/522 ids orient identically, and 8/8
completed scores match.

## E. Rushing mechanism summary

* **T5:** the market weights `rush_res` **−0.72** points per SD below its football value [−1.17, −0.33], 11 of 12
  seasons, q < 0.001.
* **Matched comparison** (efficiency quintile × spread bin × site): the top `rush_res` tercile beats the bottom by
  **+1.34** ATS points.
* **The source is rush defense** (I).
* **The channel is ball control and interceptions** (F).
* **The timing is even across halves** (G).
* **Negative controls:**
  * a sign-flipped rush residual gives −0.02 [−0.32, +0.29];
  * a within-season shuffle gives +0.07 [−0.25, +0.37];
  * `net.finishing` gives −0.31 (CI includes 0);
  * `net.disruption` gives +0.11 (CI includes 0).

  All are null against the +0.72.

## F. Rushing drive sustainability

Coefficients per SD of `rush_res`, beyond efficiency and the spread (home − away):

| Channel | Coefficient |
|---|---|
| Possession time | **+78 s** |
| Rush attempts | **+6.1** |
| Pass attempts | **−6.3** |
| Empty-drive rate | **−0.0076** (T2, [−0.0136, −0.0014], 10/12 seasons, q 0.052) |
| Points per drive | +0.05 |
| Red-zone trips | +0.08 |
| Three-and-out rate | +0.002 |
| First downs | −0.29 (fewer plays) |
| **Turnover differential** | **−0.12** |
| Interceptions | **−0.13** (POST_HOC decomposition) |
| Fumbles | +0.01 |

* **Mediation (descriptive, post-game mediators).** The ATS coefficient on `rush_res` falls from 0.74 to **0.07**
  once the turnover differential is added. Possession absorbs part of it (→ −0.32), and drive quality over-absorbs
  it.
* **Reading.** The rushing edge pays through ball security, fewer interceptions, and more of the clock. Drive
  sustainability is real but small.

## G. Rushing score timing / late-game effect

* **T1 = −0.07** [−0.50, +0.40]: there is **no late separation**.
* **Per-quarter coefficients on `rush_res`:**

  | Q1 | Q2 | Q3 | Q4 |
  |---|---|---|---|
  | +0.20 | +0.22 | +0.30 | +0.05 |

* **FRS sides against spread- and site-matched controls:**

  | Half | Edge over controls |
  |---|---|
  | 1H | **+1.29** [0.81, 1.77] |
  | 2H | +1.09 [0.66, 1.49] |
  | Q4 alone | +0.34 |

* **Lead preservation** (POST_HOC spread-, site- and halftime-matched refinement). FRS sides leading at the half:
  * second-half margin **+1.44** vs matched controls;
  * win +1.2 pp;
  * comeback against −1.2 pp.

  Tied / trailing sides add +0.8 in the second half. That is the same per-half rate: steady accumulation, not a
  closing effect.

## H. Rushing margin distribution

| Group | n | Mean residual | Median | SD | p10 / p90 | Skew | P(residual ≥ +14) | P(residual ≤ −14) |
|---|---|---|---|---|---|---|---|---|
| FRS (frozen) | 2,713 | **+1.40** | +1.0 | 15.85 | −18 / +22 | −0.01 | 21.5 % | 15.9 % |
| RRS (rush residual) | 2,747 | +1.18 | +0.5 | 15.90 | −18.5 / +21.5 | −0.05 | 21.3 % | 16.3 % |
| PRS (pass residual) | 2,717 | −0.55 | −0.5 | 15.92 | −21 / +19.5 | +0.07 | 17.4 % | 20.1 % |
| CONTROL | 4,207 | +0.52 | 0.0 | 15.81 | −19.2 / +21 | −0.03 | 20.7 % | 17.6 % |
| All home sides | 8,776 | −0.22 | −0.5 | 15.64 | −20 / +19.9 | +0.01 | 18.4 % | 18.9 % |

**Reading.** The rushing effect is a **location shift**: both tails move, and the SD and kurtosis are unchanged. It
is not a right-tail effect.

## I. Rush offense vs rush defense

* **T3** (b_def − b_off on the ATS residual): **+0.61** [+0.26, +1.00], 10/12 seasons, q 0.004.
* **Full matchup model:**

  | Component | ATS residual | Football | Market |
  |---|---|---|---|
  | Rush defense | **+1.18** [0.85, 1.55] | +0.55 | **−0.63** |
  | Rush offense | +0.57 [0.24, 0.89] | — | — |

* **The paradox.** Conditional on efficiency, a better rush defense *raises* the actual margin (+0.55) while the
  market moves the line *against* it (−0.63).
* **Turnovers absorb about 28 %** of the rush-defense coefficient (1.18 → 0.85).
* **Answer:** the residual is driven more by **rush defense**.

## J. Rushing matched / residualised analysis

* **Within-stratum** top − bottom `rush_res` tercile: +1.34 (106 strata).
* **Regression residualised on efficiency, pass and the spread:** +0.76 points of margin per SD beyond the spread.
* **Walk-forward market + `rush_res`:**
  * RMSE improves by **0.009** points over market-only (15.584 → 15.575);
  * for the frozen rush-defense feature, by 0.015.
* **Reading.** The residual is stable, but far too small per game to matter for a forecaster.

## K. Passing mechanism summary

* **Wave 1's −1.24 per SD** came from a model containing efficiency and passing only.
* **This study's model B** (efficiency + passing): market +0.41 vs football −0.15, so the residual is −0.56.
* **Model D** (all dimensions): market **1.43** vs football **1.41**, so the residual is ≈ **0**.
* **PRS sides** cover 48.8 % (−0.55), and the line does not move against them from open to close (−0.08).
* **Within strata,** the top − bottom `pass_res` tercile is −1.36: the mirror of rushing's +1.34.
* **Mechanism:**
  * **C (collinearity / decomposition): supported.**
  * B (market expectation asymmetry): not needed.
  * A (football asymmetry): partly supported, since rushing has extra football value.
  * D (opponent-adjustment artefact), E (data quality): not supported (§AG, reliability §AH).

## L. Passing vs rushing information overlap

* **Correlations** (all games with a spread):
  * `pass_res` vs `rush_res` **−0.78**;
  * `net.passing` vs efficiency 0.89, `net.rushing` vs efficiency 0.85;
  * `net.passing` vs the spread 0.75, `net.rushing` vs the spread 0.72.
* **Partial correlation with the spread given efficiency:**
  * `pass_res` +0.05, `rush_res` +0.03;
  * with the margin: `pass_res` −0.01, `rush_res` **+0.055**.
* **Incremental R² over efficiency:**

  | Feature | Spread | Margin |
  |---|---|---|
  | `pass_res` | 0.0008 | 0.0001 |
  | `rush_res` | 0.0003 | **0.0020** |
  | `pass_def` | 0.0029 | 0.0030 |
  | `rush_def` | 0.0013 | 0.0001 |

* **Answer.** The spread tracks the passing-flavoured part of the profile slightly more than the rushing part. The
  margin does the opposite.

## M. Market-line decomposition

Walk-forward by season (fit < s, predict s); coefficients are points per SD:

| Model | Market (spread) coefficients | Football (margin) coefficients | OOS R² spread / margin |
|---|---|---|---|
| A: efficiency | 11.79 | 12.08 | 0.692 / 0.325 |
| B: + pass | eff 11.79, pass **+0.41** | eff 12.08, pass **−0.15** | 0.693 / 0.325 |
| C: + rush | eff 11.79, rush **+0.24** | eff 12.08, rush **+0.96** | 0.692 / 0.327 |
| D: + pass, rush, pace, defense, offense env | pass 1.43, rush **1.34**, defsupp 0.69 | pass 1.41, rush **2.05**, defsupp 0.89 | 0.699 / 0.329 |

## N. Football weight vs market weight

Spread panel: efficiency, pass_res, rush_res and rush-defense difference together. Total panel: pace, defensive
suppression and offense environment together. Season-bootstrap 95 % intervals.

| Feature | Actual-margin effect | Market-spread effect | Residual [95 %] | Interpretation |
|---|---|---|---|---|
| Overall efficiency | +12.21 | +12.35 | −0.14 [−0.60, +0.25] | priced |
| Passing residual | +1.47 | +1.44 | +0.03 [−0.50, +0.60] | priced |
| Rushing residual | +2.17 | +1.66 | **+0.52** [+0.11, +0.95], 10/12 | under-weighted |
| Rush-defense difference | −0.22 | **−0.91** | **+0.69** [+0.38, +1.03], 10/12 | the market leans the wrong way |
| Pace (total) | +2.07 | +1.86 | +0.21 [−0.28, +0.68] | absorbed |
| Defensive suppression (total; + = better defenses) | −4.91 | −5.30 | +0.39 [−0.06, +0.84] | absorbed; the market slightly over-adjusts |
| Scoring environment (total; offense quality) | +5.33 | +5.90 | −0.56 [−1.30, +0.09] | absorbed; the market slightly over-adjusts |

## O. Passing volatility

* **T6** (SD of the PRS residual − SD of the RRS residual): **+0.01** [−0.45, +0.53]. Passing is **not** more
  volatile.

| | Margin SD | P(residual ≥ +21) / ≤ −21 | Upset rate as favourite | Corr(residual, turnover margin) | Residual SD net of turnovers |
|---|---|---|---|---|---|
| PRS | 22.1 | 8.7 % / 10.3 % | 26.4 % | −0.52 | 13.61 |
| RRS | 21.9 | 10.7 % / 8.1 % | 24.3 % | −0.52 | 13.63 |

## P. Market visibility / salience

* **Correlations with public proxies.** Efficiency correlates 0.78 with the Elo difference and 0.80 with the
  scoring-margin difference to date. The pass / rush residuals correlate with every proxy at |r| ≤ 0.15. The
  residuals are, by construction, the part public results do not show.
* **Adding the proxies to the spread model** shrinks the spread's weights:
  * on `pass_res`, 1.22 → 0.30;
  * on `rush_res`, 1.18 → 0.19.
* **The margin's weights shrink less** for rushing: `pass_res` 1.12 → 0.20, `rush_res` 1.84 → **0.85**.
* **Reading:**
  * The market prices both residuals mainly through their visible footprint (Elo / record).
  * Rushing carries margin value *beyond* that footprint.
  * This is **consistent with** lower visibility of rushing and run-defense value. It does not show what bettors
    think.
* **POST_HOC:** with everything else held fixed, the win-percentage difference to date carries a residual weight of
  −0.46. The market leans on record. Recorded as a future hypothesis only.

## Q. CONTROL win-probability mechanism

| Tier | n | Win | Mean side spread | Mean margin | Mean residual | Residual SD | Cover | ML-implied win (2021+) | Actual win (2021+) |
|---|---|---|---|---|---|---|---|---|---|
| HOME MODERATE | 916 | 79.7 % | −11.9 | +12.8 | +0.93 | 15.7 | 52.2 % | 75.6 % | 80.2 % |
| AWAY MODERATE | 918 | 67.3 % | −6.9 | +7.3 | +0.45 | 15.9 | 50.7 % | 65.9 % | 66.4 % |
| HOME STRONG | 1,443 | 90.6 % | −23.5 | +23.4 | −0.13 | 15.4 | 47.7 % | 85.0 % | 87.6 % |
| AWAY STRONG | 930 | 83.1 % | −15.2 | +16.4 | +1.18 | 16.5 | 54.0 % | 78.9 % | 80.4 % |

**Saturation (deciles of CONTROL magnitude):**

| Measure | Lowest decile | Highest decile |
|---|---|---|
| Win probability | 69 % | 95 % |
| Market-expected margin | +7.6 | +31.7 |
| Actual margin | +8.4 | +31.8 |
| Residual | — | stays within ±1.4 in every decile |

**Slopes per unit of magnitude:**
* win +0.052;
* market margin **+5.28**;
* actual margin **+5.09**;
* residual **−0.18** [−0.43, +0.08] (T7).

**Answer.** CONTROL magnitude moves win probability *and* the market number together. The win-probability gain is
real, but it is already in the price (moneylines show a +2–4 pp edge before vig, consistent with Wave 1's no-ROI
result).

## R. CONTROL vs market spread

* **Correlation.** CONTROL magnitude correlates 0.67 with the market-expected margin (0.58 within STRONG).
* **The close moves toward the CONTROL side** (2021+): by +0.48 points (toward 53.4 % / away 34.1 %).
* **Residual vs opener** +0.96 (51.8 %), **vs close** +0.48 (50.6 %). The market absorbs part of CONTROL during the
  week.
* **Market skepticism** (the CONTROL side not favoured by 3 or more) is concentrated in **away** CONTROL: 11.6 %
  vs 2.6 % at home.

## S. CONTROL cover-failure modes (CONTROL wins that did not cover, n 1,274)

| | Wins that covered | Wins that failed to cover |
|---|---|---|
| Mean side spread | −15.4 | **−20.1** |
| 1H margin | +16.6 | +6.6 |
| Margin through Q3 | +23.7 | +9.8 |
| Q4 margin | +4.8 | +1.7 |
| Turnover margin | +1.11 | +0.21 |
| Possession | +205 s | +64 s |
| Opponent Q4 points | 4.0 | 6.3 |

* **Never separated:** 64.5 % led by less than half the spread at the half.
* **Garbage-time give-back** (covered through Q3, then lost it in Q4): only 11.5 %.
* **The typical failure** is a big number that the team never gets near. Neutral turnovers and a still-efficient
  opponent are the common features; a late collapse is rare.

## T. CONTROL loss modes (CONTROL losses, n 779)

| | Value |
|---|---|
| Turnover margin | **−1.23** |
| Possession | −187 s |
| Success-rate difference | −0.015 |
| Yards-per-play difference | −0.34 |
| 1H margin | −5.0 |
| 2H margin | −4.8 |
| Opponent points per drive | 2.45 (vs 1.12 when CONTROL wins and covers) |
| Mean side spread | −8.4 |

**What breaks CONTROL is turnovers.** CONTROL teams that lose do not lose the efficiency battle by much. They lose
the turnover battle by more than one per game, and the possession that comes with it. Losses also cluster where the
market expected less (−8.4 vs −17).

## U. STRONG CONTROL market recognition

* **Walk-forward AUC** for P(STRONG) from visible proxies (|Elo difference|, |scoring margin to date|,
  |win-% difference|, ranked teams): **0.84**. That is identical to the AUC from |spread| alone (0.84).
* **STRONG profile vs no-CONTROL:**

  | | STRONG | No CONTROL |
  |---|---|---|
  | \|Elo difference\| | 394 | 165 |
  | \|scoring margin to date\| | 24.6 | 11.0 |
  | \|spread\| | 19.1 | 7.6 |

* **Market recognition:**
  * 97.1 % of STRONG games have the CONTROL side favoured by 3 or more;
  * median market margin +19.5;
  * Kalshi 2026: 40 of 45, median +21; corr(magnitude, Kalshi implied) 0.71.
* **Answer.** STRONG CONTROL is largely synonymous with "the obviously better team". Disagreement with the market is
  rare because the market sees the same thing.

## V. STRONG CONTROL skeptical-market cases

**Historical (n 70; the full list is in the artifact, `strong_recognition.skeptical_cases_historical`):**
* ATS 42–27–1, mean +5.25.
* Timing: median week **4** (all STRONG: week 8); median prior games of the CONTROL side **3**.
* 61 % away (all STRONG: 39 %); the CONTROL side is the underdog in 42.
* **Elo disagrees with CONTROL in 34 of 65** with an Elo, against 3.7 % of all STRONG games.
* 5 neutral-site; 7 involve an FCS team.

**2026 Kalshi (5):**

| Game | Implied margin | Margin | Residual |
|---|---|---|---|
| Rutgers @ Boston College | +2.9 | +7 | +4.1 |
| LSU @ Ole Miss | +2.8 | −8 | −10.8 |
| Oklahoma State @ West Virginia | −1.7 | +17 | +18.7 |
| Virginia @ Florida State | +1.1 | −31 | −32.1 |
| Akron @ Central Michigan | −6.9 | −24 | −17.1 |

**Reading:**
* These are **"early-season, prior beats in-season data"** games. The market and Elo, both built with preseason and
  prior-season information, disagree with three or four games of in-season efficiency.
* Whether the market "knows something" (QB, injury, schedule) cannot be checked: historical QB and injury data are
  unavailable.
* POST_HOC: the Elo-disagree subset averaged +7.2 vs +4.8. That is not a rule; it is recorded as a future
  hypothesis.

## W. 2026 feature-distribution shift

| Population (weeks 1–6) | n | Mean | SD | Share \|z\| ≥ 1 |
|---|---|---|---|---|
| 2026 all | 251 | **+0.54** | **1.77** | 38.6 % |
| 2026 FBS vs FBS | 229 | **+0.19** | **1.36** | 32.8 % |
| 2026 FCS involved | 22 (8.8 %) | +4.23 | 1.20 | 100 % |
| Historical same window, all (12 seasons) | — | range +0.13 … +0.71 | range 1.34 … 1.89 | 30–37 % |
| Historical same window, FBS only | — | range +0.02 … +0.20 | range 1.25 … 1.44 | 26–33 % |
| Historical full season | — | +0.07 … +0.23 | 1.34 … 1.58 | 29–33 % |

* **2026 by week:**

  | Week | 2 | 3 | 4 | 5 |
  |---|---|---|---|---|
  | Mean | +0.78 | +0.69 | +0.48 | +0.18 |
  | SD | 2.07 | 1.84 | 1.67 | 1.57 |

  The 2026 distribution narrows week by week toward the full-season shape.
* **T12 decision rule:**
  * the SD is within the historical window maximum (1.77 ≤ 1.89 × 1.1);
  * the FBS-only mean is inside the historical range.

  **Verdict: `CONSISTENT_WITH_EARLY_SEASON_COMPOSITION`.** Wave 2A compared early 2026 against full historical
  seasons, which is not a like-for-like comparison.

## X. Source-parity test

* **Identity:**
  * 522/522 overlapping game ids orient identically (home/away, neutral);
  * 8/8 games completed in both sources have identical scores.
* **Raw level** (FBS-vs-FBS team-game rush success rate, weeks 1–6):

  | Source | Mean | SD |
  |---|---|---|
  | ESPN 2026 | 0.412 | 0.121 |
  | CFBD 2023 | 0.420 | 0.119 |
  | CFBD 2024 | 0.420 | 0.122 |
  | CFBD 2025 | 0.420 | 0.117 |

  A ≈ 0.008 lower level, with matching dispersion.
* **The frozen feature is a z-score difference standardised within season.** A level offset of that size cancels;
  an FBS-only distribution that matches history says the same.
* **Limitation.** No game is available from both sources for features (the ESPN API is unreachable here, and the
  2026 CFBD cache predates the season), so a game-by-game parity test was **not possible**.
* **Conclusion:** no evidence of a source-contract break; the shift is explained without one.

## Y. Early-season reliability

* **Correlation of a team's pregame value at week k with its final regular-season pregame value** (historical):

  | Metric | Week 2 | Week 3 | Week 4 | Week 5 | Week 6 | Week 10 |
  |---|---|---|---|---|---|---|
  | Rush defense | 0.46 | 0.58 | 0.68 | 0.75 | 0.80 | 0.92 |
  | Pass defense | 0.45 | 0.60 | 0.68 | 0.73 | 0.80 | — |
  | Offense success | 0.53 | 0.64 | 0.75 | 0.81 | — | — |
  | Pace | 0.45 | 0.60 | 0.67 | 0.75 | — | — |

* **Qualification by week** (|z| ≥ 1): week 2 38 %, 3 36 %, 4 31 %, 5 30 %; 28–33 % later. Mean |z|: 0.98 in week 2,
  falling to about 0.78.
* **Is the 2026 distribution normal for early October?** **Yes**, for the weeks that make it up (1–5).

## Z. FBS/FCS effect

* **FCS-involved games carry extreme values:**
  * mean defdiff +1.8 to +5.1 historically and +4.2 in 2026;
  * 65–100 % qualify;
  * 4–12 % of early-window rows historically, 8.8 % in 2026.
* They inflate the mean and SD of the pooled distribution in every season's early window, not just 2026.
* Residual performance in FCS rows was not optimised or split; any exclusion idea is a future hypothesis only.

## AA. Feature uncertainty

* **Mean |z| by the fewer prior games of the two teams:**

  | Prior games | ≤ 2 | 3–4 | 5–6 | ≥ 7 |
  |---|---|---|---|---|
  | Historical window | 0.92 | 0.76 | 0.81 | 0.69 |
  | 2026 | **1.15** | 0.77 | — | — |

* The rush-defense SE (success-rate SE as a proxy) falls from 0.035 to 0.019 over the same range.
* **Answer: yes, extreme early-season values are less reliable.** Standardisation by the full-history SD does not
  account for the larger SE with ≤ 2 games. Nothing changes in Wave 2; this is a future-hypothesis item.

## AB. Pace mechanism

* **Per SD of the possession environment:**

  | Channel | Effect |
  |---|---|
  | Plays | **+7.3** |
  | Drives | **+1.25** |
  | Points per drive | −0.03 |
  | Points per play | −0.01 |
  | Actual total | **+2.07** |
  | Market total | **+1.86** |
  | Residual (T8) | +0.21 [−0.28, +0.68] |

* **By quintile:** the market total runs 49.4 → 61.8 and the actual 49.6 → 61.8. Residuals are 0.1–1.2 with no
  trend.
* **Opener vs close:** the line moves +0.23 per SD toward pace during the week. The close residual (−0.32 per SD) is
  slightly below the opener residual (−0.09).
* **Answers:**
  1. Pace moves the total about 1.9 points per SD.
  2. It moves the actual total about 2.1 points per SD.
  3. They are similar.
  4. There is no reliable over- or under-weighting at the extremes.
  5. **Pace works through possessions, not efficiency.**

## AC. Defensive suppression mechanism

* **Per SD of defense quality** (both defenses better):

  | Channel | Effect |
  |---|---|
  | Scoring opportunities | **−0.51** |
  | Yards per play | **−0.32** |
  | Points per drive | **−0.23** |
  | Red-zone TD rate | −0.03 |
  | Success rate | −0.02 |
  | Drives | +0.34 |
  | Plays | +0.19 |

* **The mechanism:** fewer successful and explosive drives reach scoring range, while possessions slightly
  *increase*. It is not slower pace.
* **Totals:** market −5.30 vs actual −4.91 (T9 residual +0.39, CI includes 0).
* **The production claim** (n 533): market 44.8, actual 45.8, residual +1.0.
* **Which components are priced?** The volume and efficiency of the suppression, essentially fully. The market
  slightly over-shoots it.

## AD. Scoring environment mechanism

| Feature | Expected football effect | Market adjustment | Residual remaining |
|---|---|---|---|
| Offense-quality sum (per SD) | +5.33 | +5.90 | −0.56 [−1.30, +0.09] |
| `scoring_env_claim` ELEVATED (n 1,731) | actual 63.9 | market 64.25 | −0.31 |
| `scoring_env_claim` SUPPRESSED (n 1,484) | actual 46.7 | market 46.06 | +0.65 |

* **T10** (ELEVATED − SUPPRESSED): −0.96 [−2.01, +0.12], q 0.17.
* **Reading:** absorbed, with a slight tendency to over-adjust both ways. That is not established.

## AE. Opening vs closing market absorption (2021+)

| Signal | n | Residual vs opener (cover) | Residual vs close (cover) | Line move toward side | Share toward / away |
|---|---|---|---|---|---|
| FRS rush edge | 1,141 | +1.74 (53.4 %) | +1.41 (52.4 %) | **+0.33** | 51.7 % / 35.7 % |
| RRS | 1,184 | +1.19 (53.3 %) | +1.01 (52.8 %) | +0.18 | 48.3 % / 38.9 % |
| PRS | 1,128 | −0.93 (49.5 %) | −0.85 (49.3 %) | −0.08 | 40.0 % / 46.5 % |
| CONTROL | 1,770 | +0.96 (51.8 %) | +0.48 (50.6 %) | **+0.48** | 53.4 % / 34.1 % |

* **T11** (open − close residual, FRS): **+0.33** [+0.27, +0.40], 5/5 seasons, q < 0.001.
* **Answer.** The close absorbs part of the rush and CONTROL signals during the week: about a fifth of the rush
  residual, and half of CONTROL's. Most of the rush residual survives to the close.

## AF. Line movement

* **Per SD (home perspective, close − open toward home):**

  | Feature | Move |
  |---|---|
  | Efficiency | +0.38 |
  | `rush_res` | +0.13 |
  | `pass_res` | +0.05 |

* **Residual coefficient, opener → close:**

  | Feature | vs opener | vs close |
  |---|---|---|
  | `rush_res` | +0.45 | +0.32 |
  | `pass_res` | −0.40 | −0.45 |
  | Efficiency | +0.69 | +0.32 |

* **Reading.** The market learns efficiency and some rushing during the week; it does not move on passing. The
  rushing under-weight is present at the open and remains through the close.

## AG. Matchup vs team-strength decomposition

* **Rush:**
  * offense +0.57, defense **+1.18**, interaction **−0.03** [−0.30, +0.19] on the ATS residual.
* **Pass:**
  * offense −0.47, defense −0.74, interaction −0.27 [−0.66, +0.09].
  * These conditional signs mirror the rush ones, because efficiency is held fixed.
* **Answer.** The rushing residual is **team quality (above all rush defense)**, not a matchup-specific interaction.
  The market does not appear to miss matchup interactions.

## AH. Information absorption matrix (`CFB_INFORMATION_ABSORPTION_MATRIX`)

| Signal | Football predictive? | Football effect | Market response | Residual | Historical stability | 2026 behaviour | Mechanism confidence |
|---|---|---|---|---|---|---|---|
| CONTROL | Yes (wins 67–91 %) | margin +5.09 per unit of magnitude | +5.28 per unit | −0.18 [−0.43, +0.08] | priced every season (T7 3+/9−) | Kalshi agrees (40/45 ≥ +3); DraftKings CONTROL sides −1.05 (n 38) | **HIGH** (priced) |
| Rush edge (`rush_res`) | Yes | +2.17 / SD | +1.66 / SD | **+0.52** [+0.11, +0.95] | 10/12 seasons | DraftKings residual weight +0.63 (n 120); RRS 26–20 +2.6; Kalshi PROS-001 +0.37 (n 66) | **MODERATE** |
| Rush defense (frozen) | weak alone (−0.22 given the rest) | — | **−0.91 / SD** | **+0.69** [+0.38, +1.03] | 10/12 | DraftKings FRS 25–27, +0.01 (n 52) | **MODERATE** |
| Pass edge (`pass_res`) | Yes | +1.47 / SD | +1.44 / SD | +0.03 [−0.50, +0.60] | — | DraftKings residual weight −1.55 (n 120, noisy) | **HIGH** that it is priced once rushing is in the model |
| Pace | Yes (plays +7.3 / SD) | total +2.07 | +1.86 | +0.21 [−0.28, +0.68] | 8/12 | DraftKings n 120: noisy | **HIGH** (absorbed) |
| Defensive suppression | Yes | total −4.91 | −5.30 | +0.39 [−0.06, +0.84] | 9/12 | n 120: noisy | **MODERATE** (absorbed; slight over-adjustment) |
| Scoring environment | Yes | total +5.33 | +5.90 | −0.56 [−1.30, +0.09] | 4/8 | n 120: noisy | **MODERATE** (absorbed; slight over-adjustment) |
| Disruption | weak | — | — | +0.11 [−0.30, +0.55] (negative control) | — | — | **HIGH** (no residual) |
| Closeness | V2 relationship | — | — | Wave 1: underdog ATS 51.2 % (priced) | — | — | **MODERATE** (priced; not re-estimated) |

## AI. Case studies (deterministic rankings after membership was fixed; not evidence by themselves)

* **A. Largest positive FRS residuals:**
  * UNLV–Idaho State 2015 (+59; +6 turnovers);
  * Penn State @ Maryland 2019 (+52.5);
  * Oklahoma @ Kansas State 2015 (+51);
  * Clemson @ Miami 2015 (+50);
  * Michigan @ Rutgers 2016 (+48).

  All are blowouts already decided at the half (+35 to +44).
* **B. Largest negative FRS residuals:**
  * Liberty–New Mexico State 2022 (−59);
  * North Texas–Portland State 2015 (−57.5, FCS);
  * Ohio State @ Iowa 2017 (−52; −4 turnovers);
  * Washington State @ California 2017 (−50.5; −7 turnovers);
  * Rice–Charlotte 2022 (−48).

  These are turnover disasters and FCS upsets.
* **C. Pass-edge sides the market over-priced:**
  * UTSA–UTEP 2014, Rice–Charlotte 2022, Washington State–Arizona 2023, Ole Miss–Mississippi State 2016,
    Marshall–Akron 2016.
  * All lost by 27–38 as favourites. Each has `pass_res` ≥ +1.1 and `rush_res` ≤ −0.4, so "pass edge" is a
    "rush deficit".
* **D. STRONG skeptical cases:** see §V and the artifact (all 70).
* **E. CONTROL wins that badly failed to cover:**
  * TCU–Kansas 2015 (−46, won by 6);
  * Ole Miss–Washington State 2025 (−33, by 3);
  * Oregon–San José State 2018 (−42.75, by 13);
  * Wisconsin–Georgia State 2016 (−35.5, by 6);
  * Wyoming @ UConn 2021 (−31.5, by 2).

  All are very large numbers on games that never separated.

## AJ. Negative controls

| Control | Estimate [95 %] | Expected |
|---|---|---|
| NC1: random-sign rush residual | −0.02 [−0.32, +0.29] | ≈ 0 ✓ |
| NC2: within-season shuffle | +0.07 [−0.25, +0.37] | ≈ 0 ✓ |
| NC3: `net.finishing` | −0.31 [−0.93, +0.26] | ≈ 0 ✓ |
| NC3: `net.disruption` | +0.11 [−0.30, +0.55] | ≈ 0 ✓ |
| NC4: Q1 timing placebo (rush) | +0.20 per SD in Q1 | reported: rushing accrues from the first quarter |

* The real `rush_res` statistic is +0.72; every negative control is null.
* Walk-forward benchmark: the negative-control features *worsen* market-only RMSE (−0.007 / −0.0065), while
  `rush_res` / rush defense improve it (+0.009 / +0.015).

## AK. Formal tests / FDR

| Test | Estimate | 95 % CI | Seasons + / − | BH q | Result |
|---|---|---|---|---|---|
| T1 late separation (2H − 1H) | −0.07 | [−0.50, +0.40] | 7 / 5 | 0.93 | not supported |
| T2 drive sustainability (empty-drive rate) | −0.0076 | [−0.0136, −0.0014] | 2 / 10 | 0.052 | supported, small |
| T3 rush defense − rush offense | +0.61 | [+0.26, +1.00] | 10 / 2 | 0.004 | supported |
| T4 market overweights passing | +0.00 | [−0.59, +0.53] | 4 / 8 | 0.98 | **not supported** (collinearity) |
| T5 market underweights rushing | −0.72 | [−1.17, −0.33] | 1 / 11 | < 0.001 | supported |
| T6 passing more volatile | +0.01 | [−0.45, +0.53] | 4 / 8 | 0.98 | not supported |
| T7 CONTROL magnitude priced | −0.18 | [−0.43, +0.08] | 3 / 9 | 0.26 | supported (priced; the margin slope is +5.09) |
| T8 pace absorbed | +0.21 | [−0.28, +0.68] | 8 / 4 | 0.53 | supported (absorbed) |
| T9 defensive suppression absorbed | +0.39 | [−0.06, +0.84] | 9 / 3 | 0.19 | supported (absorbed) |
| T10 scoring environment absorbed | −0.96 | [−2.01, +0.12] | 4 / 8 | 0.17 | supported (absorbed; slight lean) |
| T11 close absorbs the rush signal | +0.33 | [+0.27, +0.40] | 5 / 0 | < 0.001 | supported (partly) |
| T12 2026 shift (rule) | — | — | — | — | CONSISTENT_WITH_EARLY_SEASON_COMPOSITION |

## AL. Mechanism scorecard

| Mechanism | Evidence for | Evidence against | Confidence | Conclusion |
|---|---|---|---|---|
| Rushing improves drive sustainability | T2 −0.0076 per SD (10/12); possession +78 s; red-zone trips +0.08 | Small; three-and-outs and red-zone TD rate flat | **MODERATE** | Real but small; the larger channel is interceptions and possession |
| Rushing improves late-game separation | Matched leading sides +1.44 in the 2H | T1 −0.07; per-quarter edge even (Q4 +0.05); 1H ≥ 2H | **LOW** | Not a late-game effect: steady accumulation |
| Rush defense drives the residual | T3 +0.61 (q 0.004); rush defense +1.18 vs offense +0.57; market leans the wrong way (−0.91) | Rush offense also positive | **HIGH** | Yes |
| Market underweights rushing | T5 −0.72 (q < 0.001, 11/12); within-stratum +1.34; NCs null | Tiny forecasting gain (RMSE +0.009); 2026 Kalshi flat | **HIGH** (the point weight) / LOW (economic) | Yes, by about 0.5–0.7 points per SD |
| Market overweights passing | Wave 1 −1.24 (efficiency + passing only) | T4 +0.00 with both residuals; model D market 1.43 vs football 1.41; pass_res ≈ −0.78 × rush_res | **HIGH** that it is **not** a separate mispricing | Collinearity / decomposition mirror |
| Passing is more volatile | — | T6 +0.01; identical SD and tails; same turnover sensitivity | **HIGH** (against) | No |
| CONTROL magnitude is priced | Slopes 5.28 vs 5.09; T7 −0.18 (CI includes 0); deciles track | ML +2–4 pp pre-vig; opener residual +0.96 | **HIGH** | Yes |
| STRONG CONTROL is obvious to the market | AUC 0.84 from public proxies = \|spread\| AUC; 97 % favoured by ≥ 3 | 70 skeptical cases (early season) | **HIGH** | Yes |
| 2026 shift is an early-season effect | FBS-only 2026 inside the historical window; week-by-week narrowing; FCS rows +4.2 | 2026 FCS share (8.8 %) at the upper end of history | **HIGH** | Yes (composition + few games) |
| 2026 shift is a source mismatch | Different success definitions; level −0.008 | Matching dispersion; FBS-only matches; identity / scores match; no game-level overlap possible | **LOW** (not supported; untestable game-by-game) | No evidence |
| Pace is fully absorbed | T8 +0.21 (CI includes 0); quintiles track | Market moves +0.23 per SD during the week | **HIGH** | Yes |
| Defensive suppression is fully absorbed | T9 +0.39 (CI includes 0) | The claim subset +1.0 | **MODERATE** | Yes (slight over-adjustment) |
| Scoring environment is fully absorbed | T10 −0.96 (CI includes 0) | Lean toward over-adjustment (p 0.08) | **MODERATE** | Yes (slight over-adjustment) |

## AM. Most likely CFB market mechanism

* **The spread prices overall team quality, and the visible results that reveal it, almost perfectly.**
* **It under-weights one component of that quality:** how well a team stops the run, and to a lesser extent how well
  it runs.
* **That component pays out on the field** through ball control and fewer interceptions (the opponent is pushed into
  passing), accumulating steadily across the game.
* **It barely shows in the box-score outcomes** (points, record, Elo) that drive the number.
* **The "passing overweight" is the same fact seen from the other side.**
* **CONTROL, pace, suppression and the scoring environment** are what the market sees best, so their football
  effects are priced.

## AN. What information does the market appear to miss?

**Conservatively:**
* about 0.5–0.7 points per SD of the efficiency-orthogonal rushing / run-defense profile, of which the close recovers
  roughly a fifth from the opener;
* possibly a small over-reliance on win–loss record (POST_HOC, −0.46 per unit of win-% difference).

**Economically,** none of this reaches a demonstrated edge:
* it is about one point per game for the qualifying sides;
* the walk-forward RMSE gain is ≤ 0.015 points;
* the 2026 Kalshi replay is flat (+0.37).

## AO. What information does the market price well?

* overall efficiency;
* CONTROL magnitude, including STRONG;
* passing quality (once rushing is accounted for);
* pace and possessions;
* defensive suppression;
* the scoring environment;
* matchup interactions.

## AP. Future hypotheses (listed only; not tested)

These are in `docs/research/CFB_MECHANISM_FUTURE_HYPOTHESES.md`:

* **C2C-F1** Rush-defense component as the primary frozen feature.
* **C2C-F2** Interception channel / opponent pass-volume forecast.
* **C2C-F3** An SE-scaled early-season z.
* **C2C-F4** Separate the FCS-involved games.
* **C2C-F5** STRONG skeptical cases where Elo disagrees.
* **C2C-F6** Opener vs close for the rush and CONTROL signals.
* **C2C-F7** Record over-reliance.
* **C2C-F8** Scoring-environment over-adjustment in totals.

## AQ. Tests / CI

* **`tests/test_cfb_signal_mechanisms.py`: 37 tests**, covering every item in protocol §8 (prompt §49):
  * frozen-file and Wave-2A artifact pins;
  * the candidate hash;
  * CONTROL and the rush constants imported;
  * no future game in a feature;
  * no final score in the market model;
  * the spread never a football input;
  * opener / close and home / away orientation;
  * the parity join;
  * prior-games-only proxies;
  * FBS / FCS identity;
  * outcome-blind membership;
  * no prospective write;
  * deterministic rerun.
* **`ruff check src tests scripts`:** clean.
* **Full suite and CI:** see the PR.

## AR. Artifacts

`data/scripting/validation/signal_mechanisms/`:

| File | Contents |
|---|---|
| `build_manifest.json` | code SHA, CFBD digests, log pin / hashes, sidecar hash, residualisers, anchor, 2026 checks, parity |
| `games_2014_2025.jsonl.gz` | 8,786 study rows: pregame features, lines, outcomes, game channels, visible proxies |
| `games_2026.jsonl.gz` | 397 rows |
| `mechanism_report.json` | the artifact `cfb_signal_mechanisms/1.0.0`: formal tests, every section above, case-study ids, the absorption matrix |

Reproduce:

```
python3 scripts/signal_mechanisms_cfb.py build --cfbd-root <research-data cache v2> --sidecar-odds <espn_odds/2026.jsonl>
python3 scripts/signal_mechanisms_cfb.py analyze
```

## AS. Production impact

**None.** No change to:

* the Script Engine, V1, V2, CONTROL, claims or calibration;
* Value Watch, SIFT or the research conductor;
* Wave-2 capture, settlement or ledgers;
* recommendations, staking or the router.

Every file is new: research docs, a research module, two research scripts, a test, and the artifact directory.

## AT. Direct answers

1. **Why does rushing add margin information beyond efficiency?**
   * The efficiency-orthogonal run game, chiefly run defense, produces ball control and fewer interceptions (the
     opponent is pushed into passing).
   * That is worth more margin (+2.2 per SD) than the market's 1.7.
2. **Rush offense or rush defense?** **Rush defense** (+1.18 vs +0.57; T3 q 0.004).
3. **Does rushing create late-game separation?** **No.** The edge accrues evenly by half (1H +1.29, 2H +1.09
   matched; T1 −0.07).
4. **Is the market genuinely underweighting rushing information?**
   * In points, **yes**: about 0.5–0.7 per SD, 11/12 seasons, and it survives negative controls.
   * Economically, it is too small to clear vig, and 2026 is flat.
5. **Why does passing show a negative residual after controlling for efficiency?**
   * Because, with efficiency held fixed, more passing means less rushing (−0.78 correlation).
   * With both in the model, passing is priced correctly (T4 ≈ 0).
6. **Is passing information more heavily embedded in the spread than rushing information?**
   * Slightly, relative to its football value: the spread prices passing at full value (1.44 vs 1.47) and rushing
     at about 75 % (1.66 vs 2.17).
   * Both are priced mainly through the visible footprint (Elo / record).
7. **Why does CONTROL predict winners so strongly but fail ATS?**
   * The market moves the number one-for-one with CONTROL magnitude (5.28 vs 5.09 per unit).
   * Being a 90 % winner comes with a −23.5 spread.
8. **What usually causes CONTROL teams to win but fail to cover?**
   * A very large number (−20 on average) and a game that never separates: 64.5 % lead by less than half the spread
     at the half.
   * Turnovers are neutral, and the opponent stays efficient.
   * A late give-back is rare (11.5 %).
9. **Why does Kalshi almost never disagree with STRONG CONTROL?**
   * STRONG CONTROL is the obviously better team (public proxies AUC 0.84), and Kalshi sees it (median +21).
   * Disagreement happens only in early weeks, when priors and in-season data diverge.
10. **Is the 2026 shift real football, early-season noise or a source / data problem?**
    * **Early-season composition and noise.** FBS-vs-FBS 2026 matches history, and FCS-involved rows carry the shift.
    * There is no evidence of a source problem, though a game-by-game parity test was not possible.
11. **Does pace add anything the total market does not know?** **No** (T8 +0.21, CI includes 0).
12. **Does defensive suppression add anything the total market does not know?** **No** (T9 +0.39, CI includes 0;
    the market slightly over-adjusts).
13. **Which information is least efficiently incorporated?** The efficiency-orthogonal run-defense / rushing profile.
14. **Which is most efficiently incorporated?**
    * overall efficiency and CONTROL magnitude (STRONG especially);
    * passing (once rushing is in the model);
    * pace.
15. **Did this study reveal a research-integrity defect in Wave 2?** **No.**
    * Every frozen value reproduces exactly.
    * One interpretive note: Wave 2A attributed the 2026 shift to the source difference. This study shows it is
      instead explained by early-season FCS composition.
    * That changes no rule and no data.
16. **Should any current prospective Wave-2 rule change?** **NO.**
