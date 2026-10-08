# Football Signal Discovery Lab — Wave 1 (CFB): pre-registered protocol

Status: **PRE-REGISTERED**

This protocol was committed before any CFBD line was joined to any script-engine feature row or game outcome. The
evaluator (`scripts/run_signal_discovery_cfb.py evaluate`) refuses to run unless this file is marked as above and
names the canonical SHA-256 of every hypothesis file it is about to evaluate.

| Item | Value |
|---|---|
| Base main | `d777c0b949595543407523fa62e6d31b1a8234d5` |
| Research version | `cfb-signal-discovery-wave1/1.0.0` |
| Feature schema | `cfb_signal_features/1.0.0` |
| Football methodology read (unchanged) | `cfb-script-engine/1.3.0` builder + `cfb-script-engine/2.0.0` claims, `cfb-opponent-adjustment/1.0.0` |
| **Hypothesis set 1** | `data/scripting/validation/signal_discovery_wave1/hypotheses_set1.json` |
| **Set 1 canonical SHA-256** | **`8854fbcdd55cbe458d2fe8c7ac44277a73d8bd054a2f7ace63da27e854600e77`** |
| Set 2 (Stage A candidates) | written after the Stage A screen; its hash is appended in §12 before any block-B evaluation |
| Kind of study | **RETROSPECTIVE DISCOVERY / VALIDATION.** Changes no production rule, threshold, claim, SIFT output, recommendation, stake, router behaviour or market mapping. |

## 1. Questions (kept separate for every candidate)

* **A. Football signal.** Does the pregame relationship predict a football outcome (winner, margin, total, first-half
  margin, team points)?
* **B. Market signal.** Does it predict the outcome *relative to the closing line* (ATS residual, total residual,
  moneyline residual)?
* **C. Economic edge.** At a price, after vig/fees, would buying the contract have returned money?

A strong A with a null B is reported as the market pricing the signal, never as a failure of the football signal.

## 2. Honest provenance (why nothing here is a sealed holdout)

| Data | What has already seen it |
|---|---|
| CFBD football 2014–2025 | Fully opened. Archetype Wave 1 (2021–25) and the V2 sealed holdout (2014–20) joined every game's result to the same pregame records this study reuses. CONTROL tier win rates and margins for every season are published. |
| CFBD lines 2014–2025 | **Never joined to a script-engine feature or CONTROL tier.** The retired projection model joined median closing lines to outcomes (`research-data:data/research/v2/preds/market_close.parquet`, 2017–19 and 2021–25) to score that model; the CONTROL × spread question has never been computed. |
| Kalshi 2026 | Prices captured prospectively, weeks 1–6; CONTROL × game-winner already studied (`docs/CONTROL_2026_MARKET_RESULTS.md`). |

Consequently **every result in this wave is labelled RETROSPECTIVE DISCOVERY / VALIDATION**. Block B (2020–2025) is
"lines not yet opened against these features", not untouched data. Any rule meant for use must pass a frozen
prospective test (§11).

## 3. Data

* **Features (market-blind, outcome-blind).** For every scheduled CFBD game 2014–2025, the unmodified production
  builder is run on `gamelog.before(rows, data_cutoff(kickoff))` only (04:00 ET on the local game date; history games
  must have kicked off ≥ 4h30 before). The production matchup vector is flattened: seven dimension nets and both
  directional edges, every adjusted unit metric for both teams and both units (FBS-SD scale), RAW / ADJUSTED / SE /
  games for the headline metrics, pace (possession and tempo environments), volatility context, the opponent-adjusted
  scoring baseline, and the production V2 claims (CONTROL tier, CLOSENESS, PACE, SCORING ENVIRONMENT, DEFENSIVE
  SUPPRESSION, DISRUPTION). In-season only, as production.
  * Produced and hashed before this protocol: `features_manifest.json` (code `d777c0b9`, 10,369 games, 8,786 eligible;
    eligible counts per season identical to the archetype studies; CONTROL tiers identical to the frozen V2 holdout
    rows, 477/477 checked for 2020).
* **Lines (market side).** CFBD `lines_*` per provider, untimestamped (`CLOSING_UNTIMESTAMPED`). Consensus rule fixed
  in `signal_discovery/market_lines.py`: median across distinct providers (alias-normalised) for spread and total;
  opening spread median (2021+); moneyline = median no-vig probability; economics use one provider's actual odds in
  priority DraftKings > ESPN Bet > Bovada > William Hill (NJ) > Caesars. Score fields in the lines payload are dropped
  on load. A game whose line ids disagree with the feature orientation is `MARKET_ORIENTATION_MISMATCH`.
* **Outcomes.** CFBD `games` final points, cross-checked against the box rows (`OUTCOME_MISMATCH` excluded); quarter
  scores for 1H; ties excluded.
* **Unavailable, reported not imputed.** Explosive-play counts (CFBD has none 2014–25), QB identity / QB change,
  injuries, historical team-total / 1H / 1Q / winning-margin lines, any timestamped pre-close spread before 2021.

## 4. Eligible population

Feature eligibility as the archetype studies (both teams have ≥ 1 prior game; sustained-efficiency net and baseline
defined). Market tests additionally need the relevant consensus line. FCS-involved games are included; FBS-only is a
reported sensitivity. Regular season and postseason are both included.

## 5. Two-stage design

* **Stage B, set 1 (this commit).** The prompt's required studies (§38) and baselines, written as declarative specs
  in `signal_discovery/hypotheses.py`, thresholds fixed from football logic and the production finding scale only.
  Evaluated once on all seasons 2014–2025 with blocks A (2014–19) and B (2020–25) shown separately.
* **Stage A screen.** Only after set 1 is frozen. Opens lines and outcomes for **block A seasons only** (the runner
  refuses any other season). Ranks ~300 univariate features, 8 curated football interactions and within-CONTROL-tier
  conditionals against home ATS residual, total residual, home margin and total points; BH q across the whole screen.
* **Stage B, set 2.** At most 8 candidates from the screen, each with a football interpretation, exact rule and
  threshold, written to `hypotheses_set2.json` and hashed into §12 **before** block B is opened for them. Set-2 status
  is decided on block B only; block A is shown as its discovery sample.

## 6. Metrics

* SIDE specs: football lift = win (or |margin| ≤ 8, or 1H margin) minus the same-season orientation base rate (home,
  away or neutral); margin distribution (mean, quantiles, SD). ATS: n, covers, pushes, losses, cover rate (Wilson),
  mean / median residual with bootstrap CI, residual quantiles, ROI at an **assumed −110** (break-even 52.38 %),
  opening-line residual and line movement (2021+). ML (2021+): win − no-vig implied, Brier, ROI at the provider's
  actual closing odds.
* TOTAL specs: total vs season mean (football); total residual, over/under rate, ROI at assumed −110 (market).
* SLOPE specs: OLS slope per feature SD (HC1 SE) of margin / total (football) and ATS / total residual (market),
  optional controls (partial slope); quintile tables; a pre-registered follow-the-feature rule (|z| ≥ 1).
* Price basis is always named: `ASSUMED_-110` for spreads/totals (CFBD records no spread or total price; never called
  executable) and `PROVIDER_CLOSING_ODDS` for moneylines (a sportsbook reference, untimestamped).

## 7. Multiple testing and the status rule (mechanical)

* Families: all non-baseline, non-unavailable hypotheses of sets 1 + 2 together; BH q-values computed separately for
  the primary football test and the primary market test of each hypothesis; Holm reported beside them. The Stage A
  screen reports its own whole-screen BH q (≈ 700 associations) and is never cited as evidence.
* Status (in `evaluate.assign_status`; frozen in set 1 as `status_rule`):
  * `DATA_UNAVAILABLE` — cannot be tested with the source.
  * `MARKET_WATCH` — market residual q < 0.05, sign stable (≥ 60 % of seasons with n ≥ 10 and both blocks agree) and
    in the signal's favour, **but** economics only at an assumed or untimestamped price; or market q < 0.10 without the
    stability.
  * `OVERPRICED` — market residual q < 0.05, stable, against the signal side.
  * `APPROX_EFFICIENT` — football q < 0.05 in the expected direction and the market residual is indistinguishable from
    zero (cover-rate CI half-width ≤ 5 pp, or slope within 2 SE of 0).
  * `FOOTBALL_VALIDATED` — football passes; market inconclusive (or no historical market).
  * `DISCOVERY_ONLY` — market n < 100.
  * `REJECTED` — neither test clears its threshold.
  * `VALUE_WATCH` is **not reachable** from historical data in this wave: it requires executable economics, which only
    exist for 2026 Kalshi captures. `EDGE_CONFIRMED` is never assigned in Wave 1.

## 8. Stability (every hypothesis)

Season-by-season, blocks A/B, leave-one-season-out range, early (week ≤ 6) vs late, signal side home/away/neutral,
favourite vs underdog, conference vs non-conference, FBS-only vs FCS-involved, top-contributing-team removal, top-5
team row share.

## 9. Walk-forward (pre-registered models, no selection)

* `WF-ATS`: OLS of home ATS residual on seven fixed football nets/gaps; train on all seasons < N, test N, N = 2017…2025.
  OOS correlation, MSE vs zero, picks with |prediction| ≥ 2 points at −110.
* `WF-TOTAL`: same for the total residual on five fixed features.
* `WF-ML`: logistic home win on market logit vs market logit + two football features (ridge 1.0); train 2021…N−1,
  test N = 2022…2025; log loss and Brier vs the raw market.

## 10. Required deep dives

CONTROL tiers (HOME/AWAY × MODERATE/STRONG and pooled) vs spread and moneyline: n, record, pushes, cover rate,
residual distribution, by season, favourite/underdog, spread buckets, opening line (2021+), early/late,
MODERATE − STRONG residual difference; market-disagreement regimes (football tier × market confidence: control side
favoured ≥ 10 / 3–9.5 / < 3 or underdog); baseline-vs-market favourite disagreement; CONTROL win rate by market
implied-probability bucket.

## 11. Prospective requirement

Nothing here is used for a decision. A candidate may be recommended for prospective tracking only if it is
interpretable, n is adequate, stable in time, not concentrated in a few teams, survives BH, has a coherent market
relationship and can be frozen as an exact rule before kickoff. Prospective rows must carry signal id/version,
feature snapshot, contract, executable price and timestamp, later settlement, code SHA and protocol SHA; no backfill.

## 12. Set 2 registration (appended after the Stage A screen)

The Stage A screen (`stage_a_screen.json`, block A 2014–2019 only, code `2e9f55a7`) tested 410 associations
(273 market, 137 football); 15 market associations had whole-screen BH q < 0.05 and 21 had q < 0.10. They were
dominated by one theme (run-game / run-defense efficiency vs the spread) plus a "visibility" pattern inside STRONG
CONTROL (the more the control team's adjusted scoring offense out-rates its opponent's, the worse it covers) and a
pass-rate style effect. None of the 8 curated interactions had a market q < 0.3. Eight candidates were written down:

| Id | Rule (exact spec in `hypotheses_set2.json`) | Expected |
|---|---|---|
| CFB-DSC-001 | slope of home ATS residual on home − away rush-defense quality (`*_def_q.rush_success_rate`) | + |
| CFB-DSC-002 | slope of home ATS residual on production `net.rushing` (no controls) | + |
| CFB-DSC-003 | slope of home ATS residual on home − away raw pass rate | − |
| CFB-DSC-004 | within STRONG CONTROL: slope of control-side ATS residual on control-oriented adjusted ppg-offense quality difference | − |
| CFB-DSC-005 | STRONG CONTROL with that difference ≤ 1.5 (rounded block-A median 1.645) → control side ATS | + |
| CFB-DSC-006 | slope of home ATS residual on `net.sustained_efficiency − net.scoring` (not screened; generalises DSC-004) | + |
| CFB-DSC-007 | slope of total residual on home + away raw pass rate | − |
| CFB-DSC-008 | within MODERATE CONTROL: slope of control-side ATS residual on control-oriented success-rate matchup net | + |

| Item | Value |
|---|---|
| **Set 2** | `data/scripting/validation/signal_discovery_wave1/hypotheses_set2.json` |
| **Set 2 canonical SHA-256** | **`babd3650f0835b7ec60a7705ed49d71e7fd19d83797eb150075445d8294bd234`** |
| Decisive sample | block B (2020–2025) only; block A is shown as the discovery sample, never as validation |
| FDR | set-2 primary tests join set 1 in the all-hypothesis BH family, and are also corrected within set 2 on block B |
| Derived features | `evaluate._derive_set2` (pregame quantities only), committed with this registration |

## 13. Deviations

Any departure from this protocol is listed in the results document with its reason.
