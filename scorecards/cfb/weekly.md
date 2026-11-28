## CFB projection scorecard — last 28 days

Generated 2026-11-28T20:39:10Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 52471 across 381 games. Settled: 19848 (void/DNP 156, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1820 (230) | 4.139 | +0.204 | 2.879 | 77% | — |
| game_total | pf-cfb (challenger) | 230 (230) | 13.016 | +0.505 | 8.999 | 81% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 228 (228) | 13.063 | +0.729 | — | — |  |
| margin | pf-cfb (challenger) | 230 (230) | 12.769 | +3.305 | 9.083 | 80% | — |
| margin | script-engine-scoring-baseline (incumbent) | 228 (228) | 13.145 | +3.383 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 406 (225) | 7.073 | +0.771 | 5.075 | 84% | — |
| passing_yards | pf-cfb (challenger) | 406 (225) | 61.842 | +8.410 | 44.252 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 3787 (230) | 18.297 | +0.674 | 12.240 | 82% | — |
| receptions | pf-cfb (challenger) | 3787 (230) | 1.286 | +0.039 | 0.863 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1820 (230) | 25.836 | +1.104 | 17.616 | 80% | — |
| team_points | pf-cfb (challenger) | 460 (230) | 9.089 | +0.252 | 6.402 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 456 (228) | 9.230 | +0.365 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +3.305 (95% CI +1.366..+5.276) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
