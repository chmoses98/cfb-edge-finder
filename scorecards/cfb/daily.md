## CFB projection scorecard — last 7 days

Generated 2026-11-06T00:19:05Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 21407 across 175 games. Settled: 6844 (void/DNP 50, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 393 (50) | 4.083 | +0.309 | 2.875 | 81% | — |
| game_total | pf-cfb (challenger) | 50 (50) | 11.648 | +1.103 | 8.356 | 82% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 50 (50) | 12.214 | +1.174 | — | — |  |
| margin | pf-cfb (challenger) | 50 (50) | 11.997 | -1.021 | 8.640 | 80% | — |
| margin | script-engine-scoring-baseline (incumbent) | 50 (50) | 12.376 | -0.748 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 89 (50) | 6.800 | -0.180 | 4.743 | 88% | — |
| passing_yards | pf-cfb (challenger) | 89 (50) | 60.261 | +4.565 | 42.308 | 89% | — |
| receiving_yards | pf-cfb (challenger) | 837 (50) | 18.359 | +0.731 | 12.284 | 82% | — |
| receptions | pf-cfb (challenger) | 837 (50) | 1.293 | +0.050 | 0.868 | 83% | — |
| rushing_yards | pf-cfb (challenger) | 393 (50) | 24.376 | +3.099 | 16.592 | 84% | — |
| team_points | pf-cfb (challenger) | 100 (50) | 8.849 | +0.552 | 6.104 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 100 (50) | 8.803 | +0.587 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 50 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 50 settled forecasts (< 100)
- passing_attempts: **INSUFFICIENT** — 89 settled forecasts (< 100)
- passing_yards: **INSUFFICIENT** — 89 settled forecasts (< 100)
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **ALERT** — bias +3.099 (95% CI +0.208..+6.266) exceeds 5% of the mean
- team_points: **OK** — within expectations
