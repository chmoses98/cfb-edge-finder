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
