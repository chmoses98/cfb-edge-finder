## CFB projection scorecard — season to date

Generated 2026-11-28T20:39:10Z. Research only; nothing here is a betting signal.

Archived pregame forecasts: 52471 across 381 games. Settled: 19848 (void/DNP 156, stat corrections 0).

| Stat | Model | n (games) | MAE | Bias | CRPS | 80% range caught | vs incumbent (CRPS) |
|---|---|---|---|---|---|---|---|
| carries | pf-cfb (challenger) | 2594 (328) | 4.146 | +0.198 | 2.891 | 78% | — |
| game_total | pf-cfb (challenger) | 328 (328) | 13.071 | +0.184 | 9.094 | 80% | — |
| game_total | script-engine-scoring-baseline (incumbent) | 326 (326) | 13.276 | +0.333 | — | — |  |
| margin | pf-cfb (challenger) | 328 (328) | 12.505 | +2.434 | 8.906 | 79% | — |
| margin | script-engine-scoring-baseline (incumbent) | 326 (326) | 12.739 | +2.666 | — | — |  |
| passing_attempts | pf-cfb (challenger) | 574 (322) | 7.159 | +0.502 | 5.118 | 83% | — |
| passing_yards | pf-cfb (challenger) | 574 (322) | 62.700 | +7.240 | 44.702 | 87% | — |
| receiving_yards | pf-cfb (challenger) | 5448 (328) | 18.198 | +0.703 | 12.177 | 82% | — |
| receptions | pf-cfb (challenger) | 5448 (328) | 1.291 | +0.035 | 0.864 | 81% | — |
| rushing_yards | pf-cfb (challenger) | 2594 (328) | 25.859 | +1.165 | 17.673 | 80% | — |
| team_points | pf-cfb (challenger) | 656 (328) | 9.041 | +0.092 | 6.368 | 81% | — |
| team_points | script-engine-scoring-baseline (incumbent) | 652 (326) | 9.142 | +0.166 | — | — |  |

**Drift checks**

- carries: **OK** — within expectations
- game_total: **OK** — within expectations
- margin: **ALERT** — bias +2.434 (95% CI +0.543..+4.150) exceeds 5% of the mean
- passing_attempts: **OK** — within expectations
- passing_yards: **OK** — within expectations
- receiving_yards: **OK** — within expectations
- receptions: **OK** — within expectations
- rushing_yards: **OK** — within expectations
- team_points: **OK** — within expectations
