## CFB projection scorecard — last 7 days

Generated 2026-11-06T23:48:41Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 21463 across 175 games. Settled: 6844 (void/DNP 50, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 374 (48) | 4.062 | +0.366 | 2.856 | 82% | — |
| game_total | pf-cfb (challenger) | 48 (48) | 11.613 | +1.537 | 8.319 | 83% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 48 (48) | 12.277 | +1.669 | — | — |  |
| margin | pf-cfb (challenger) | 48 (48) | 11.649 | -1.726 | 8.342 | 81% | — |
| margin | script-engine-scoring-baseline (incumbent) | 48 (48) | 12.067 | -1.604 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 85 (48) | 6.625 | -0.145 | 4.657 | 87% | — |
| passing_yards | pf-cfb (challenger) | 85 (48) | 58.793 | +6.026 | 41.596 | 89% | — |
| receiving_yards | pf-cfb (challenger) | 802 (48) | 18.526 | +0.988 | 12.302 | 82% | — |
| receptions | pf-cfb (challenger) | 802 (48) | 1.299 | +0.064 | 0.869 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 374 (48) | 24.305 | +3.570 | 16.501 | 84% | — |
| team_points | pf-cfb (challenger) | 96 (48) | 8.613 | +0.769 | 5.957 | 82% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 96 (48) | 8.543 | +0.834 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **INSUFFICIENT** — 48 settled forecasts (< 100)
- margin: **INSUFFICIENT** — 48 settled forecasts (< 100)
- passing_attempts: **INSUFFICIENT** — 85 settled forecasts (< 100)
- passing_yards: **INSUFFICIENT** — 85 settled forecasts (< 100)
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **ALERT** — bias +3.570 (95% CI +0.303..+6.888) exceeds 5% of the mean
- team_points: **INSUFFICIENT** — 96 settled forecasts (< 100)
