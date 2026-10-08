# Football Signal Discovery Lab — Wave 1 (CFB): results

**Verdict.** The search found repeatable CFB football relationships, and one coherent family the closing spread does
not fully price: **run-game efficiency is under-weighted and passing efficiency over-weighted by the closing spread,
conditional on overall efficiency.** It replicated in 2020–2025, the block whose lines had never been joined to these
features. It is about 0.6–1.4 points per feature SD, and **following it at −110 is at or just below break-even**
(52.1–52.8 % cover). It therefore reaches **MARKET_WATCH, not value**. CONTROL is real football and is **approximately
efficient against the closing spread at every tier**. **Moderate CONTROL shows no reliable ATS signal:** 921-869-44,
51.5 %, CI 49.1–53.8 %. Nothing here is a bet, and production is unchanged.

* **Kind of study:** RETROSPECTIVE DISCOVERY / VALIDATION. Every football season had been opened before; see the
  protocol, §2.
* **Protocol:** `docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE1_PROTOCOL.md`.
  * Set 1 (sha256 `8854fbcd…`) was committed at `2e9f55a7`, before any line was joined.
  * Set 2 (sha256 `babd3650…`) was committed at `a3277a0e`, after the block-A screen and before block B was opened.
* **Every number below** comes from `docs/research/FOOTBALL_SIGNAL_DISCOVERY_WAVE1_TABLES.md`, which is generated from
  `data/scripting/validation/signal_discovery_wave1/evaluation_report.json`.
  * The evaluation ran once, at code `a3277a0e` on 2026-10-08T18:54Z.
  * Seed 20261008; 2,000 bootstrap draws.
* **Registry:** `data/scripting/validation/signal_discovery_wave1/football_signal_registry.json`
  (`football_signal_registry/1.0.0`, 38 entries).

## 1. Data actually used

| Layer | Source | Seasons | Notes |
|---|---|---|---|
| Football features | CFBD cache (`research-data`), rebuilt by the production builder on pre-cutoff games only | 2014–2025 | 10,369 games, 8,786 eligible; identical eligibility to the archetype studies; CONTROL tier identical to the frozen V2 rows |
| Opponent adjustment | production `cfb-opponent-adjustment/1.0.0` (in-season ridge with home effect and shrinkage priors) | — | no new method; raw, adjusted, SE and games stored for headline metrics |
| Lines | CFBD `/lines`: median across providers | spread 2014–25 (8,776 / 8,786), total (8,680), moneyline **2021–25 only** (3,601) | untimestamped; no spread or total price, so −110 is **assumed**; opening spread 2021+ |
| Outcomes | CFBD final scores (cross-checked with box rows), quarter scores | 2014–2025 | 0 mismatches, 0 ties |
| Kalshi | 2026 catalog snapshots (spread ladder) | 2026 weeks 4–6 | descriptive CONTROL check only (§H) |

**Data that is unavailable and is not imputed:**

* explosive-play counts;
* QB identity, injuries;
* timestamped pre-close lines before 2021;
* historical team-total, 1H, 1Q and winning-margin lines.

## 2. Research design (as run)

1. **Stage B, set 1.** 30 pre-registered specs: the prompt's §38 list, context, baselines, and two DATA_UNAVAILABLE
   entries. They were evaluated on all seasons, with blocks A (2014–19) and B (2020–25) shown separately.
2. **Stage A screen.** Block A lines and outcomes only.
   * 410 associations, of which 273 are market associations.
   * 15 market associations reached whole-screen q < 0.05.
3. **Stage B, set 2.** 8 candidates written from the screen, each with a football reason, then judged on block B only.
4. **Multiple testing.** Benjamini–Hochberg within the football family (33 tests) and within the market family (32
   tests) across sets 1 and 2; Holm is reported alongside. Set 2 was also corrected on its own within block B.
5. **Status.** Assigned mechanically by `evaluate.assign_status`.
   * `VALUE_WATCH` is unreachable: it needs executable prices, and CFBD spreads carry none.
   * `EDGE_CONFIRMED` is never assigned in Wave 1.

## 3. Results by family

### Football signal (A): broadly real

* **CONTROL tiers (2014–2025):**
  * HOME STRONG wins 90.6 %; AWAY STRONG 83.1 %; HOME MODERATE 79.7 %; AWAY MODERATE 67.3 %.
  * Each tier beats its orientation base in the expected order.
  * At halftime, CONTROL sides lead by **+9.1 points** more than the orientation baseline (CFB-SIG-026,
    q < 0.001). The edge already shows by the half.
* **Pace, defensive suppression, scoring environment:** each moves total points by 4–10 points in the stated
  direction (CFB-SIG-008 to 013, all q < 0.001).
* **Rushing edge:** rushing beyond sustained efficiency adds +1.8 points of margin per SD (CFB-SIG-015, q < 0.001).
* **Passing edge:** passing beyond efficiency adds nothing to margin (−0.3/SD, q = 0.47).
* **Raw vs adjusted:** raw yards/play net predicts margin (+9.8/SD), but less well than the adjusted sustained net
  (+12.1/SD).

### Market signal (B): mostly priced; one family is not

| Signal | Market residual | Block A | Block B | Seasons in expected direction | Status |
|---|---|---|---|---|---|
| CFB-SIG-015 rushing net beyond efficiency | **+1.40 pts/SD**, p < 0.001, Holm 0.001 | +1.44 | +1.36 | 10/12 | MARKET_WATCH |
| CFB-SIG-014 passing net beyond efficiency | **−1.24 pts/SD**, p 0.001, Holm 0.025 | −1.04 | −1.45 | 10/12 | **OVERPRICED** (market over-rates passing edges) |
| CFB-DSC-001 rush-defense edge (set 2) | +0.76/SD overall; **block B +0.69, p 0.003** | +0.83 | +0.69 | **12/12** | MARKET_WATCH |
| CFB-DSC-002 rushing net (set 2) | block B +0.56, p 0.018 | +0.68 | +0.56 | 11/12 | MARKET_WATCH |
| CFB-DSC-003 pass-rate tendency (set 2, −) | block B −0.53, p 0.031 | −0.69 | −0.53 | 10/12 | MARKET_WATCH |

These are one phenomenon seen from several sides. The closing spread rewards passing production (visible yards and
points) and under-rewards rushing efficiency and run defense, relative to what both do to the final margin.

The **economic** reading is sobering. Following any of them at |z| ≥ 1 covers 52.1–52.8 % (ROI −0.6 % to +0.7 % at
an assumed −110), which is right at break-even (52.4 %). The residual is real at the level of points, but too small
per game to pay standard vig.

### Economic edge (C): none demonstrated

* No hypothesis cleared the −110 break-even with a confidence interval above it.
* The best stable family returns about zero.
* Moneyline economics (2021–25, provider odds):
  * CONTROL sides win 2.3 pp more than the no-vig price implies (q 0.048).
  * After the provider's vig, ROI is **+0.7 % [−2.3, +3.7]**.

## G. CONTROL deep dive

| Tier | n | Win | ATS W-L-P | Cover [95 %] | Mean resid | ROI@−110 | ML 2021–25: win − implied | ML ROI [95 %] |
|---|---|---|---|---|---|---|---|---|
| HOME MODERATE | 916 | 79.7 % | 467-428-21 | 52.2 % [48.9, 55.4] | +0.93 | −0.4 % | +4.6 pp (p 0.018) | +3.1 % [−2.5, 9.1] |
| AWAY MODERATE | 918 | 67.3 % | 454-441-23 | 50.7 % [47.5, 54.0] | +0.45 | −3.2 % | +0.5 pp | +1.3 % [−7.1, 10.3] |
| HOME STRONG | 1,452 | 90.6 % | 679-743-21 | 47.7 % [45.2, 50.3] | −0.13 | −8.8 % | +2.6 pp | −0.5 % [−4.1, 3.4] |
| AWAY STRONG | 930 | 83.1 % | 494-420-16 | 54.0 % [50.8, 57.3] | +1.18 (p 0.029) | +3.2 % | +1.5 pp | −1.1 % [−7.1, 4.8] |
| ALL CONTROL | 4,216 | 81.5 % | 2094-2032-81 | 50.8 % [49.2, 52.3] | +0.52 | −3.1 % | +2.3 pp (q 0.048) | +0.7 % [−2.3, 3.7] |

* **Historical consistency.** Win rates are stable across all 12 seasons (`TABLES` §6a). ATS by season swings
  between about 44 % and 56 % with no trend.
* **Strength is priced.**
  * The mean closing spread on the CONTROL side is −9.4 for MODERATE and −20.3 for STRONG.
  * MODERATE − STRONG mean residual is +0.31 points [−0.64, +1.25], indistinguishable from zero.
* **AWAY STRONG** (54.0 %, p 0.029) and **HOME STRONG** (47.7 %) look like opposite mispricings. They are 2 of 7
  descriptive tier cells. Neither was a pre-registered primary test and neither survives a multiplicity correction.
  Their pooled tier (CFB-SIG-003) is 50.2 %. They are recorded as **noise candidates** and as the next frozen
  hypothesis (§AD), not as findings.

## H. Moderate CONTROL ATS (required answer)

| Item | Value |
|---|---|
| Historical ATS n (2014–2025, closing consensus spread) | **1,834** |
| Record (W-L-P) | **921-869-44** |
| Cover rate (excl. pushes) | **51.5 %** (Wilson 95 % 49.1–53.8 %) |
| Break-even at −110 | 52.38 % |
| ROI at an assumed −110 | **−1.8 %** |
| Mean / median margin − closing spread (control side) | **+0.69 / +0.5 points** (bootstrap CI includes 0; p 0.06; BH q 0.149) |
| Residual quantiles p10/p25/p50/p75/p90 | −18.5 / −9.9 / +0.5 / +11.5 / +21.5 |
| HOME / AWAY MODERATE | 52.2 % / 50.7 % |
| As favourite / as underdog | 51.5 % (n 1,719) / 52.7 % (n 112) |
| Early (wk ≤ 6) / later | 55.7 % (n 233) / 50.8 % |
| vs **opening** spread, 2021+ | n 804, **53.9 %**, +1.25 points, ROI +2.8 %. The line moved toward the control side by +0.31 points on average (toward in 49.5 %, away in 38.7 %). |
| By season (cover %) | 49, 55, 54, 47, 49, 51, 44, 54, 46, 54, 55, 54 (2014→2025) |
| MODERATE vs STRONG | +0.31 points [−0.64, +1.25] |
| 2026 (Kalshi spread ladder, 87–118 min pre-kick) | 5 priced games, 4 above the implied margin (Iowa +5.2 → won by 1; NDSU −17.0 → 28; Texas Tech −13.6 → 22; Boise −18.7 → 19; USC −7.3 → 4). Two of the seven had no pre-kick snapshot. **Five games are not evidence.** |

**Answer: there is no reliable evidence that Moderate CONTROL predicts spread performance at the close.**

* The point estimate is slightly positive (+0.7 points).
* It is not significant after correction, never reaches break-even, and is equally explained by noise.
* The only hint is against the **opening** line from 2021. That was not a pre-registered primary test. It is listed
  as the frozen next-wave hypothesis (§AD).
* The 2026 6-0 moneyline run is consistent with history, where MODERATE wins 73.5 %. It says nothing about the spread.

## I. Interactions

| Interaction | Football | Market (closing spread / total) | Status |
|---|---|---|---|
| CONTROL × PACE (004) | margin slope −0.53/SD (q 0.08); the block-A screen interaction on home margin was +0.79 (z 2.8) | +0.63 pts/SD (p 0.010, q 0.041) but block B +0.55 with p 0.12; 8/12 seasons; follow rule 52.6 % | MARKET_WATCH (not stable) |
| CONTROL × defensive suppression (005) | win lift +0.33 (q < 0.001) | ATS 49.4 %; ML +2.0 pp, ROI +0.1 % | FOOTBALL_VALIDATED, market efficient |
| CLOSENESS → underdog ATS (006) | \|m\| ≤ 8 more often (V2 result) | 51.2 %, +0.16 | REJECTED (priced) |
| EVEN × LOW PACE → underdog (007) | — | 47.7 %, −0.82 | REJECTED |
| Pace → totals (008–010) | +4 to +7 points | residual 0 (HIGH 47.4 %, LOW 51.6 %) | APPROX_EFFICIENT |
| Defensive suppression → under (011) | −9.9 points vs mean | −1.0 residual, under 47.3 % | APPROX_EFFICIENT |
| Scoring environment (012/013) | ±8–9 points | residual ≈ 0 | APPROX_EFFICIENT |
| Passing matchup (014) | none beyond efficiency | **−1.24/SD**: the market over-rates passing | **OVERPRICED** |
| Rushing matchup (015/016, DSC-001/002) | +1.8/SD | **+1.4/SD**, replicated in block B | **MARKET_WATCH** |
| Disruption × weak protection (017) | win lift +0.24 | 49.6 % | APPROX_EFFICIENT |
| Finishing without efficiency (019) | small | 49.9 % | APPROX_EFFICIENT |
| Explosiveness (018) | — | — | **DATA_UNAVAILABLE** (no CFBD explosive counts 2014–25) |
| Trend / form, QB change (027) | — | — | **DATA_UNAVAILABLE** / not built (would need a new adjustment method) |
| Visibility within STRONG (DSC-004/005) | — | block A −1.33/SD; **block B −0.30 (p 0.50)** | **REJECTED** on block B (screen overfit) |
| Moderate × success-rate net (DSC-008) | — | block A +1.27; **block B +0.27 (p 0.57)** | **REJECTED** on block B |

## J. Market-efficiency matrix (CFB)

States: STRONG_RELATIONSHIP (q < 0.05 and stable), POSSIBLE (q < 0.10 or unstable), NO_SIGNAL, NOT_TESTED,
DATA_UNAVAILABLE. Rows are the football signal, and each cell is the market relationship.

| Signal | ML | Spread | Total | Team total | 1H | 1Q | Winning margin |
|---|---|---|---|---|---|---|---|
| CONTROL (any) | POSSIBLE (+2.3 pp vs no-vig, no ROI after vig) | NO_SIGNAL | NOT_TESTED | DATA_UNAVAILABLE | DATA_UNAVAILABLE (football +9.1 pts at half) | DATA_UNAVAILABLE | DATA_UNAVAILABLE (V2 intervals calibrated, football only) |
| MODERATE CONTROL | POSSIBLE (HOME +4.6 pp, n.s. ROI) | NO_SIGNAL (51.5 %) | NOT_TESTED | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| STRONG CONTROL | NO_SIGNAL | NO_SIGNAL (50.2 %) | NOT_TESTED | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Rushing edge beyond efficiency | NOT_TESTED | **STRONG_RELATIONSHIP** (sub-vig) | NOT_TESTED | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Passing edge beyond efficiency | NOT_TESTED | **STRONG_RELATIONSHIP (against)** | NOT_TESTED | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Pass-rate tendency | NOT_TESTED | POSSIBLE (block B q 0.08) | NO_SIGNAL (DSC-007) | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Pace | NOT_TESTED | NOT_TESTED | NO_SIGNAL | NO_SIGNAL (derived) | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Defensive suppression / scoring env. | NOT_TESTED | NOT_TESTED | NO_SIGNAL | NO_SIGNAL (derived) | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Closeness / even matchup | NOT_TESTED | NO_SIGNAL | NOT_TESTED | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Disruption × protection | NOT_TESTED | NO_SIGNAL | NOT_TESTED | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |
| Football baseline vs line | NO_SIGNAL (WF-ML) | NO_SIGNAL | NO_SIGNAL | NO_SIGNAL (derived) | DATA_UNAVAILABLE | DATA_UNAVAILABLE | DATA_UNAVAILABLE |

## U. Market disagreement (CFB)

* **Football strong / market strong** (control side favoured ≥ 10):
  * Win 91.0 %. Cover 49.3 % (n 2,013); ROI −5.9 % at −110.
  * Moneyline: won 88.9 % against an implied 86.9 %, ROI −1.4 %.
  * The market is right.
* **Football strong / market skeptical** (control side favoured < 3 or an underdog):
  * n 70. Cover 60.9 % (42-27-1), mean +5.25 points, p 0.009.
  * Moneyline (n 32): won 46.9 % against an implied 44.4 %.
  * The most interesting cell in the study, but **n < 100, so DISCOVERY_ONLY**. It is the top frozen candidate for
    prospective tracking (§Y).
* **Football moderate / market skeptical:** n 207, cover 52.0 %, nothing.
* **Football baseline and spread disagree on the favourite** (1,431 games): the market favourite won 62.5 %.
  Football-side ATS covered 49.7 % (−0.12 points). In 2021+ the football side's implied probability was 37.6 % and it
  won 38.6 % (n 648).
* **Calibration at the same price** (CONTROL side, moneyline 2021+):
  * In the 80–90 % implied bucket CONTROL won 88.2 % against an implied 85.2 % (n 507).
  * In the 60–80 % bucket, 73.7 % against 72.2 % (n 666).
  * Under the vig.
* **Who is right?** The market, almost always. The one exception is a small cell where a STRONG efficiency edge
  faces a skeptical spread.

## V. Multiple testing (CFB)

| Item | Count |
|---|---|
| Stage A screen associations (exploration only) | 410 (273 market); whole-screen BH q < 0.05: 15 |
| Locked hypotheses: football / market tests | 33 / 32 |
| Market tests with BH q < 0.05 (all seasons) | 10 (SIG-001, 004, 014, 015, 016, 023, DSC-001, 002, 003, 004) |
| Market tests with Holm p < 0.05 | 5 (SIG-014, SIG-015, DSC-001, DSC-002, DSC-003) |
| Set 2 surviving block B (BH q < 0.05 within set 2) | 1 (DSC-001); DSC-002 and DSC-003 at q 0.07 and 0.08 |
| Correction method | Benjamini–Hochberg (primary), Holm (reported); bootstrap CIs; per-season and block stability |

## W. Failed and rejected ideas (CFB)

* **Moderate CONTROL ATS:** 51.5 %, no evidence. Strong CONTROL ATS: 50.2 %.
* **CONTROL × PACE:** the market slope is not stable in block B, and its football slope has the wrong sign.
* **CLOSENESS → underdog**, and **EVEN × LOW PACE → underdog** (47.7 %): rejected.
* **Every totals idea** (pace, suppression, scoring environment, the football total baseline): football yes, market no.
* **Football scoring-baseline vs spread disagreement** (021) and **vs total** (022): no information beyond the line.
  The pre-registered walk-forward model combining seven football nets gave OOS correlation with the ATS residual
  **0.015 [−0.011, 0.042]**. Picks covered 46.7 % (n 412). WF-TOTAL picks covered 38.5 %. WF-ML Δlog-loss +0.0004.
* **Derived team totals** (025): nothing.
* **Set 2 screen winners that failed block B:**
  * DSC-004 / DSC-005, the "visibility" within STRONG CONTROL: block A −1.33/SD, block B −0.30.
  * DSC-006, efficiency minus scoring.
  * DSC-007, pass rate → under: block A −0.54, block B +0.13.
  * DSC-008, moderate × success rate: block A +1.27, block B +0.27.
  * This is the overfitting the protocol was built to catch: 4 of 8 screen candidates collapsed out of sample.
* **AWAY STRONG +54 % / HOME STRONG 47.7 % ATS:** descriptive tier cells that were not pre-registered as primaries.
  Likely noise until frozen and re-tested.

## Y. Best prospective candidates (CFB): for tracking only, not bets

Eligibility (protocol §11) requires all of the following:

* interpretable;
* adequate n;
* stable;
* not concentrated;
* FDR-surviving;
* coherent market;
* freezable.

| Rank | Candidate | Exact rule | Target market | Required future n | Why it survived | What falsifies it |
|---|---|---|---|---|---|---|
| 1 | **RUSH-DEFENSE / RUSHING EDGE vs spread** (CFB-DSC-001 + SIG-015) | Pregame production features at the 04:00 ET cutoff. z = (home rush-defense quality − away rush-defense quality) standardised with the 2014–2025 mean/SD in `evaluation_report.json`. Record the side favoured when \|z\| ≥ 1 and its Kalshi spread-ladder 50 % point at PRIMARY_60_180. | Kalshi full-game spread (the ladder rung nearest the 50 % point, executable ask, taker fee) | ≥ 400 games (2 seasons) to separate 52.5 % from 50 % at 80 % power; ≥ 1,500 to judge economics | Holm-surviving; 12/12 seasons same sign; replicated on unopened block B; football mechanism | Mean margin-minus-implied-spread ≤ 0 over the first 400 tracked games, or cover < 50 % |
| 2 | **STRONG CONTROL with a skeptical spread** (CFB-SIG-023) | Production V2 STRONG CONTROL and the control side's Kalshi-implied margin < +3 at PRIMARY_60_180 | Kalshi spread ladder and game winner | ≥ 100 games (history gives ~6 per season, so multi-season) | +5.25 points, 60.9 % cover (n 70); a football-strong / market-skeptical logic | Fewer than 52 % covers after 100 games |

Moderate CONTROL is **not** recommended for ATS tracking. Its moneyline is already tracked prospectively by
H1 (`CONTROL_PROSPECTIVE_2026_PROTOCOL.md`).

## AA. Tests and leakage proof (CFB)

`tests/test_signal_discovery_cfb.py`: 14 tests, all passing. They prove:

* no final score, and no later or same-slate game, reaches a feature row (target and later games doctored; the
  non-vacuous check passes);
* appending future games never changes an earlier row;
* rolling and opponent-adjusted metrics stop at the cutoff, with games counted equal to pre-cutoff games and no
  full-season averages;
* the CONTROL tier is the unmodified production claim;
* the feature module cannot import the lines, outcomes or evaluator modules and contains no line string;
* the production builder has no market input;
* the lines loader drops scores;
* the evaluator's arithmetic is correct (orientation, pushes, −110, BH);
* the runner refuses an unregistered hypothesis file;
* the frozen set-1 file equals the code.

## AB. Files

| Path | What |
|---|---|
| `src/cfb_edge_finder/signal_discovery/` | `features.py` (market-blind replay), `market_lines.py`, `outcomes.py`, `stats.py` (numpy only), `hypotheses.py` (sets 1 and 2), `screen.py`, `evaluate.py`, `deep_dive.py` |
| `scripts/run_signal_discovery_cfb.py` | stages: features, freeze-set1, freeze-set2, screen, evaluate |
| `scripts/signal_discovery_cfb_tables.py`, `scripts/signal_discovery_cfb_registry.py`, `scripts/signal_discovery_cfb_kalshi_2026_spread.py` | generated tables, registry, 2026 descriptive check |
| `data/scripting/validation/signal_discovery_wave1/` | `features/` (12 season tables plus a manifest with SHA-256s), `hypotheses_set1.json`, `hypotheses_set2.json`, `stage_a_screen.json`, `evaluation_report.json`, `evaluation_rows.jsonl.gz`, `football_signal_registry.json`, `control_2026_kalshi_implied_spread.json` |
| `docs/research/` | `FOOTBALL_SIGNAL_DISCOVERY_WAVE1_PROTOCOL.md`, `…_RESULTS.md` (this file), `…_TABLES.md`, `…_MARKET_AUDIT.md`, `CFB_SIGNAL_CATALOG.md` |

**Reproduce:**

1. Extract the CFBD caches from `origin/research-data` as in the archetype studies.
2. Run `python3 scripts/run_signal_discovery_cfb.py features …`.
3. Run `evaluate --cfbd 2014=<dir> … --work data/scripting/validation/signal_discovery_wave1/features --out data/scripting/validation/signal_discovery_wave1`.
4. Run `python3 scripts/signal_discovery_cfb_tables.py`.

The evaluator refuses to run if the protocol, the hypothesis files or the feature tables have changed.

## AC. Production impact

**None.** Nothing in any of these changed:

* V1/V2 CONTROL definitions;
* production claims, thresholds, intervals;
* SIFT output and app export;
* recommendation, staking, sizing;
* the router, the research conductor, the prospective ledger;
* market mapping and workflows.

The only non-research edit is a `ruff` per-file ignore in `pyproject.toml` for the generated-table script.

## Deviations from the protocol

* `evaluate.py` gained an `np.std == 0` guard (a feature with no variance) and a least-squares IRLS step. Both were
  found on synthetic data before the protocol commit. After the protocol commit, the only change was the set-2
  derived features, which were committed with the set-2 registration.
* The 2026 Kalshi spread-ladder check (§H) was added after the evaluation as a descriptive answer to the prompt's
  explicit question. It did not enter any status.
