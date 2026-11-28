## CFB projection scorecard — last 7 days

Generated 2026-11-28T20:39:10Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 52471 across 381 games. Settled: 19848 (void/DNP 156, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 309 (39) | 4.059 | +0.359 | 2.812 | 80% | — |
| game_total | pf-cfb (challenger) | 39 (39) | 11.695 | +1.541 | 8.318 | 79% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 39 (39) | 12.146 | +2.197 | — | — |  |
| margin | pf-cfb (challenger) | 39 (39) | 10.431 | +2.352 | 7.677 | 87% | — |
| margin | script-engine-scoring-baseline (incumbent) | 39 (39) | 10.800 | +1.523 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 71 (39) | 6.308 | -0.053 | 4.715 | 83% | — |
| passing_yards | pf-cfb (challenger) | 71 (39) | 66.760 | -8.867 | 46.245 | 89% | — |
| receiving_yards | pf-cfb (challenger) | 625 (39) | 18.445 | -0.063 | 12.509 | 83% | — |
| receptions | pf-cfb (challenger) | 625 (39) | 1.266 | +0.064 | 0.853 | 85% | — |
| rushing_yards | pf-cfb (challenger) | 309 (39) | 23.172 | +4.836 | 15.311 | 85% | — |
| team_points | pf-cfb (challenger) | 78 (39) | 8.450 | +0.770 | 5.745 | 88% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 78 (39) | 8.412 | +1.106 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 39 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 39 settled forecasts (< 100)
- passing_attempts: **INSUFFICIENT** — 71 settled forecasts (< 100)
- passing_yards: **INSUFFICIENT** — 71 settled forecasts (< 100)
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **ALERT** — bias +4.836 (95% CI +2.181..+7.335) exceeds 5% of the mean
- team_points: **INSUFFICIENT** — 78 settled forecasts (< 100)
