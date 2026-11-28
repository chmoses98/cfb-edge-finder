## CFB projection scorecard — season to date

Generated 2026-11-28T01:43:43Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 51982 across 381 games. Settled: 19196 (void/DNP 152, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 2492 (315) | 4.121 | +0.190 | 2.871 | 78% | — |
| game_total | pf-cfb (challenger) | 325 (325) | 13.063 | +0.151 | 9.093 | 81% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 323 (323) | 13.271 | +0.287 | — | — |  |
| margin | pf-cfb (challenger) | 325 (325) | 12.571 | +2.492 | 8.942 | 79% | — |
| margin | script-engine-scoring-baseline (incumbent) | 323 (323) | 12.796 | +2.734 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 551 (309) | 7.180 | +0.519 | 5.130 | 84% | — |
| passing_yards | pf-cfb (challenger) | 551 (309) | 62.684 | +7.736 | 44.708 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 5259 (315) | 18.171 | +0.787 | 12.139 | 82% | — |
| receptions | pf-cfb (challenger) | 5259 (315) | 1.293 | +0.039 | 0.865 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 2492 (315) | 25.877 | +0.894 | 17.720 | 80% | — |
| team_points | pf-cfb (challenger) | 650 (325) | 9.047 | +0.075 | 6.379 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 646 (323) | 9.160 | +0.143 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.492 (95% CI +0.641..+4.320) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
