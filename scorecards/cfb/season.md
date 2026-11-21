## CFB projection scorecard — season to date

Generated 2026-11-21T23:36:34Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 44327 across 312 games. Settled: 15702 (void/DNP 130, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 2030 (254) | 4.147 | +0.201 | 2.895 | 78% | — |
| game_total | pf-cfb (challenger) | 275 (275) | 13.360 | -0.070 | 9.269 | 80% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 273 (273) | 13.497 | +0.133 | — | — |  |
| margin | pf-cfb (challenger) | 275 (275) | 12.845 | +2.474 | 9.130 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 273 (273) | 13.018 | +2.917 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 444 (249) | 7.457 | +0.540 | 5.241 | 83% | — |
| passing_yards | pf-cfb (challenger) | 444 (249) | 62.173 | +11.275 | 44.397 | 86% | — |
| receiving_yards | pf-cfb (challenger) | 4281 (254) | 18.019 | +1.132 | 11.978 | 82% | — |
| receptions | pf-cfb (challenger) | 4281 (254) | 1.289 | +0.043 | 0.861 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 2030 (254) | 25.941 | +1.025 | 17.765 | 80% | — |
| team_points | pf-cfb (challenger) | 550 (275) | 9.180 | -0.035 | 6.497 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 546 (273) | 9.283 | +0.064 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.474 (95% CI +0.590..+4.509) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +11.275 (95% CI +3.046..+18.712) exceeds 5% of the mean
- receiving_yards: **ALERT** — bias +1.132 (95% CI +0.297..+1.800) exceeds 5% of the mean
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
