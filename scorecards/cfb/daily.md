## CFB projection scorecard — last 7 days

Generated 2026-11-12T00:09:30Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 30973 across 242 games. Settled: 10620 (void/DNP 78, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 461 (59) | 3.969 | +0.376 | 2.766 | 79% | — |
| game_total | pf-cfb (challenger) | 59 (59) | 13.785 | -2.473 | 9.591 | 80% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 59 (59) | 14.102 | -2.359 | — | — |  |
| margin | pf-cfb (challenger) | 59 (59) | 14.511 | +7.943 | 10.400 | 76% | — |
| margin | script-engine-scoring-baseline (incumbent) | 59 (59) | 15.024 | +8.864 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 98 (55) | 7.261 | -0.082 | 5.034 | 86% | — |
| passing_yards | pf-cfb (challenger) | 98 (55) | 56.427 | -2.107 | 40.921 | 89% | — |
| receiving_yards | pf-cfb (challenger) | 980 (59) | 18.454 | -0.339 | 12.382 | 82% | — |
| receptions | pf-cfb (challenger) | 980 (59) | 1.307 | -0.034 | 0.882 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 461 (59) | 24.446 | +2.306 | 16.776 | 80% | — |
| team_points | pf-cfb (challenger) | 118 (59) | 9.672 | -1.236 | 7.001 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 118 (59) | 10.012 | -1.183 | — | — |  |

**Drift checks**

- carries: **ALERT** — bias +0.376 (95% CI +0.002..+0.709) exceeds 5% of the mean
- game_total: **INSUFFICIENT** — 59 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 59 settled forecasts (< 100)
- passing_attempts: **INSUFFICIENT** — 98 settled forecasts (< 100)
- passing_yards: **INSUFFICIENT** — 98 settled forecasts (< 100)
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **WATCH** — CRPS 110% of historical holdout
