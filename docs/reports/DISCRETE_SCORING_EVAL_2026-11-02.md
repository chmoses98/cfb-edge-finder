# Discrete score model vs Normal baseline — held-out evaluation (2026-11-02)

Research only. Nothing here is wired into RUN CFB; promotion needs an explicit owner decision.

## Protocol
- Universe: FBS-vs-FBS games in data/football/2026/team_games.jsonl (500 games, 347 rated with ≥2 prior games per team).
- Calibration: 208 games (2026-09-19 → 2026-10-17); shape and Normal dispersions fitted here only.
- **Held-out: 139 games (2026-10-17 → 2026-11-01)**, never used for fitting.
- Ratings refit per kickoff date from strictly earlier dates (point-in-time). Identical strikes for every system.

## Systems
| system | margin log score | total log score | team-score log score | event Brier | event log loss | event ECE | P(\|margin\|∈{3,7,10,14}) pred (obs 30.2%) |
|---|---|---|---|---|---|---|---|
| adjusted_discrete | -4.048 | -4.232 | -3.669 | 0.2398 | 0.6728 | 0.044 | 21.1% |
| adjusted_normal | -4.183 | -4.324 | -3.891 | 0.2422 | 0.6785 | 0.055 | 12.9% |
| naive_discrete | -4.074 | -4.194 | -3.667 | 0.2471 | 0.6873 | 0.032 | 23.6% |
| naive_normal | -4.197 | -4.279 | -3.885 | 0.2482 | 0.6895 | 0.014 | 14.9% |

Higher log score is better. `naive_normal` is the repo's existing Normal `GameDistribution` form with naive shrunk team averages.

## Paired bootstrap, per-game log-score gain vs naive_normal (95% CI)
| system | margin | total | team score |
|---|---|---|---|
| adjusted_discrete | +0.149 [+0.073, +0.233] ✔ | +0.047 [-0.040, +0.132] | +0.216 [+0.116, +0.304] ✔ |
| adjusted_normal | +0.014 [-0.049, +0.082] | -0.045 [-0.118, +0.027] | -0.007 [-0.052, +0.041] |
| naive_discrete | +0.124 [+0.073, +0.177] ✔ | +0.085 [+0.024, +0.146] ✔ | +0.218 [+0.126, +0.299] ✔ |

## Reading it honestly
- **The exact discrete shape is the robust gain**: `naive_discrete` and `adjusted_discrete` beat the Normal on margin and team-score log score with CIs excluding zero (`adjusted_discrete` margin +0.149, team +0.216 nats/game). This is what changes team-total, spread and key-number prices.
- **Opponent adjustment is not shown to help yet.** `adjusted_normal` ≈ `naive_normal` (CIs include 0); for totals, `naive_discrete` scores better than `adjusted_discrete`. Weeks 1–9 give 3–6 games per team, so rating noise is large. Event Brier favours the adjusted means (0.240 vs 0.247 for discrete) but event ECE is worse than the Normal's — calibration is not demonstrated.
- **Key-number mass is still under-predicted** by all systems (≈21–24% discrete vs 30% observed in a 139-game sample; Normal ≈13–15%). The discrete law closes about half the gap; the rest is a flagged open item, not fixed here.
- Fitted shape: fg_share=0.15, sigma_common=0.0, sigma_team=0.0 (grid lower edge: the compound-Poisson law already carries the needed dispersion).
- Overtime ending probabilities (`OT_OUTCOMES`) and the TD value pmf are assumed priors, not fitted (no overtime data in repo).

## Not supported by repository data (blockers)
- **epa**: no play-level EPA in repo data (only drive summaries / success counts)
- **injury_effect_sizes**: no calibrated injury magnitudes; injuries are not used to move means
- **market_prices**: no historical market lines in repo data; measures distribution quality, not profit
- **period_1q_1h**: no per-period scoring in team_games.jsonl; 1Q/1H distributions are refused, not guessed
- **special_teams**: no special-teams data

No market prices exist for these games, so this measures distribution quality — **not betting profit or CLV**.

## Key-number calibration (PR #249, owner comment 6588421337) — verdict: **NOT_FIXED** (aggregate gap closed; accuracy gain not demonstrated)

**What was wrong.** The uncalibrated model predicts 21.1% of games ending on a margin of exactly 3, 7, 10 or 14; the held-out sample shows 30.2% (and 31.2% on the calibration split, so the gap is not a held-out fluke). The per-number table shows it is almost all **margin = 3** (observed ~12-13.5%, model ~5.5%) and, less so, 7.

**Diagnosis, tried on the calibration split only.**
- *Event-count dispersion* (Poisson -> Binomial over a fitted number of scoring opportunities) barely moved the calibration likelihood (-7.361 Poisson vs -7.367 at n=28) or key-number mass (20.0% -> at most 20.6%). Rejected and removed.
- *Noisy adjusted ratings* (3-6 games per team) produce over-extreme predicted margins; a fitted shrinkage `margin_shrink` toward a zero difference (total preserved) improves the calibration likelihood (-7.361 -> -7.325 at 0.6-0.7). It moves key-number mass only from 20.0% to ~22%.
- *Key-number lift* (reweight joint cells whose |margin| is a key number, renormalise) closes the aggregate gap. Two variants, both fitted on the 208 calibration games only (`fit_calibration`, `fit_calibration_per_key`; neither can see a held-out game):
  - **v1 uniform lift**: `margin_shrink=0.7`, `key_lift=1.6`
  - **v2 per-key lift**: `margin_shrink=0.7`, lifts {3: 2.6, 7: 1.2, 10: 1.3, 14: 1.3}

Defaults are `margin_shrink=1, key_lift=1, key_lifts=()` — the uncalibrated baseline, bit-for-bit.

**Pre-declared rule** (written before the held-out numbers were read; `key_number_verdict`). CALIBRATION_FIXED iff all of: (A) held-out predicted key mass within 1.96 binomial SE of observed; (B) its gap smaller than the uncalibrated reference's; (C) paired held-out log-score gain positive on margin and not significantly negative on total/team; (D) held-out event Brier no worse than the reference by more than 0.001.

| candidate | A | B | C | D | verdict |
|---|---|---|---|---|---|
| v1 uniform lift | True | True | True | False | **NOT_FIXED** |
| v2 per-key lift | True | True | True | False | **NOT_FIXED** |

Predicted / observed mass per key number (mean over games):

| system | split | 3 | 7 | 10 | 14 | all four |
|---|---|---|---|---|---|---|
| adjusted_discrete | calibration | 5.3% / 13.5% | 6.6% / 7.7% | 4.2% / 4.3% | 3.9% / 5.8% | 20.0% / 31.2% |
| adjusted_discrete | heldout | 5.6% / 12.2% | 7.0% / 10.8% | 4.4% / 3.6% | 4.1% / 3.6% | 21.1% / 30.2% |
| adjusted_discrete_cal | calibration | 8.2% / 13.5% | 10.2% / 7.7% | 6.4% / 4.3% | 5.9% / 5.8% | 30.6% / 31.2% |
| adjusted_discrete_cal | heldout | 8.5% / 12.2% | 10.6% / 10.8% | 6.6% / 3.6% | 6.1% / 3.6% | 31.7% / 30.2% |
| adjusted_discrete_calk | calibration | 13.3% / 13.5% | 7.6% / 7.7% | 5.2% / 4.3% | 4.8% / 5.8% | 30.8% / 31.2% |
| adjusted_discrete_calk | heldout | 13.8% / 12.2% | 7.9% / 10.8% | 5.3% / 3.6% | 4.9% / 3.6% | 31.9% / 30.2% |

Held-out systems (higher log score is better):

| system | margin | total | team | event Brier | event ECE | key mass pred (obs 30.2%) |
|---|---|---|---|---|---|---|
| adjusted_discrete | -4.048 | -4.232 | -3.669 | 0.2398 | 0.044 | 21.1% |
| adjusted_discrete_cal | -4.024 | -4.230 | -3.669 | 0.2423 | 0.024 | 31.7% |
| adjusted_discrete_calk | -4.014 | -4.235 | -3.670 | 0.2431 | 0.026 | 31.9% |
| naive_discrete | -4.074 | -4.194 | -3.667 | 0.2471 | 0.032 | 23.6% |
| naive_discrete_cal | -4.062 | -4.191 | -3.669 | 0.2478 | 0.038 | 31.6% |
| naive_discrete_calk | -4.053 | -4.195 | -3.670 | 0.2486 | 0.038 | 31.5% |

Paired bootstrap, calibration effect (per-game log-score gain vs the uncalibrated counterpart, 95% CI):

| | margin | total | team score |
|---|---|---|---|
| adjusted_discrete_cal vs adjusted_discrete | +0.024 [-0.026, +0.071] | +0.003 [-0.005, +0.010] | +0.000 [-0.017, +0.018] |
| adjusted_discrete_calk vs adjusted_discrete | +0.034 [-0.026, +0.098] | -0.002 [-0.021, +0.018] | -0.001 [-0.019, +0.016] |
| naive_discrete_cal vs naive_discrete | +0.011 [-0.021, +0.044] | +0.003 [-0.003, +0.009] | -0.001 [-0.008, +0.005] |
| naive_discrete_calk vs naive_discrete | +0.021 [-0.022, +0.072] | -0.001 [-0.019, +0.018] | -0.003 [-0.009, +0.003] |

**Reading it.**
- The aggregate key-number gap IS closed on held-out (31.9% predicted vs 30.2% observed, SE 3.9%), and event ECE improves (0.044 -> 0.026).
- Probabilistic accuracy is **not** shown to improve: every log-score CI spans zero, and held-out event Brier is *worse* (0.2398 -> 0.2431), which fails criterion D. So the verdict is NOT_FIXED, and the model stays unpromoted regardless.
- **Disclosure:** v2 was designed after v1's held-out result had been seen, motivated by the calibration-split per-number gaps. Its held-out numbers are therefore not fully clean evidence. The rule above was not changed after any result was seen.
- **Single season.** The repository holds only 2026 (`data/football/2026`): 347 rated games. A margin of exactly 3 on 12-13% of games is higher than my general expectation for football, but that expectation is not evidence from this repository and I could not check it here. A lift fitted to one season's lumpiness may simply be fitting this season. Promotion needs multi-season data and a market-price comparison (neither exists in the repository).
- **1Q / 1H remain refused.** `team_games.jsonl` has no per-period scoring, so `build_period_distribution` still raises `MissingPeriodCalibration`; nothing here prices a quarter or half.
