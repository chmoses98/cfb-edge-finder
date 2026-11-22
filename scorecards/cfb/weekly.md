## CFB projection scorecard — last 28 days

Generated 2026-11-22T19:33:14Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 44327 across 312 games. Settled: 18946 (void/DNP 150, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 2018 (256) | 4.108 | +0.176 | 2.866 | 77% | — |
| game_total | pf-cfb (challenger) | 256 (256) | 12.551 | +0.758 | 8.723 | 83% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 254 (254) | 12.777 | +0.980 | — | — |  |
| margin | pf-cfb (challenger) | 256 (256) | 12.982 | +3.166 | 9.252 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 254 (254) | 13.381 | +3.286 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 452 (251) | 7.136 | +0.712 | 5.090 | 84% | — |
| passing_yards | pf-cfb (challenger) | 452 (251) | 61.885 | +9.384 | 44.156 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 4268 (256) | 18.157 | +0.921 | 12.097 | 82% | — |
| receptions | pf-cfb (challenger) | 4268 (256) | 1.290 | +0.052 | 0.863 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 2018 (256) | 25.748 | +0.872 | 17.652 | 80% | — |
| team_points | pf-cfb (challenger) | 512 (256) | 9.012 | +0.379 | 6.358 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 508 (254) | 9.163 | +0.489 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +3.166 (95% CI +1.160..+5.114) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
