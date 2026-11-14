## CFB projection scorecard — last 28 days

Generated 2026-11-14T00:23:39Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 33289 across 242 games. Settled: 11108 (void/DNP 80, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1444 (183) | 4.155 | +0.200 | 2.906 | 79% | — |
| game_total | pf-cfb (challenger) | 183 (183) | 13.747 | -1.444 | 9.655 | 78% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 183 (183) | 14.171 | -1.268 | — | — |  |
| margin | pf-cfb (challenger) | 183 (183) | 12.729 | +2.876 | 9.063 | 78% | — |
| margin | script-engine-scoring-baseline (incumbent) | 183 (183) | 12.972 | +3.614 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 314 (178) | 7.533 | -0.015 | 5.293 | 82% | — |
| passing_yards | pf-cfb (challenger) | 314 (178) | 63.354 | +2.726 | 45.304 | 85% | — |
| receiving_yards | pf-cfb (challenger) | 3064 (183) | 18.449 | +0.177 | 12.374 | 81% | — |
| receptions | pf-cfb (challenger) | 3064 (183) | 1.314 | -0.001 | 0.881 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1444 (183) | 25.741 | +1.230 | 17.654 | 80% | — |
| team_points | pf-cfb (challenger) | 366 (183) | 9.310 | -0.722 | 6.610 | 80% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 366 (183) | 9.407 | -0.636 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.876 (95% CI +0.359..+4.943) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
