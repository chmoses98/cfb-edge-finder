## CFB projection scorecard — last 28 days

Generated 2026-11-27T01:46:21Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 50164 across 381 games. Settled: 19060 (void/DNP 152, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 1991 (252) | 4.097 | +0.213 | 2.855 | 78% | — |
| game_total | pf-cfb (challenger) | 252 (252) | 12.459 | +0.731 | 8.657 | 84% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 250 (250) | 12.642 | +0.945 | — | — |  |
| margin | pf-cfb (challenger) | 252 (252) | 12.801 | +2.985 | 9.144 | 79% | — |
| margin | script-engine-scoring-baseline (incumbent) | 250 (250) | 13.195 | +3.086 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 445 (247) | 7.036 | +0.781 | 5.035 | 84% | — |
| passing_yards | pf-cfb (challenger) | 445 (247) | 61.408 | +10.045 | 43.773 | 88% | — |
| receiving_yards | pf-cfb (challenger) | 4187 (252) | 18.140 | +0.962 | 12.095 | 82% | — |
| receptions | pf-cfb (challenger) | 4187 (252) | 1.284 | +0.057 | 0.859 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 1991 (252) | 25.696 | +1.067 | 17.567 | 80% | — |
| team_points | pf-cfb (challenger) | 504 (252) | 8.945 | +0.365 | 6.304 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 500 (250) | 9.093 | +0.472 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.985 (95% CI +0.963..+5.024) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **ALERT** — bias +10.045 (95% CI +2.936..+17.497) exceeds 5% of the mean
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
