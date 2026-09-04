# CFB Final Salvage Verdict

**Verdict: C — NO DEFENSIBLE BETTING EDGE CURRENTLY EXISTS.**

Issued 2026-09-04 as the closing document of the open-ended CFB research
cycle. This is a scientific audit, not a feature mission. Every number
below was recomputed from the underlying artifacts on the `research-data`,
`research-data-v2enrich` and `claude/cfb-model-v2-research-krupoc`
branches; prior report summaries were not trusted where the data could be
re-examined, and one of them turned out to be wrong (§4.3).

| Field | Value |
|---|---|
| Historical corpus | 6,266 FBS-vs-FBS games, 2017–2025 (2020 excluded), V2 walk-forward out-of-sample predictions persisted on `research-data` (`data/research/v2/preds/`) |
| Market data | CFBD `/lines` per book: consensus close for all seasons; opening spreads, opening totals and moneylines 2021+ (Bovada; DraftKings 2023+; ESPN Bet 2024+) |
| Prospective corpus (2026) | 6,992 observations, 102 games, 2,570 tickers; **684 settled contracts on 7 games** (Week 0 plus the Thursday 2026-09-03 slate) |
| Models compared | V2 (`ens_margin_d025_eq` + `tot_eff_ridge_d025_affine`, spec `cfb-v2-candidate-2026-09-02`), 0.5.0 control (2021–2025), market open/close |
| Reproduction | `scripts/salvage_audit/` (see its README); all intervals are bootstrap 95% |

---

## 1. What was investigated

The mission asked whether *any* defensible, economically useful edge can be
extracted. The following were each tested directly against prices:

1. **Independent price**: V2 side vs closing spread / total / moneyline at
   −110, at real per-book moneyline prices, and with the verified Kalshi
   fee (`0.07·P·(1−P)`).
2. **Market-residual model**: walk-forward ridge on `actual − close` using
   V2's full feature set plus the line itself.
3. **Market-movement predictor**: `(V2 − open)` against `(close − open)`,
   per season and per book; CLV in points; steam-follow and steam-fade.
4. **Opening-line model**: V2 side at the opener, and walk-forward stacking
   `margin ~ open + V2`.
5. **Filter for mispriced contracts / avoid list**: whether `|V2 − close|`
   predicts the market's absolute error.
6. **Calibration aid**: walk-forward logistic `P(cover) ~ (V2 − close)`.
7. **Regimes**: 49 cuts × {spread, total} × {all, |gap| ≥ 3} = 196 slices
   covering disagreement direction and magnitude, favourites/underdogs,
   home/away, spread size bands, Power/Group conferences, conference vs
   non-conference, neutral, postseason, week bands and months, coaching
   change, returning passing production, roster continuity, transfer-portal
   volume, talent gap, FBS newcomers, component agreement (struct vs eff
   ridge), distance from 0.5.0, dome, elevation, rest, travel, kickoff hour,
   extreme win probability. Weather was tested only on the partial 2025
   Open-Meteo sample the previous mission delivered (null there too).
8. **2026 prospective ledgers**: coverage, settlement, CLV, control and V2
   performance to date, and what the infrastructure preserves.

Pricing note: FBS-vs-FCS games are never priced by V2 or 0.5.0 (the
`UNSUPPORTED_POPULATION` boundary), so that regime cannot be evaluated and
is not claimed either way.

---

## 2. Headline results (V2 vs closing consensus, one bet per game)

### Spreads, −110

| filter | n | ATS % | ROI | 95% CI | seasons ROI > 0 |
|---|---|---|---|---|---|
| all games | 6,266 | 50.7 | −3.1% | [−5.4, −0.7] | 1/8 |
| \|V2 − close\| ≥ 3 | 2,917 | 50.2 | −4.1% | [−7.5, −0.6] | 1/8 |
| ≥ 5 | 1,433 | 49.7 | −5.0% | [−9.9, −0.2] | — |
| ≥ 7 | 603 | 50.3 | −3.8% | [−11.4, +3.9] | — |
| ≥ 10 | 183 | 53.9 | +2.8% | [−10.8, +15.9] | — |

V2 on home / away / favourite / underdog at ≥ 3: 51.0 / 49.5 / 50.6 / 50.0%.
MAE: V2 12.63, close 12.19 (paired, market better in every season).

### Totals, −110

| filter | n | % | ROI | 95% CI | seasons > 0 |
|---|---|---|---|---|---|
| all | 6,266 | 50.8 | −3.0% | [−5.5, −0.7] | 1/8 |
| \|V2 − close\| ≥ 3 | 2,888 | 51.6 | −1.6% | [−5.0, +1.9] | 3/8 |
| ≥ 7 | 613 | 52.6 | +0.4% | [−7.4, +7.5] | — |

### Moneylines (2021–2025, n = 3,771 games with book moneylines)

| model | log loss | Brier |
|---|---|---|
| V2 (walk-forward conditional scale) | 0.5629 | 0.1914 |
| 0.5.0 control | 0.6177 | 0.2148 |
| **de-vigged market** | **0.5475** | **0.1853** |

Paired log-loss difference V2 − market = **+0.0154 [+0.0086, +0.0226]**;
the market is better in all five seasons. At real book prices, betting
every V2-positive-EV side at the best available price: n = 3,168,
ROI −0.2% [−5.8, +5.5]; at EV ≥ 5%: −1.3%; per book Bovada −4.2%,
DraftKings −9.6%, ESPN Bet −5.6%. 0.5.0: −6.9% [−12.7, −0.7].

---

## 3. Does V2 predict market movement? (2021–2025, n = 3,937 with openers)

Yes, weakly — and it is not convertible into money.

| \|V2 − open\| | n | close moved toward V2 | against | no move | slope of (close−open) on (V2−open) | mean CLV (pts) |
|---|---|---|---|---|---|---|
| all | 3,937 | 47.7% | 39.3% | 13.0% | 0.098 (corr 0.19) | +0.26 [+0.20, +0.33] |
| ≥ 3 | 1,779 | 50.8% | 36.9% | 12.3% | 0.100 | +0.47 [+0.35, +0.58] |
| ≥ 7 | 358 | 56.1% | 33.5% | 10.3% | 0.131 | +1.04 [+0.66, +1.43] |

Per season the slope is 0.10, 0.06, 0.16, **0.01**, 0.14 — it vanished in
2024. The whole effect explains 3.6% of movement variance.

What it is worth:

- **Betting the V2 side at the opener** (settled against the actual
  result): 52.4% on all games, ROI +0.1% [−3.1, +3.0]; 51.4% at ≥ 3.
  Positive CLV, zero profit at −110.
- **Stacking**: `margin ~ open + V2` gives V2 a stable coefficient of
  0.20–0.27 but MAE 12.122 vs 12.127 for the opener alone
  (Δ −0.004 [−0.038, +0.029]). `close + V2`: Δ +0.001. V2 adds nothing
  measurable beyond either line.
- **Steam-follow** (bet V2 side vs close only after the market moved
  toward it): 51.9% at ≥ 3 (n = 532), ROI −0.9%. Steam-fade: 50.0%.

Conclusion: V2 leads roughly one tenth of the opener-to-close move, which
is the same information the market prices within the week. It is an
observation about market efficiency, not an edge.

---

## 4. Regime search

### 4.1 Nothing clears the multiple-comparison bar

196 slices were examined. The Bonferroni z for p < 0.05 is 3.66. The best
two:

| slice | n | % | ROI | z | seasons > 0 |
|---|---|---|---|---|---|
| totals, kickoff in September, \|V2 − close\| ≥ 3 | 843 | 55.9 | +6.7% [+0.4, +13.2] | 3.43 | 6/8 |
| spreads, Week 1, all | 376 | 56.4 | +7.7% [−1.5, +17.3] | 2.48 | 5/8 |

Every other slice has z < 2.3.

### 4.2 Week 1 spreads: non-stationary, rejected

2017–2019: 65.9 / 63.6 / 64.4%. 2021–2025: 51.0 / 52.8 / 50.0 / 61.5 /
45.8% (pooled 51.9%, ROI −1.0%). The 0.5.0 control on the same 2021+ Week 1
games: 50.2%. Whatever V2's preseason features exploited before 2020 is
gone.

### 4.3 September totals: real pattern, but it is not V2

The pattern survives leave-one-season-out (55–57% every time), every
early-season definition (weeks 1–4 / 2–5 / month), thresholds 3–6, and
real books 2023–2025 (57–61%). The out-of-sample slope of the market's
total residual on `(V2 − close)` is positive in all six September test
seasons and ≈ 0 in October onward.

But it decomposes completely into mean reversion of extreme early-season
market totals:

| September, \|V2 − close\| ≥ 3 | n | % | ROI |
|---|---|---|---|
| V2 side agrees with mean reversion | 696 | 57.5 | +9.6% [+2.5, +16.6] |
| V2 side disagrees with mean reversion (V2's side) | 147 | 48.6 | −7.1% |

A model-free walk-forward affine shrink of the market total (`a + b·close`
fit on prior Septembers) reaches 55.3% at ≥ 1 point (n = 636) and 60.4%
at ≥ 2 (n = 193). A plain fade of September totals ≥ 65 (under) hits
60.8% (n = 172), of totals ≤ 48 (over) 55.5% (n = 340). None of this
appears in October–January.

So: an early-season totals-overdispersion anomaly exists in the CFBD
consensus closes, V2 contributes nothing beyond "shrink toward the mean",
it was found post hoc in a 196-slice sweep, and it misses the corrected
significance bar. It is registered below (§8) as a prespecified
prospective hypothesis, not promoted to a signal. At Kalshi's executable
prices (ask ≈ 0.52–0.55 plus the taker fee) break-even is 54–57%, inside
the interval.

### 4.4 The market-residual model, avoid filter and calibration aid

- **Residual model**: pooled out-of-sample correlation 0.037; the ridge
  alpha jumps between 100 and 100,000 across folds; |pred| ≥ 1 gives
  54.1% (n = 1,634, ROI +3.2% [−1.3, +7.8]). Not defensible.
- **Avoid filter**: corr(|V2 − close|, market absolute error) = 0.007.
  The market's error is not larger where V2 disagrees; V2's own uncertainty
  model does not predict it either (0.002).
- **Calibration aid**: walk-forward logistic slope of P(cover) on
  (V2 − close) is 0.001–0.007 per point; log loss is indistinguishable
  from a coin.

### 4.5 A correction to the V2 enrichment report

`docs/v2/CFB_V2_DATA_ENRICHMENT_REPORT.md` §16 states that V2 "already beats
the close on winner log loss" (0.5357 vs 0.5392). On the same 3,771 games
with actual book moneylines the market wins 0.5475 vs 0.5629 with a paired
interval excluding zero. `market_benchmark_final.json` on `research-data`
already carried the correct figures; the text claim should not be relied
on.

---

## 5. The four kinds of value, separated

| kind | finding |
|---|---|
| **Predictive value** | Real. V2 beats 0.5.0 by 1.4 points of margin MAE and beats the naive prior by ~3.5 points; it is a respectable projection. It is 0.43 points worse than the close and adds nothing to the opener. |
| **Calibration value** | Its probabilities are well calibrated in bins (0.06 → 0.06, 0.97 → 0.96), but they are strictly dominated by de-vigged market probabilities. No contract or side is better priced by V2 than by the market. |
| **CLV value** | Small and genuine at the opener (+0.26 to +1.04 points), disappearing by the close. Worth less than the vig; not present on Kalshi's own open-to-close path yet (n too small). |
| **Actual betting value** | None demonstrated. Every price-facing test is at or below break-even, with intervals that include the fee-adjusted loss. |

---

## 6. 2026 prospective system — what it has and what was wrong

### 6.1 State as of 2026-09-04

- 6,992 canonical observations (0.4.0: 4,157; 0.5.0: 2,835) across all
  timing labels: EARLY_OPEN 2,570, T_7D 783, T_3D 1,737, T_24H 751, T_6H
  331, T_90 179, T_60 179, T_30 231, **CLOSING 231**. The empty CLOSING
  column reported in `EMPIRICAL_RESEARCH_GATE.md` has been filled since.
- 684 settled contracts on 7 games; 71% of settled rows have a captured
  CLOSING price. Model-favoured research P/L is +24% [−15, +50] on 7 game
  clusters — no information at that sample.
- Kalshi executable YES + NO asks sum to a median 1.02, 75th percentile
  1.08, maximum 1.99: the spread cost is comparable to any edge measured
  anywhere in this document.
- Retro-scoring the frozen V2 artifact on the 7 settled games: −37% of
  risk, margin MAE 26 on the three CLOSING-covered games (UMass–Rutgers:
  V2 +39.9, actual −16). Seven games; a caution, not a result.
- CFBD quota: **0 of 1,000 remaining, resets 2026-10-01.** The collector
  runs on cached football state plus the ESPN schedule/result fallbacks.

### 6.2 Defect fixed in this mission

**Every V2 shadow row written so far (422) was unavailable** with reason
"not in the frozen V2 slate". The artifact is keyed by the CFBD game id
the V2 dataset was built from (`401858424`), the scanner looked up the
canonical slug (`cfb-2026-wk01-uab-at-illinois`). The only end-to-end test
had built its fake artifact keyed by slugs, so it could not see it, and
that test was then deleted with the scipy cleanup (`868674a`).

Fix: `research.v2_shadow.resolve_artifact_game_id` resolves through the
matched `GameRecord.source_game_ids["cfbd"]` with the canonical id as the
fallback; `build_row` takes the artifact key separately from the recorded
canonical id, so the ledger schema is unchanged. Regression tests drive
the real `_apply_scan` with a CFBD-keyed artifact and slug observations,
and the failing form of the test was confirmed before the fix. The 422
existing rows are left as they are (append-only, and "V2 had no opinion
because of a defect" is itself evidence); rows from the next capture on
will carry V2 prices.

### 6.3 Defects and gaps not fixed, recorded for the owner

1. **The V2 artifact is frozen with preseason-only state for all 761
   games of 2026.** The dataset firewall never reads a 2026 result, so V2
   Week 8 predictions are the same preseason model as Week 1. The shadow
   will look progressively worse through the season and its Weeks 4+
   comparison against 0.5.0 (which does update) will be meaningless. A
   weekly rebuild needs CFBD calls that do not exist until October. This
   should be decided deliberately, not left to drift.
2. The heartbeat carries no V2 telemetry (`v2_shadow_state`,
   `v2_shadow_unavailable_reasons` exist in the scan telemetry but are not
   surfaced), which is why a 100% unavailable shadow ran for a day unnoticed.
3. Settlement lags: `akron-at-wake-forest` (final 2026-09-03) was
   unattributed at audit time; `pending_not_final` rows are consistent with
   the ESPN fallback cadence, not a defect, but worth watching while CFBD
   is at zero.
4. Nothing captures a **lineup/QB assumption**: every 2026 observation
   carries `qb_status_confirmed=False` and `home/away_qb_state=unknown`.
   The live sidecar's ESPN injury feed returned empty lists for 178 of 182
   teams. This is exactly the information the historical forensics say the
   market has and the model does not; it is still not being recorded in a
   form that could later explain a disagreement.

### 6.4 What the ledgers do preserve (verified)

Model projections and versions per observation (`data_versions`,
`model_version.ratings_component_version`, `training_cutoff`); executable
YES/NO asks, midpoint and capture timestamp at every timing label including
CLOSING with `closing_status` reasons; fee schedule version and fee-adjusted
gap; settlement with source (`cfbd` / `espn_fallback`), overtime flag and
Kalshi cross-check; per-observation YES and NO economics; CLV inputs
(entry and closing quotes on the same key); the CONTROL/talent-shadow
linked rows and the V2 shadow rows with artifact sha; capture state and
source-failure heartbeats; the live sidecar's timestamped ESPN odds,
injuries and Open-Meteo forecasts on `research-sidecar`. Nothing here
writes an outcome into a frozen research artifact.

---

## 7. What should continue, what should stop

**Continue (zero-cost, evidence-preserving):**

- The canonical 0.5.0 capture across all timing labels, settlement, and
  the weekly report.
- The V2 shadow, now that it prices — with the frozen-state caveat above.
- The live sidecar (odds/injuries/forecast).
- The talent shadow linked rows.

**Stop:**

- Feature engineering on the historical archive. The enrichment report's
  ceiling finding is confirmed from the market side: no free historical
  family, and no regime, moves V2's side-of-market accuracy off 50%.
- Threshold research, gap-bucket ranking, and any "which contracts to
  bet" analysis on retrospective data. The answer is known.
- Model version work aimed at closing the 0.43-point gap to the close.
  Closing it entirely would still yield a market-equivalent, not an edge.

---

## 8. Prospective hypothesis register (not a signal, not a bet)

To make sure the one surviving pattern is judged on data it has never
seen, it is written down now with its kill and promote rules. It is a
**market anomaly** hypothesis; V2 is not part of it.

**H-SEP-TOTALS-2026.** For FBS-vs-FBS games kicking off in September, the
closing market total is over-dispersed: totals ≤ 48 settle over and
totals ≥ 62 settle under more than 52.4% of the time each.

- Population: Kalshi total contracts at the T_24H label or later
  (CLOSING preferred), the strike nearest 50¢ defining the market total;
  independently, the sidecar's DraftKings closing total.
- Direction fixed in advance: over on ≤ 48, under on ≥ 62. Nothing else.
- Unit: game. One decided game per game-side.
- **Kill** if, after ≥ 150 decided games (this will take the 2026 and 2027
  Septembers), the pooled hit rate's 95% lower bound is below 52.4%, or if
  any single September with ≥ 60 games is below 50%.
- **Promote to shadow candidate** only if the pooled lower bound exceeds
  the executable break-even at the observed asks (≈ 54–57% on Kalshi) —
  after which the existing protocol (`PROSPECTIVE_RESEARCH_PROTOCOL.md`)
  applies unchanged: discovery/validation separation, human review, no
  self-promotion.
- 2026's September games are already being captured; this file's commit
  is the preregistration timestamp. The first evaluable count arrives at
  the end of September 2026 and will be roughly 50–70 games — a
  hypothesis-test, not a decision.

---

## 9. Is a real-money bet justified today?

No. There is no market, regime, timing label or price band in which the
system's side beats the executable price after fees with an interval that
excludes a loss. The recommendation locks in `docs/RECOMMENDATION_SKELETON.md`
should remain closed.

---

## 10. Remaining risks

- The historical market benchmark is CFBD's book snapshot, not Kalshi.
  Kalshi's own open-to-close path may differ; only the prospective ledger
  can answer that and it has 7 games.
- V2's 2026 shadow is a preseason model all season (§6.3.1).
- The CFBD quota is exhausted for September; settlement and schedule run on
  fallbacks.
- The September-totals anomaly, if real, is small enough to be consumed by
  Kalshi's spread and fee.
- One more risk is behavioural: a 55–60% historical table is exactly the
  kind of number that invites betting before the register above has run.
  It was found by searching, and it is recorded here so that it is judged
  by data that had no part in finding it.
