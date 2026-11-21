## CFB projection scorecard — last 7 days

Generated 2026-11-21T19:50:03Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 43782 across 312 games. Settled: 15542 (void/DNP 130, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 401 (51) | 4.124 | +0.186 | 2.857 | 77% | — |
| game_total | pf-cfb (challenger) | 53 (53) | 12.907 | +2.581 | 8.619 | 87% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 51 (51) | 12.437 | +3.065 | — | — |  |
| margin | pf-cfb (challenger) | 53 (53) | 12.952 | +0.965 | 9.302 | 79% | — |
| margin | script-engine-scoring-baseline (incumbent) | 51 (51) | 14.114 | +0.141 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 91 (52) | 6.475 | +1.977 | 4.595 | 88% | — |
| passing_yards | pf-cfb (challenger) | 94 (53) | 58.956 | +30.330 | 41.268 | 91% | — |
| receiving_yards | pf-cfb (challenger) | 855 (52) | 16.468 | +3.563 | 10.668 | 82% | — |
| receptions | pf-cfb (challenger) | 763 (51) | 1.233 | +0.179 | 0.810 | 82% | — |
| rushing_yards | pf-cfb (challenger) | 420 (53) | 27.392 | -0.066 | 18.711 | 78% | — |
| team_points | pf-cfb (challenger) | 106 (53) | 9.154 | +1.291 | 6.365 | 78% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 103 (52) | 9.425 | +1.550 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 53 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 53 settled forecasts (< 100)
- passing_attempts: **INSUFFICIENT** — 91 settled forecasts (< 100)
- passing_yards: **INSUFFICIENT** — 94 settled forecasts (< 100)
- receiving_yards: **ALERT** — bias +3.563 (95% CI +1.999..+5.017) exceeds 5% of the mean
- receptions: **ALERT** — bias +0.179 (95% CI +0.065..+0.281) exceeds 5% of the mean
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
